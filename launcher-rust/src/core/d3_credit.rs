//! The translation team's credit line, put back in Diablo III's own archives by the launcher.
//!
//! The Chinese build of the game carries that credit as one of the pad hint strings of
//! `StringList\Console.stl`, beside `{icon:button1} Continue`, so the game draws it on screen
//! every session. It lives in `CPKs/enUS_Patch2_7_7.cpk`, in Blizzard's own version 7 archive
//! format - the same one `scripts/d3_cpk.py` reads for the port (layout after
//! github.com/RedMadKnight/D3CPKUnpack: a 72-byte header, bit-packed tables, 16 KiB compressed
//! sectors, then one u32 name offset per file and the names).
//!
//! Doing it here rather than in `run.py` is deliberate: a localized release ships a launcher
//! alone, and the Python shell on an installed copy can be an older build - asking it for a
//! function it does not have is what broke v0.6.7-zh3. The launcher now carries the fix itself.
//!
//! Nothing is guessed: the entry is found by name (checked against the archive's own FNV-1a
//! hash), the replacement must keep the entry's byte length because the .stl stores a
//! per-string length and the next string follows immediately, and only the 16 KiB sector that
//! holds the string is rewritten, so every table in the archive header stays valid.

use std::collections::HashMap;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use flate2::Compression;

/// The string list holding the credit, inside the archive.
pub const ENTRY: &str = r"StringList\Console.stl";
/// The archive that carries it, under `CPKs/` or `Update/CPKs/` of a dump.
pub const ARCHIVE_NAME: &str = "enUS_Patch2_7_7.cpk";
/// What the translation team wrote there.
pub const OLD: &str = "GKinto.com 游戏大桶 汉化制作";
/// What this port puts in its place: the same byte length, 36.
pub const NEW: &str = "bbs.GBTGame.me 海量游戏尽在GBT";
/// The update notes file, from the dump root.
pub const CHANGEINFO: [&str; 3] = ["sce_sys", "changeinfo", "changeinfo.xml"];
/// The line added to those notes, as the branch writes it elsewhere.
pub const LINE: &str = "\t- bbs.GBTGame.me 海量游戏尽在GBT";
const ANCHOR: &str = "    - Various bug fixes and improvements";

const MAGIC: u32 = 0xA1B2_C3D4;
const VERSION: u32 = 7;
const HEADER_BYTES: usize = 72;

#[derive(Debug)]
pub enum Error {
    Io(io::Error),
    /// Not a version 7 Diablo III archive.
    Format(String),
    /// The replacement would change the entry's length.
    Length { old: usize, new: usize },
    /// The string occurs more than once in the entry: no safe choice.
    Ambiguous(usize),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Io(error) => write!(f, "{error}"),
            Error::Format(what) => write!(f, "{what}"),
            Error::Length { old, new } => write!(
                f,
                "the replacement must be {old} bytes, not {new}: the string list stores its length"
            ),
            Error::Ambiguous(count) => write!(f, "the string occurs {count} times in the entry"),
        }
    }
}

impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        Error::Io(error)
    }
}

/// The 64-bit FNV-1a hash the archive keeps per file, over the lower-case name.
pub fn name_hash(name: &str) -> u64 {
    let mut hash: u64 = 0xCBF2_9CE4_8422_2325;
    for byte in name.to_lowercase().as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100_0000_01B3);
    }
    hash
}

/// `width` bits at `at`, most significant bit first. The tables pack fields wider than 64 bits
/// (a file entry is 64 hash + size + count + location bits), so the value is assembled as u128.
fn bits(data: &[u8], mut at: usize, width: usize) -> u128 {
    let mut value: u128 = 0;
    for _ in 0..width {
        let byte = u128::from(*data.get(at >> 3).unwrap_or(&0));
        value = (value << 1) | ((byte >> (7 - (at & 7))) & 1);
        at += 1;
    }
    value
}

/// `count` fields of `width` bits from `data[start_bits..]`; returns them and the byte offset
/// just past the last one.
fn unpack(data: &[u8], start_bits: usize, count: usize, width: usize) -> (Vec<u128>, usize) {
    let mut at = start_bits;
    let mut out = Vec::with_capacity(count);
    for _ in 0..count {
        out.push(bits(data, at, width));
        at += width;
    }
    (out, (at + 7) >> 3)
}

