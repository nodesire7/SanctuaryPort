/* Rewrites the translation team's credit inside the game's CPK archive, in the bytes the
 * process reads - the file on disk is never touched.
 *
 * The credit is a string in `StringList\Console.stl` (described in the archive's table of
 * contents). A version 7 CPK stores its files as 16 KiB sectors of zlib chunks, so the string is
 * not readable in the raw bytes: this module parses the archive the same way `D3` does
 * (launcher-rust/src/core/d3_credit.rs is the reference implementation), inflates the sectors
 * that hold the entry, replaces the string, and recompresses each sector into the fixed slot the
 * reader expects. The result is cached per archive and served by the ReadFile hook.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <string.h>

#include "cpk.h"

#define MAGIC 0xA1B2C3D4u
#define VERSION 7u
#define HEADER_BYTES 72
#define MAX_PATCHES 4

typedef struct {
    u64 index;
    unsigned char *slot; /* sector_size bytes, served instead of the file's own */
} patch_slot;

struct hu_archive {
    WCHAR path[MAX_PATH];
    u64 first;
    u64 sector_size;
    u64 *cs2do;
    size_t cs2do_count;
    u64 *ds2cs;
    size_t ds2cs_count;
    patch_slot patches[MAX_PATCHES];
    int patch_count;
};

/* ------------------------------------------------------------------- zlib */

typedef unsigned long z_size;
typedef int (*uncompress_t)(unsigned char *, z_size *, const unsigned char *, z_size);
typedef int (*compress2_t)(unsigned char *, z_size *, const unsigned char *, z_size, int);
typedef z_size (*compress_bound_t)(z_size);

static uncompress_t zlib_uncompress;
static compress2_t zlib_compress2;
static compress_bound_t zlib_bound;

static void load_zlib(void) {
    WCHAR path[MAX_PATH];
    HMODULE library;
    DWORD length;
    if (zlib_uncompress) return;
    GetModuleFileNameW((HMODULE)(void *)&load_zlib, path, MAX_PATH);
    length = lstrlenW(path);
    while (length > 0 && path[length] != L'\\') length--;
    if (length > 0 && path[length] == L'\\') {
        lstrcpynW(path + length + 1, L"zlib1.dll", MAX_PATH - (length + 1));
        library = LoadLibraryW(path);
    } else {
        library = NULL;
    }
    if (!library) library = LoadLibraryW(L"zlib1.dll");
    if (!library) return;
    zlib_uncompress = (uncompress_t)(void (*)(void))GetProcAddress(library, "uncompress");
    zlib_compress2 = (compress2_t)(void (*)(void))GetProcAddress(library, "compress2");
    zlib_bound = (compress_bound_t)(void (*)(void))GetProcAddress(library, "compressBound");
}


/* A trace for support calls: set HU_LOG=1 to have the hook write what it found and why it gave
 * up. Nothing is written otherwise. */
void hu_log(const char *format, const char *text) {
    char path[MAX_PATH];
    char line[1024];
    DWORD wrote = 0;
    HANDLE file;
    int length;
    if (GetEnvironmentVariableA("HU_LOG", path, MAX_PATH) == 0) return;
    length = wsprintfA(line, format, text ? text : "");
    GetTempPathA(MAX_PATH, path);
    lstrcatA(path, "sanctuary-hook.log");
    file = CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ, NULL, OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return;
    WriteFile(file, line, (DWORD)length, &wrote, NULL);
    WriteFile(file, "\r\n", 2, &wrote, NULL);
    CloseHandle(file);
}
/* -------------------------------------------------------------- bit tables */

static unsigned __int128 parts(const unsigned char *data, size_t bytes, size_t at, unsigned width) {
    unsigned __int128 value = 0;
    unsigned bit;
    for (bit = 0; bit < width; bit++, at++) {
        unsigned char byte = (size_t)(at >> 3) < bytes ? data[at >> 3] : 0;
        value = (value << 1) | ((byte >> (7 - (at & 7))) & 1);
    }
    return value;
}

/* `count` fields of `width` bits; returns the byte offset just past the last field. */
static size_t unpack(const unsigned char *data, size_t bytes, size_t at_bits, size_t count,
                     unsigned width, unsigned __int128 *out) {
    size_t index;
    for (index = 0; index < count; index++) {
        out[index] = parts(data, bytes, at_bits, width);
        at_bits += width;
    }
    return (at_bits + 7) >> 3;
}

