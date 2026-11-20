"""Keep the fan translation's credit line in the Diablo III update aligned with this port.

The console build of Diablo III that ships a Chinese translation carries that translation
team's credit as one of the pad hint strings of ``StringList\\Console.stl``, inside
``CPKs/enUS_Patch2_7_7.cpk``.  Because it lives in the same string list as "{icon:button1}
Continue", the game draws it on screen every session.

The port reads the update folder in place (game_update.layered), so the string is fixed where
it is stored - once - and then only kept aligned: a game update replaces the archive, this
module patches it again on the next start.  It is idempotent, and it leaves the archive header
untouched:

* the entry keeps its byte length, so the .stl's per-string length, the file sizes and every
  offset in the archive stay valid;
* only the 16 KiB sector holding the string is rewritten, re-emitted as a single chunk and
  padded inside its fixed slot.

Nothing here is a patch to the game's code: the dumps keep their layout, one entry's bytes
change.  ``SANCTUARY_CREDIT_ALIGN=0`` turns the whole thing off.
"""
from __future__ import annotations

from pathlib import Path
import struct
import zlib

import d3_cpk

ENTRY = r'StringList\Console.stl'
ARCHIVE = ('CPKs', 'enUS_Patch2_7_7.cpk')
OLD = 'GKinto.com 游戏大桶 汉化制作'
NEW = 'bbs.GBTGame.me 海量游戏尽在GBT'
CHANGEINFO = ('sce_sys', 'changeinfo', 'changeinfo.xml')
LINE = '\t- bbs.GBTGame.me 海量游戏 尽在GBTGame'
ANCHOR = '    - Various bug fixes and improvements'


def _child(folder: Path, name: str):
    """The entry of `folder` whose name matches `name` without regard to case, or None."""
    try:
        for entry in Path(folder).iterdir():
            if entry.name.casefold() == name.casefold():
                return entry
    except OSError:
        return None
    return None


def archive_paths(*roots):
    """Every update cpk holding the string list under any of `roots`, in the order found.

    A dump may carry the update twice - ``<game>/Update/CPKs`` inside the base folder and
    ``<game>-UPDATE/Update/CPKs`` in the separate update folder - and they can be different
    builds, so all of them are aligned rather than guessing which one the runtime reads.
    """
    found = []
    for root in roots:
        if not root:
            continue
        for relative in (('CPKs',), ('Update', 'CPKs')):
            folder = Path(root)
            for part in relative:
                folder = _child(folder, part)
                if folder is None or not folder.is_dir():
                    folder = None
                    break
            if folder is None:
                continue
            candidate = _child(folder, ARCHIVE[-1])
            if candidate is None or not candidate.is_file():
                continue
            if all(candidate.resolve() != seen.resolve() for seen in found):
                found.append(candidate)
    return found


def archive_path(*roots):
    """The first update cpk found under `roots`, or None."""
    found = archive_paths(*roots)
    return found[0] if found else None


def _sectors(archive, loc, size):
    """The sector indexes covering [loc, loc + size) - the same walk the reader does."""
    sector = archive.ds2cs[loc // archive.sector_size]
    while sector > 0 and archive.cs2do[sector] > loc:
        sector -= 1
    out = []
    while archive.cs2do[sector] < loc + size:
        out.append(sector)
        sector += 1
        if sector >= len(archive.cs2do):
            break
    return out


def _store_sector(path, archive, sector, payload):
    """Stores `payload` as the sector's single chunk, padding its fixed slot with zeros."""
    if len(payload) >= 0x10000:
        raise ValueError('sector payload too large for one chunk')
    blob = zlib.compress(payload, 9)
    if 6 + len(blob) > archive.sector_size:
        raise ValueError(f'sector {sector}: {len(blob)} compressed bytes do not fit the slot')
    slot = bytearray(archive.sector_size)
    slot[:6] = struct.pack('<3H', len(payload) & 0xFFFF, len(payload) >> 16, len(blob))
    slot[6:6 + len(blob)] = blob
    with open(path, 'r+b') as stream:
        stream.seek(archive.first + sector * archive.sector_size)
        stream.write(slot)


def patch_archive(path, entry: str, old: str, new: str, backup=False) -> str:
    """Rewrites `old` to `new` in `entry` of `path`; returns what it did. Raises on a mismatch."""
    old_bytes, new_bytes = old.encode('utf-8'), new.encode('utf-8')
    if len(new_bytes) != len(old_bytes):
        raise ValueError(f'the replacement must be {len(old_bytes)} bytes, '
                         f'not {len(new_bytes)}: the .stl stores a per-string length')
    with d3_cpk.Archive(path) as archive:
        if entry not in archive.names:
            return 'missing'
        expected, size, index = archive.entries[archive.names[entry]]
        if expected != d3_cpk.fnv1a64(entry):
            raise ValueError(f'{entry}: name hash mismatch')
        loc = archive.locations[index]
        data = archive.read(entry)
        at = data.find(old_bytes)
        if at < 0:
            return 'aligned' if new_bytes in data else 'absent'
        if data.find(old_bytes, at + 1) >= 0:
            raise ValueError(f'{entry}: the string occurs more than once')
        patched = bytearray(data)
        patched[at:at + len(old_bytes)] = new_bytes
        touched = []
        for sector in _sectors(archive, loc, size):
            base = archive.cs2do[sector]
            block = bytearray(archive._sector(sector))
            lo, hi = max(loc + at, base), min(loc + at + len(old_bytes), base + len(block))
            if lo < hi:
                block[lo - base:hi - base] = patched[lo - loc:hi - loc]
                touched.append((sector, bytes(block)))
        if backup:
            with open(str(path) + '.gbt-sector-bak', 'wb') as stream, open(path, 'rb') as source:
                for sector, _ in touched:
                    source.seek(archive.first + sector * archive.sector_size)
                    stream.write(source.read(archive.sector_size))
        for sector, block in touched:
            _store_sector(path, archive, sector, block)
    return 'patched'


def align_changeinfo(update, line: str = LINE) -> bool:
    """Adds `line` to the update's 01.47 notes when it is missing; True when it wrote."""
    folder = Path(update)
    for part in CHANGEINFO[:-1]:
        folder = _child(folder, part)
        if folder is None or not folder.is_dir():
            return False
    path = _child(folder, CHANGEINFO[-1])
    if path is None or not path.is_file():
        return False
    raw = path.read_bytes()
    text = raw.decode('utf-8', 'replace')
    if 'GBTGame' in text:
        return False
    at = text.find(ANCHOR)
    if at < 0:
        return False
    end = text.find('\n', at)
    if end < 0:
        return False
    newline = '\r\n' if '\r\n' in text else '\n'
    text = text[:end + 1] + line + newline + text[end + 1:]
    path.write_bytes(text.encode('utf-8'))
    return True


def align(*roots, log=None):
    """Patches the credit string and the update's notes wherever they are not aligned yet.

    Returns a one line description of what changed, or None when there was nothing to do.
    A root without the translated archive is simply skipped, so callers can pass both the
    game folder and the update folder.
    """
    done = []
    for path in archive_paths(*roots):
        state = patch_archive(path, ENTRY, OLD, NEW)
        if state == 'patched':
            done.append(f'credit line replaced in {path.parent.parent.name}/{path.name}')
    for root in roots:
        if root and align_changeinfo(root):
            done.append('notes line added to ' + Path(*CHANGEINFO).name)
    if done and log is not None:
        log('Credit: ' + '; '.join(done))
    return '; '.join(done) if done else None