fn mask(bits: usize) -> u128 {
    if bits >= 128 {
        u128::MAX
    } else {
        (1u128 << bits) - 1
    }
}

struct Entry {
    hash: u64,
    size: u64,
    location: usize,
}

/// A version 7 archive, opened for reading and for rewriting single sectors.
pub struct Archive {
    path: PathBuf,
    file: File,
    entries: Vec<Entry>,
    locations: Vec<u64>,
    cs2do: Vec<u64>,
    ds2cs: Vec<u64>,
    names: HashMap<String, usize>,
    first: u64,
    sector_size: u64,
}

impl Archive {
    pub fn open(path: &Path) -> Result<Archive, Error> {
        let mut file = File::open(path)?;
        let size = file.metadata()?.len();
        let mut head = [0u8; HEADER_BYTES];
        file.read_exact(&mut head)?;
        let magic = u32::from_le_bytes([head[0], head[1], head[2], head[3]]);
        let version = u32::from_le_bytes([head[4], head[5], head[6], head[7]]);
        if magic != MAGIC || version != VERSION {
            return Err(Error::Format(format!(
                "{}: not a version 7 Diablo III archive",
                path.display()
            )));
        }
        let word = |index: usize| -> u32 {
            let at = 8 + index * 4;
            u32::from_le_bytes([head[at], head[at + 1], head[at + 2], head[at + 3]])
        };
        let decomp_size = u64::from(word(0)) | u64::from(word(1)) << 32;
        let layout = word(2);
        let files = word(3) as usize;
        let locations = word(4) as usize;
        let header_sector = u64::from(word(5));
        let size_bits = word(6) as usize;
        let count_bits = word(7) as usize;
        let index_bits = word(8) as usize;
        let location_bits = word(9) as usize;
        let cs2do_bits = word(10) as usize;
        let ds2cs_bits = word(11) as usize;
        let read_sector = u64::from(word(13));
        let sector_size = u64::from(word(14));
        if sector_size == 0 {
            return Err(Error::Format(format!("{}: no sector size", path.display())));
        }
        let first = if layout == 10 { header_sector } else { header_sector * read_sector };
        if first > size {
            return Err(Error::Format(format!("{}: truncated header", path.display())));
        }
        let mut table = vec![0u8; first as usize];
        file.seek(SeekFrom::Start(0))?;
        file.read_exact(&mut table)?;

        let entry_bits = 64 + size_bits + count_bits + index_bits;
        let (values, mut pos) = unpack(&table, HEADER_BYTES * 8, files, entry_bits);
        let mut entries = Vec::with_capacity(files);
        for value in values {
            let location = (value & mask(index_bits)) as usize;
            let rest = value >> (index_bits + count_bits);
            entries.push(Entry {
                hash: (rest >> size_bits) as u64,
                size: (rest & mask(size_bits)) as u64,
                location,
            });
        }
        let (raw, next) = unpack(&table, pos * 8, locations, location_bits);
        pos = next;
        let locations: Vec<u64> = raw.into_iter().map(|value| value as u64).collect();
        let sectors = (size - first) / sector_size;
        let (raw, next) = unpack(&table, pos * 8, sectors as usize, cs2do_bits);
        pos = next;
        let cs2do: Vec<u64> = raw.into_iter().map(|value| value as u64).collect();
        let decomp_sectors = (decomp_size.div_ceil(sector_size)) as usize;
        let (raw, next) = unpack(&table, pos * 8, decomp_sectors, ds2cs_bits);
        pos = next;
        let ds2cs: Vec<u64> = raw.into_iter().map(|value| value as u64).collect();

        let mut names = HashMap::with_capacity(files);
        let mut offsets = Vec::with_capacity(files);
        for index in 0..files {
            let at = pos + index * 4;
            if at + 4 > table.len() {
                return Err(Error::Format(format!("{}: truncated name table", path.display())));
            }
            offsets.push(u32::from_le_bytes([
                table[at],
                table[at + 1],
                table[at + 2],
                table[at + 3],
            ]) as usize);
        }
        let base = pos + files * 4;
        for (index, offset) in offsets.into_iter().enumerate() {
            let start = base + offset;
            if start >= table.len() {
                continue;
            }
            let end = table[start..]
                .iter()
                .position(|byte| *byte == 0)
                .map(|len| start + len)
                .unwrap_or(table.len());
            let name: String = table[start..end].iter().map(|byte| char::from(*byte)).collect();
            names.insert(name, index);
        }
        Ok(Archive {
            path: path.to_path_buf(),
            file,
            entries,
            locations,
            cs2do,
            ds2cs,
            names,
            first,
            sector_size,
        })
    }