static unsigned __int128 low_mask(unsigned width) {
    if (width >= 128) return ~(unsigned __int128)0;
    return (((unsigned __int128)1) << width) - 1;
}

/* ------------------------------------------------------------------ reader */

static u32 word_at(const unsigned char *head, int index) {
    size_t at = 8 + (size_t)index * 4;
    return (u32)head[at] | ((u32)head[at + 1] << 8) | ((u32)head[at + 2] << 16) |
           ((u32)head[at + 3] << 24);
}

static HANDLE open_file(const WCHAR *path) {
    return CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL, NULL);
}

static BOOL read_at(HANDLE file, u64 offset, void *buffer, DWORD bytes) {
    LARGE_INTEGER where;
    DWORD got = 0;
    where.QuadPart = (LONGLONG)offset;
    if (!SetFilePointerEx(file, where, NULL, FILE_BEGIN)) return FALSE;
    return ReadFile(file, buffer, bytes, &got, NULL) && got == bytes;
}

/* ------------------------------------------------------------------ sector */

/* One sector: its chunks inflated and laid end to end. */
static unsigned char *inflate_sector(HANDLE file, const struct hu_archive *archive, u64 index,
                                     size_t *out_size) {
    unsigned char *slot;
    unsigned char *out;
    size_t at = 0, used = 0;
    u64 start = archive->first + index * archive->sector_size;

    if (archive->sector_size > 0x40000) return NULL;
    slot = HeapAlloc(GetProcessHeap(), 0, (SIZE_T)archive->sector_size);
    out = HeapAlloc(GetProcessHeap(), 0, (SIZE_T)archive->sector_size);
    if (!slot || !out || !read_at(file, start, slot, (DWORD)archive->sector_size)) {
        if (slot) HeapFree(GetProcessHeap(), 0, slot);
        if (out) HeapFree(GetProcessHeap(), 0, out);
        return NULL;
    }
    while (at + 6 <= archive->sector_size) {
        u32 low = (u32)slot[at] | ((u32)slot[at + 1] << 8);
        u32 high = (u32)slot[at + 2] | ((u32)slot[at + 3] << 8);
        u32 stored = (u32)slot[at + 4] | ((u32)slot[at + 5] << 8);
        u32 size, taken;
        z_size produced;
        if (stored == 0) break;
        if (stored & 0x8000) break; /* an encrypted chunk is not ours */
        size = low | (high << 16);
        taken = stored & 0x7FFF;
        if (at + 6 + taken > archive->sector_size) break;
        produced = (z_size)size;
        if (size > 0 && (!zlib_uncompress || zlib_uncompress(out + used, &produced,
                                                             slot + at + 6, taken) != 0)) {
            break;
        }
        used += size;
        at += 6 + taken;
        if (used >= archive->sector_size) break;
    }
    HeapFree(GetProcessHeap(), 0, slot);
    if (out_size) *out_size = used;
    if (GetEnvironmentVariableA("HU_DUMP", (char *)0, 0) > 0) {
        char path[MAX_PATH];
        DWORD wrote = 0;
        HANDLE dump;
        GetTempPathA(MAX_PATH, path);
        wsprintfA(path + lstrlenA(path), "sector-%lu.bin", (unsigned long)index);
        dump = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (dump != INVALID_HANDLE_VALUE) {
            WriteFile(dump, out, (DWORD)used, &wrote, NULL);
            CloseHandle(dump);
        }
    }
    return out;
}

/* The sector as one zlib chunk in a zero padded slot; NULL when it does not fit.
 * The bound is only the size of the scratch buffer: 16 KiB of text almost always fits in the
 * slot, but compressBound() alone already exceeds it, so the real length decides. */
