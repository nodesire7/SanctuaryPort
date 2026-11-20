"""Replace the fan translation's credit string in a Diablo III update, or check it is aligned.

The start-up path does this on its own (scripts/d3_credit.py, called from run.py); this is the
same code with a face on it, for a dump outside the port, a dry run, or a manual restore.

Usage::

    python tools/d3_credit_patch.py --update "<...>/CUSA00242-UPDATE"            # check / dry run
    python tools/d3_credit_patch.py --update "<...>/CUSA00242-UPDATE" --apply    # write

``--cpk`` picks the archive directly when the folder is not laid out as an update.
Exit status: 0 aligned or already aligned, 1 not found or not patchable, 2 refused.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import d3_credit  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--update', help='the update folder (holds CPKs/ and sce_sys/)')
    ap.add_argument('--cpk', help='the archive itself, when there is no update folder')
    ap.add_argument('--entry', default=d3_credit.ENTRY)
    ap.add_argument('--old', default=d3_credit.OLD)
    ap.add_argument('--new', default=d3_credit.NEW)
    ap.add_argument('--apply', action='store_true', help='write instead of only reporting')
    args = ap.parse_args()

    if not args.cpk and not args.update:
        ap.error('give --update or --cpk')
    path = args.cpk or d3_credit.archive_path(args.update)
    if not path:
        print('no CPKs/enUS_Patch2_7_7.cpk in that update: nothing to align', file=sys.stderr)
        return 1

    print(f'archive : {path}')
    print(f'entry   : {args.entry}')
    print(f'old     : {args.old!r} ({len(args.old.encode())} bytes)')
    print(f'new     : {args.new!r} ({len(args.new.encode())} bytes)')

    try:
        if not args.apply:
            with d3_credit.d3_cpk.Archive(path) as archive:
                if args.entry not in archive.names:
                    print('entry not in this archive', file=sys.stderr)
                    return 1
                data = archive.read(args.entry)
            old, new = args.old.encode('utf-8'), args.new.encode('utf-8')
            if old in data:
                if len(new) != len(old):
                    print(f'refused: the replacement must be {len(old)} bytes, not {len(new)}',
                          file=sys.stderr)
                    return 2
                at = data.find(old)
                print(f'file    : {len(data)} bytes, string at {at} (0x{at:X})')
                print('DRY RUN - pass --apply to write')
                return 0
            print('already aligned' if new in data else 'the old string is not there',
                  file=sys.stderr)
            return 0 if new in data else 1

        state = d3_credit.patch_archive(path, args.entry, args.old, args.new, backup=True)
        print(f'archive : {state}')
        if state == 'patched' and args.update:
            print('notes   : ' + ('line added' if d3_credit.align_changeinfo(args.update)
                                  else 'already there'))
    except (ValueError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2

    with d3_credit.d3_cpk.Archive(path) as check:
        after = check.read(args.entry)
    if args.new.encode() not in after or args.old.encode() in after:
        print('error: the archive does not read back the new string', file=sys.stderr)
        return 2
    print('verified: entry re-reads cleanly, old string gone')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