    pub fn has(&self, name: &str) -> bool {
        self.names.contains_key(name)
    }

    /// The decompressed bytes of `name`, checking the archive's own hash for it.
    pub fn read(&mut self, name: &str) -> Result<Vec<u8>, Error> {
        let index = *self
            .names
            .get(name)
            .ok_or_else(|| Error::Format(format!("{name}: not in {}", self.path.display())))?;
        let entry = &self.entries[index];
        if entry.hash != name_hash(name) {
            return Err(Error::Format(format!("{name}: hash mismatch")));
        }
        let entry = Entry { hash: entry.hash, size: entry.size, location: entry.location };
        self.read_range(self.locations[entry.location], entry.size)
    }

    fn read_range(&mut self, offset: u64, size: u64) -> Result<Vec<u8>, Error> {
        let mut sector = self.ds2cs[(offset / self.sector_size) as usize];
        while sector > 0 && self.cs2do[sector as usize] > offset {
            sector -= 1;
        }
        let start = self.cs2do[sector as usize];
        let mut buffer: Vec<u8> = Vec::with_capacity(size as usize + self.sector_size as usize);
        let mut current = sector;
        while start + buffer.len() as u64 <= offset + size {
            buffer.extend_from_slice(&self.sector(current)?);
            current += 1;
            if (current as usize) < self.cs2do.len() {
                let next = self.cs2do[current as usize];
                let held = start + buffer.len() as u64;
                if next > held {
                    buffer.resize(buffer.len() + (next - held) as usize, 0);
                } else if next < held {
                    buffer.truncate(buffer.len() - (held - next) as usize);
                }
            }
        }
        let from = (offset - start) as usize;
        let to = from + size as usize;
        Ok(buffer[from..to].to_vec())
    }

    /// One sector, its zlib chunks inflated and laid end to end.
    pub fn sector(&mut self, index: u64) -> Result<Vec<u8>, Error> {
        let mut slot = vec![0u8; self.sector_size as usize];
        self.file.seek(SeekFrom::Start(self.first + index * self.sector_size))?;
        self.file.read_exact(&mut slot)?;
        let mut out = Vec::new();
        let mut at = 0usize;
        while at + 6 <= slot.len() {
            let low = u16::from_le_bytes([slot[at], slot[at + 1]]) as usize;
            let high = u16::from_le_bytes([slot[at + 2], slot[at + 3]]) as usize;
            let stored = u16::from_le_bytes([slot[at + 4], slot[at + 5]]) as usize;
            if stored == 0 {
                break;
            }
            if stored & 0x8000 != 0 {
                return Err(Error::Format(format!("sector {index}: encrypted chunk")));
            }
            let size = low | (high << 16);
            let taken = stored & 0x7FFF;
            let end = (at + 6 + taken).min(slot.len());
            let mut inflated = Vec::with_capacity(size);
            ZlibDecoder::new(&slot[at + 6..end]).read_to_end(&mut inflated)?;
            if size > 0 {
                inflated.resize(size, 0);
            }
            out.extend_from_slice(&inflated);
            at += 6 + taken;
        }
        Ok(out)
    }

    /// Stores `payload` as the sector's only chunk, padding the fixed slot with zeros.
    fn store_sector(&mut self, index: u64, payload: &[u8]) -> Result<(), Error> {
        if payload.len() >= 0x10000 {
            return Err(Error::Format(format!("sector {index}: payload too large")));
        }
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::best());
        encoder.write_all(payload)?;
        let blob = encoder.finish()?;
        if blob.len() >= 0x8000 || 6 + blob.len() > self.sector_size as usize {
            return Err(Error::Format(format!("sector {index}: compressed data does not fit")));
        }
        let mut slot = vec![0u8; self.sector_size as usize];
        slot[0..2].copy_from_slice(&(payload.len() as u16).to_le_bytes());
        slot[2..4].copy_from_slice(&((payload.len() >> 16) as u16).to_le_bytes());
        slot[4..6].copy_from_slice(&(blob.len() as u16).to_le_bytes());
        slot[6..6 + blob.len()].copy_from_slice(&blob);
        let mut file = OpenOptions::new().write(true).open(&self.path)?;
        file.seek(SeekFrom::Start(self.first + index * self.sector_size))?;
        file.write_all(&slot)?;
        file.flush()?;
        Ok(())
    }

    fn sectors_of(&self, offset: u64, size: u64) -> Vec<u64> {
        let mut sector = self.ds2cs[(offset / self.sector_size) as usize];
        while sector > 0 && self.cs2do[sector as usize] > offset {
            sector -= 1;
        }
        let mut out = Vec::new();
        while (sector as usize) < self.cs2do.len()
            && self.cs2do[sector as usize] < offset + size
        {
            out.push(sector);
            sector += 1;
        }
        out
    }
}