static unsigned char *compress_sector(const unsigned char *payload, size_t size,
                                      u64 sector_size) {
    unsigned char *slot, *scratch;
    z_size bound, produced;
    if (!zlib_compress2 || !zlib_bound) return NULL;
    bound = zlib_bound((z_size)size);
    scratch = HeapAlloc(GetProcessHeap(), 0, (SIZE_T)bound);
    slot = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, (SIZE_T)sector_size);
    if (!scratch || !slot) {
        if (scratch) HeapFree(GetProcessHeap(), 0, scratch);
        if (slot) HeapFree(GetProcessHeap(), 0, slot);
        return NULL;
    }
    produced = bound;
    if (zlib_compress2(scratch, &produced, payload, (z_size)size, 9) != 0 ||
        produced >= 0x8000 || 6 + produced > sector_size) {
        HeapFree(GetProcessHeap(), 0, scratch);
        HeapFree(GetProcessHeap(), 0, slot);
        return NULL;
    }
    memcpy(slot + 6, scratch, (size_t)produced);
    HeapFree(GetProcessHeap(), 0, scratch);
    slot[0] = (unsigned char)(size & 0xFF);
    slot[1] = (unsigned char)((size >> 8) & 0xFF);
    slot[2] = (unsigned char)((size >> 16) & 0xFF);
    slot[3] = 0;
    slot[4] = (unsigned char)(produced & 0xFF);
    slot[5] = (unsigned char)((produced >> 8) & 0xFF);
    return slot;
}

/* --------------------------------------------------------------- patching */

/* Replaces `old` with `new` (same length) in the sectors that hold the entry, caching the
 * rebuilt sectors on the archive. */
static void patch_entry(struct hu_archive *archive, u64 offset, u64 size, const char *old_text,
                        const char *new_text, size_t text_length) {
    HANDLE file = open_file(archive->path);
    u64 sector;
    unsigned length = (unsigned)lstrlenA(new_text);

    if (file == INVALID_HANDLE_VALUE) return;
    if (archive->ds2cs_count == 0) { CloseHandle(file); return; }
    {
        static char note[256];
        wsprintfA(note, "patch: offset=%lu size=%lu first=%lu sector_size=%lu cs2do=%lu ds2cs=%lu",
                  (unsigned long)offset, (unsigned long)size, (unsigned long)archive->first,
                  (unsigned long)archive->sector_size, (unsigned long)archive->cs2do_count,
                  (unsigned long)archive->ds2cs_count);
        hu_log("%s", note);
    }
    sector = archive->ds2cs[offset / archive->sector_size];
    while (sector > 0 && sector < archive->cs2do_count && archive->cs2do[sector] > offset) {
        sector--;
    }
    while (sector < archive->cs2do_count && archive->cs2do[sector] < offset + size &&
           archive->patch_count < MAX_PATCHES) {
        size_t held = 0;
        unsigned char *data;
        {
            static char note[192];
            wsprintfA(note, "patch: sector %lu cs2do=%lu", (unsigned long)sector,
                      (unsigned long)archive->cs2do[sector]);
            hu_log("%s", note);
        }
        data = inflate_sector(file, archive, sector, &held);
        {
            static char note[192];
            wsprintfA(note, "patch: inflated %lu bytes", (unsigned long)held);
            hu_log("%s", note);
        }
        if (data) {
            size_t at, found = (size_t)-1;
            {
                static char note[256];
                wsprintfA(note, "patch: needle len=%lu bytes=%02x %02x %02x %02x ... held=%lu",
                          (unsigned long)text_length, (unsigned char)old_text[0],
                          (unsigned char)old_text[1], (unsigned char)old_text[2],
                          (unsigned char)old_text[3], (unsigned long)held);
                hu_log("%s", note);
            }
            for (at = 0; at + text_length <= held; at++) {
                if (memcmp(data + at, old_text, text_length) == 0) {
                    found = at;
                    break;
                }
            }

            if (found != (size_t)-1) {
                unsigned char *slot;
                memcpy(data + found, new_text, length);
                slot = compress_sector(data, held, archive->sector_size);
                if (slot) {
                    archive->patches[archive->patch_count].index = sector;
                    archive->patches[archive->patch_count].slot = slot;
                    archive->patch_count++;
                }
            }
            HeapFree(GetProcessHeap(), 0, data);
        }
        sector++;
    }
    CloseHandle(file);
}

/* ------------------------------------------------------------------- API */

/* The entry the credit lives in, and how it is found. */
#define ENTRY_NAME "StringList\\Console.stl"

