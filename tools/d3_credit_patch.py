"""Replace a UTF-8 string inside a Diablo III console archive (.cpk) in place.

The port only ever *reads* the game's archives (see scripts/d3_cpk.py), so a wrong byte in
one of them means re-downloading 700 MB.  This tool is deliberately conservative:

* it never changes the length of the file it edits, so no offset, size, hash or name table in
  the archive header moves, and every following file stays where it was;
* it rewrites single compressed sectors (16 KiB slot each), re-emitting them as one chunk and
  padding the slot - the archive header is not touched at all;
* the replacement must keep the entry's byte length, because the .stl keeps a per-string
  length in its own table;
* it verifies the result by re-opening the archive through scripts/d3_cpk.py and comparing.

Usage::

    python tools/d3_credit_patch.py --cpk <path> --entry "StringList\\Console.stl" \
        --old "GKinto.com 游戏大桶 汉化制作" --new "bbs.GBTGame.me 海量游戏尽在GBT" [--apply]

Without --apply it only reports what it would do (dry run).
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import d3_cpk  # noqa: E402


def sector_slots(archive: d3_cpk.Archive, loc: int, size: int):
    """(sector index, ) covering [loc, loc + size) - same walk the reader does."""
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


def rewrite_sector(path: str, archive: d3_cpk.Archive, sector: int, payload: bytes):
    """Store `payload` as the sector's single chunk, padding the fixed slot with zeros."""
    if len(payload) != len(archive._sector(sector)):
        raise ValueError('sector payload changed length')
    if len(payload) >= 0x10000:
        raise ValueError('sector payload too large for a chunk')
    blob = zlib.compress(payload, 9)
    if 6 + len(blob) > archive.sector_size:
        raise ValueError(f'sector {sector}: compressed {len(blob)} + 6 does not fit '
                         f'{archive.sector_size}')
    header = struct.pack('<3H', len(payload) & 0xFFFF, len(payload) >> 16, len(blob))
    slot = bytearray(archive.sector_size)
    slot[:6] = header
    slot[6:6 + len(blob)] = blob
    offset = archive.first + sector * archive.sector_size
    with open(path, 'r+b') as fh:
        fh.seek(offset)
        fh.write(slot)
    return 6 + len(blob)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cpk', required=True)
    ap.add_argument('--entry', default=r'StringList\Console.stl')
    ap.add_argument('--old', default='GKinto.com 游戏大桶 汉化制作')
    ap.add_argument('--new', default='bbs.GBTGame.me 海量游戏尽在GBT')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    old = args.old.encode('utf-8')
    new = args.new.encode('utf-8')
    print(f'archive : {args.cpk}')
    print(f'entry   : {args.entry}')
    print(f'old     : {args.old!r} ({len(old)} bytes)')
    print(f'new     : {args.new!r} ({len(new)} bytes)')
    if len(new) != len(old):
        print(f'ERROR: replacement must be exactly {len(old)} bytes to keep the .stl table and '
              f'the archive layout intact', file=sys.stderr)
        return 2

    with d3_cpk.Archive(args.cpk) as archive:
        if args.entry not in archive.names:
            print(f'ERROR: {args.entry} not in archive', file=sys.stderr)
            return 2
        h, size, index = archive.entries[archive.names[args.entry]]
        if h != d3_cpk.fnv1a64(args.entry):
            print('ERROR: name hash mismatch', file=sys.stderr)
            return 2
        loc = archive.locations[index]
        data = archive.read(args.entry)
        print(f'file    : {size} bytes at decompressed offset {loc} (0x{loc:X})')

        hits = []
        start = 0
        while True:
            pos = data.find(old, start)
            if pos < 0:
                break
            hits.append(pos)
            start = pos + 1
        if not hits:
            if data.find(new) >= 0:
                print('already patched - nothing to do')
                return 0
            print('ERROR: old string not found', file=sys.stderr)
            return 1
        if len(hits) > 1:
            print(f'ERROR: {len(hits)} occurrences, refusing to guess', file=sys.stderr)
            return 1
        at = hits[0]
        print(f'string  : file offset {at} (0x{at:X}) -> sector {loc + at}')

        patched = bytearray(data)
        patched[at:at + len(old)] = new
        if bytes(patched) == data:
            print('already patched - nothing to do')
            return 0

        sectors = sector_slots(archive, loc, size)
        touched = []
        for sector in sectors:
            base = archive.cs2do[sector]
            block = bytearray(archive._sector(sector))
            lo = max(loc + at, base)
            hi = min(loc + at + len(old), base + len(block))
            if lo >= hi:
                continue
            block[lo - base:hi - base] = patched[lo - loc:hi - loc]
            touched.append((sector, block))
        print(f'sectors : {[s for s, _ in touched]}')

        if not args.apply:
            for sector, block in touched:
                probe = zlib.compress(bytes(block), 9)
                print(f'  sector {sector}: recompress {len(probe)} bytes '
                      f'(slot {archive.sector_size})')
            print('DRY RUN - pass --apply to write')
            return 0

        backup = args.cpk + '.gbt-sector-bak'
        with open(args.cpk, 'rb') as fh, open(backup, 'wb') as out:
            for sector, _ in touched:
                fh.seek(archive.first + sector * archive.sector_size)
                out.write(fh.read(archive.sector_size))
        print(f'backup  : {backup} ({len(touched)} sectors)')

        for sector, block in touched:
            used = rewrite_sector(args.cpk, archive, sector, bytes(block))
            print(f'  wrote sector {sector}: {used} bytes used of {archive.sector_size}')

    with d3_cpk.Archive(args.cpk) as check:
        after = check.read(args.entry)
    if len(after) != size:
        print('ERROR: file size changed', file=sys.stderr)
        return 1
    if new not in after:
        print('ERROR: new string not readable back', file=sys.stderr)
        return 1
    if old in after:
        print('ERROR: old string still present', file=sys.stderr)
        return 1
    print('verified: entry re-reads cleanly, old string gone')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