/// What a patch did, so the caller can say it once and stay quiet afterwards.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    /// The old credit was there and is replaced.
    Patched,
    /// The port's line is already there.
    Aligned,
    /// Neither string: a build that does not carry the translated list.
    Absent,
}

/// Replaces `old` with `new` in `ENTRY` of `path`, rewriting only the sectors that hold it.
pub fn patch_archive(path: &Path, old: &str, new: &str) -> Result<Outcome, Error> {
    let (old, new) = (old.as_bytes(), new.as_bytes());
    if old.len() != new.len() {
        return Err(Error::Length { old: old.len(), new: new.len() });
    }
    let mut archive = Archive::open(path)?;
    if !archive.has(ENTRY) {
        return Ok(Outcome::Absent);
    }
    let data = archive.read(ENTRY)?;
    let Some(at) = data.windows(old.len()).position(|window| window == old) else {
        let aligned = data.windows(new.len()).any(|window| window == new);
        return Ok(if aligned { Outcome::Aligned } else { Outcome::Absent });
    };
    if data[at + 1..].windows(old.len()).any(|window| window == old) {
        return Err(Error::Ambiguous(2));
    }
    let mut patched = data.clone();
    patched[at..at + old.len()].copy_from_slice(new);

    let index = archive.names[ENTRY];
    let entry = &archive.entries[index];
    let (loc, size) = (archive.locations[entry.location], entry.size);
    let sectors = archive.sectors_of(loc, size);
    let mut blocks = Vec::with_capacity(sectors.len());
    for sector in sectors {
        let base = archive.cs2do[sector as usize];
        let mut block = archive.sector(sector)?;
        let from = (loc + at as u64).max(base);
        let to = (loc + at as u64 + old.len() as u64).min(base + block.len() as u64);
        if from < to {
            let src = ((from - loc) as usize)..((to - loc) as usize);
            let dst = ((from - base) as usize)..((to - base) as usize);
            block[dst].copy_from_slice(&patched[src]);
        }
        blocks.push((sector, block));
    }
    for (sector, block) in blocks {
        archive.store_sector(sector, &block)?;
    }
    Ok(Outcome::Patched)
}

/// The file of `folder` whose name matches `name` without regard to case, when it is a file.
pub fn child_file(folder: &Path, name: &str) -> Option<PathBuf> {
    let entries = fs::read_dir(folder).ok()?;
    for entry in entries.flatten() {
        if entry.file_name().to_string_lossy().eq_ignore_ascii_case(name)
            && entry.path().is_file()
        {
            return Some(entry.path());
        }
    }
    None
}

fn child_dir(folder: &Path, name: &str) -> Option<PathBuf> {
    let entries = fs::read_dir(folder).ok()?;
    for entry in entries.flatten() {
        if entry.file_name().to_string_lossy().eq_ignore_ascii_case(name)
            && entry.path().is_dir()
        {
            return Some(entry.path());
        }
    }
    None
}

/// Every update archive under `roots`: `<root>/CPKs/` first, then `<root>/Update/CPKs/`. A dump
/// can hold the update twice and they can be different builds, so all of them are aligned.
pub fn archives(roots: &[&Path]) -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = Vec::new();
    for root in roots {
        for relative in [&["CPKs"][..], &["Update", "CPKs"][..]] {
            let mut folder = root.to_path_buf();
            let mut reached = true;
            for part in relative {
                match child_dir(&folder, part) {
                    Some(inner) => folder = inner,
                    None => {
                        reached = false;
                        break;
                    }
                }
            }
            if !reached {
                continue;
            }
            let Some(archive) = child_file(&folder, ARCHIVE_NAME) else { continue };
            if !found.iter().any(|seen| seen == &archive) {
                found.push(archive);
            }
        }
    }
    found
}