hu_archive *hu_cpk_prepare(const WCHAR *path, const char *old_text, const char *new_text) {
    HANDLE file;
    unsigned char *table = NULL;
    unsigned char head[HEADER_BYTES];
    DWORD got = 0;
    u64 size, decomp_size, first, sector_size;
    u32 files, locations, header_sector, index_bits, location_bits, size_bits, count_bits,
        cs2do_bits, ds2cs_bits, layout;
    struct hu_archive *archive;
    unsigned __int128 *entries = NULL, *locations_values = NULL, *cs2do_values = NULL,
                    *ds2cs_values = NULL;
    size_t pos, index, entry_index = (size_t)-1, name_base, name_count;
    u32 *name_offsets = NULL;
    BOOL found = FALSE;

    load_zlib();
    hu_log("prepare: %s", "start");
    if (!zlib_uncompress || !zlib_compress2) { hu_log("prepare: %s", "no zlib"); return NULL; }

    file = open_file(path);
    if (file == INVALID_HANDLE_VALUE) return NULL;
    if (!ReadFile(file, head, HEADER_BYTES, &got, NULL) || got != HEADER_BYTES) goto done;
    if ((u32)(head[0] | (head[1] << 8) | (head[2] << 16) | ((u32)head[3] << 24)) != MAGIC ||
        (u32)(head[4] | (head[5] << 8) | (head[6] << 16) | ((u32)head[7] << 24)) != VERSION) {
        goto done;
    }
    decomp_size = (u64)word_at(head, 0) | ((u64)word_at(head, 1) << 32);
    layout = word_at(head, 2);
    files = word_at(head, 3);
    locations = word_at(head, 4);
    header_sector = word_at(head, 5);
    size_bits = word_at(head, 6);
    count_bits = word_at(head, 7);
    index_bits = word_at(head, 8);
    location_bits = word_at(head, 9);
    cs2do_bits = word_at(head, 10);
    ds2cs_bits = word_at(head, 11);
    {
        LARGE_INTEGER length;
        if (!GetFileSizeEx(file, &length)) goto done;
        size = (u64)length.QuadPart;
    }
    {
        u32 read_sector = word_at(head, 13);
        sector_size = word_at(head, 14);
        (void)read_sector;
    }
    hu_log("prepare: header %s", "read");
    if (sector_size == 0 || files == 0 || index_bits == 0) { hu_log("prepare: %s", "bad header"); goto done; }
    first = layout == 10 ? header_sector : (u64)header_sector * word_at(head, 13);
    if (first > size || first < HEADER_BYTES || first > (1u << 24)) goto done;

    table = HeapAlloc(GetProcessHeap(), 0, (SIZE_T)first);
    if (!table || !read_at(file, 0, table, (DWORD)first)) goto done;

    archive = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(*archive));
    if (!archive) goto done;
    archive->first = first;
    archive->sector_size = sector_size;
    lstrcpynW(archive->path, path, MAX_PATH);

    entries = HeapAlloc(GetProcessHeap(), 0, (SIZE_T)files * sizeof(*entries));
    if (!entries) goto free_archive;
    pos = unpack(table, (size_t)first, (size_t)HEADER_BYTES * 8, files,
                 64 + size_bits + count_bits + index_bits, entries);

    locations_values = HeapAlloc(GetProcessHeap(), 0, (SIZE_T)locations * sizeof(*locations_values));
    if (!locations_values) goto free_archive;
    pos = unpack(table, (size_t)first, pos * 8, locations, location_bits, locations_values);

    archive->cs2do_count = (size_t)((size - first) / sector_size);
    cs2do_values = HeapAlloc(GetProcessHeap(), 0,
                             archive->cs2do_count * sizeof(*cs2do_values));
    if (!cs2do_values) goto free_archive;
    pos = unpack(table, (size_t)first, pos * 8, archive->cs2do_count, cs2do_bits, cs2do_values);
    archive->cs2do = HeapAlloc(GetProcessHeap(), 0,
                               archive->cs2do_count * sizeof(u64));
    if (!archive->cs2do) goto free_archive;
    for (index = 0; index < archive->cs2do_count; index++) {
        archive->cs2do[index] = (u64)cs2do_values[index];
    }

    archive->ds2cs_count = (size_t)((decomp_size + sector_size - 1) / sector_size);
    ds2cs_values = HeapAlloc(GetProcessHeap(), 0,
                             archive->ds2cs_count * sizeof(*ds2cs_values));
    if (!ds2cs_values) goto free_archive;
    pos = unpack(table, (size_t)first, pos * 8, archive->ds2cs_count, ds2cs_bits, ds2cs_values);
    archive->ds2cs = HeapAlloc(GetProcessHeap(), 0, archive->ds2cs_count * sizeof(u64));
    if (!archive->ds2cs) goto free_archive;
    for (index = 0; index < archive->ds2cs_count; index++) {
        archive->ds2cs[index] = (u64)ds2cs_values[index];
    }

    /* The name table: u32 offsets from the byte after them, then NUL terminated names. */
    pos += 0; /* `pos` already points past the packed tables */
    name_base = pos + (size_t)files * 4;
    name_count = (size_t)files;
    name_offsets = HeapAlloc(GetProcessHeap(), 0, name_count * sizeof(u32));
    if (!name_offsets) goto free_archive;
    for (index = 0; index < name_count; index++) {
        size_t at = pos + index * 4;
        if (at + 4 > first) goto free_archive;
        name_offsets[index] = (u32)table[at] | ((u32)table[at + 1] << 8) |
                              ((u32)table[at + 2] << 16) | ((u32)table[at + 3] << 24);
    }
    for (index = 0; index < name_count; index++) {
        size_t start = name_base + name_offsets[index];
        if (start >= first) continue;
        if (lstrcmpA((LPCSTR)(table + start), ENTRY_NAME) == 0) {
            entry_index = index;
            break;
        }
    }
    if (entry_index == (size_t)-1) { hu_log("prepare: %s", "entry missing"); goto free_archive; }
    hu_log("prepare: entry %s", "found");

    {
        unsigned __int128 value = entries[entry_index];
        unsigned __int128 rest = value >> (index_bits + count_bits);
        u64 entry_size = (u64)(rest & low_mask(size_bits));
        size_t location = (size_t)(value & low_mask(index_bits));
        u64 offset = locations_values[location];
        patch_entry(archive, offset, entry_size, old_text, new_text,
                    (size_t)lstrlenA(old_text));
        found = archive->patch_count > 0;
        hu_log(found ? "prepare: %s" : "prepare: %s", found ? "patched" : "string not found");
    }

    if (name_offsets) HeapFree(GetProcessHeap(), 0, name_offsets);
    if (entries) HeapFree(GetProcessHeap(), 0, entries);
    if (locations_values) HeapFree(GetProcessHeap(), 0, locations_values);
    if (cs2do_values) HeapFree(GetProcessHeap(), 0, cs2do_values);
    if (ds2cs_values) HeapFree(GetProcessHeap(), 0, ds2cs_values);
    if (table) HeapFree(GetProcessHeap(), 0, table);
    CloseHandle(file);
    if (!found) {
        hu_cpk_free(archive);
        return NULL;
    }
    return archive;

free_archive:
    if (name_offsets) HeapFree(GetProcessHeap(), 0, name_offsets);
    if (entries) HeapFree(GetProcessHeap(), 0, entries);
    if (locations_values) HeapFree(GetProcessHeap(), 0, locations_values);
    if (cs2do_values) HeapFree(GetProcessHeap(), 0, cs2do_values);
    if (ds2cs_values) HeapFree(GetProcessHeap(), 0, ds2cs_values);
    hu_cpk_free(archive);
done:
    if (table) HeapFree(GetProcessHeap(), 0, table);
    CloseHandle(file);
    return NULL;
}

void hu_cpk_free(hu_archive *archive) {
    int index;
    if (!archive) return;
    for (index = 0; index < archive->patch_count; index++) {
        if (archive->patches[index].slot) {
            HeapFree(GetProcessHeap(), 0, archive->patches[index].slot);
        }
    }
    if (archive->cs2do) HeapFree(GetProcessHeap(), 0, archive->cs2do);
    if (archive->ds2cs) HeapFree(GetProcessHeap(), 0, archive->ds2cs);
    HeapFree(GetProcessHeap(), 0, archive);
}

BOOL hu_cpk_patch_read(hu_archive *archive, LONGLONG offset, unsigned char *buffer,
                       DWORD bytes) {
    int index;
    BOOL patched = FALSE;
    if (!archive) return FALSE;
    for (index = 0; index < archive->patch_count; index++) {
        u64 start = archive->first + archive->patches[index].index * archive->sector_size;
        u64 end = start + archive->sector_size;
        u64 from = (u64)offset, to = (u64)offset + bytes;
        int slot = index;
        if (to <= start || from >= end) continue;
        {
            u64 low = from > start ? from : start;
            u64 high = to < end ? to : end;
            size_t source = (size_t)(low - start);
            size_t target = (size_t)(low - (u64)offset);
            memcpy(buffer + target, archive->patches[slot].slot + source, (size_t)(high - low));
            patched = TRUE;
        }
    }
    return patched;
}