/// Adds `LINE` to the update notes of `root` when it is missing; true when it wrote.
pub fn align_changeinfo(root: &Path) -> Result<bool, Error> {
    let mut folder = root.to_path_buf();
    for part in &CHANGEINFO[..2] {
        let Some(inner) = child_dir(&folder, part) else { return Ok(false) };
        folder = inner;
    }
    let Some(path) = child_file(&folder, CHANGEINFO[2]) else { return Ok(false) };
    let text = fs::read_to_string(&path)?;
    if text.contains("GBTGame") {
        return Ok(false);
    }
    let Some(anchor) = text.find(ANCHOR) else { return Ok(false) };
    let Some(end) = text[anchor..].find('\n').map(|at| anchor + at + 1) else {
        return Ok(false);
    };
    let newline = if text.contains("\r\n") { "\r\n" } else { "\n" };
    let mut out = String::with_capacity(text.len() + LINE.len() + 2);
    out.push_str(&text[..end]);
    out.push_str(LINE);
    out.push_str(newline);
    out.push_str(&text[end..]);
    fs::write(&path, out)?;
    Ok(true)
}

/// Aligns the credit and the notes of `game` and `update`; a one line summary when it changed
/// anything, `None` when there was nothing to do.
pub fn align(game: &Path, update: &Path) -> Option<String> {
    let mut done = Vec::new();
    for root in [game, update] {
        if root.as_os_str().is_empty() {
            continue;
        }
        for archive in archives(&[root]) {
            match patch_archive(&archive, OLD, NEW) {
                Ok(Outcome::Patched) => done.push(format!(
                    "credit line replaced in {}",
                    archive
                        .parent()
                        .and_then(|cpks| cpks.parent())
                        .map(|p| p.display().to_string())
                        .unwrap_or_else(|| archive.display().to_string())
                )),
                Ok(_) => {}
                Err(error) => done.push(format!("{}: {error}", archive.display())),
            }
        }
        if let Ok(true) = align_changeinfo(root) {
            done.push("notes line added".to_string());
        }
    }
    (!done.is_empty()).then(|| done.join("; "))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp() -> tempfile::TempDir {
        tempfile::tempdir().expect("a temporary folder")
    }

    #[test]
    fn the_name_hash_is_the_archive_one() {
        // The value `scripts/d3_cpk.fnv1a64` gives for the same name.
        assert_eq!(name_hash(ENTRY), 0xD290_0A84_ADD3_F3F6);
        assert_eq!(name_hash(ENTRY), name_hash(&ENTRY.to_lowercase()));
    }

    #[test]
    fn the_replacement_keeps_the_entry_length() {
        assert_eq!(OLD.len(), NEW.len());
        assert_eq!(NEW.len(), 36);
    }

    #[test]
    fn the_notes_line_goes_after_the_anchor() {
        let dir = temp();
        let notes = dir.path().join("sce_sys").join("changeinfo");
        fs::create_dir_all(&notes).unwrap();
        let path = notes.join("changeinfo.xml");
        fs::write(
            &path,
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\r\n<changeinfo>\r\n    FEATURE SUPPORT:\r\n \
             - Class Balance\r\n    - Various bug fixes and improvements\r\n     ]]>\r\n",
        )
        .unwrap();
        assert!(align_changeinfo(dir.path()).unwrap());
        let text = fs::read_to_string(&path).unwrap();
        assert!(text.contains(&format!("{ANCHOR}\r\n{LINE}\r\n")), "{text}");
        // Idempotent.
        assert!(!align_changeinfo(dir.path()).unwrap());
    }

    #[test]
    fn an_archive_is_found_without_regard_to_case() {
        let dir = temp();
        let cpks = dir.path().join("Update").join("cpks");
        fs::create_dir_all(&cpks).unwrap();
        fs::write(cpks.join("ENUS_Patch2_7_7.CPK"), b"").unwrap();
        let found = archives(&[dir.path()]);
        assert_eq!(found.len(), 1);
        assert!(found[0].ends_with("ENUS_Patch2_7_7.CPK"));
    }

    #[test]
    fn a_dump_without_the_archive_aligns_nothing() {
        let dir = temp();
        assert!(archives(&[dir.path()]).is_empty());
        assert_eq!(align(dir.path(), dir.path()), None);
    }
}
