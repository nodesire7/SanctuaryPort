"""Wrap a built launcher, and the localization DLL when there is one, into the update package.

The updater looks for ``SanctuaryPort-launcher-zh-CN.zip`` in the release of a ``zh-CN-v*``
tag and unpacks it over the installation. What goes in it:

  SanctuaryPort.exe      the launcher, built from the upstream sources with the Chinese texts
  bin/SDL3.dll           the proxy that rewrites the in-game credit in memory
  bin/SDL3.real.dll      the library the port shipped, byte for byte, under a new name
  update.json            the tag this package installs

The launcher's texts are compiled in (``include_str!``), so they are applied to the sources
before the build, not shipped as data. The DLL only touches the game's archive reads: no file of
the port is modified, so an upstream update cannot conflict with a translation.

usage: python package.py --exe PATH --tag TAG --out DIR [--dll PATH --sdl3 PATH]
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ARCHIVE = "SanctuaryPort-launcher-zh-CN.zip"


def build(exe: Path, tag: str, out: Path, dll: Path = None, sdl3: Path = None) -> Path:
    if (dll is None) != (sdl3 is None):
        raise SystemExit("--dll and --sdl3 go together")
    out.mkdir(parents=True, exist_ok=True)
    archive = out / ARCHIVE
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.write(exe, "SanctuaryPort.exe")
        if dll is not None:
            package.write(dll, "bin/SDL3.dll")
            package.write(sdl3, "bin/SDL3.real.dll")
        package.writestr("update.json", json.dumps({"tag": tag}, ensure_ascii=False) + "\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_bytes(f"{digest}  {archive.name}\n".encode("ascii"))
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dll", type=Path, help="the built SDL3 proxy")
    parser.add_argument("--sdl3", type=Path, help="the port's own SDL3.dll, shipped as SDL3.real.dll")
    args = parser.parse_args()
    archive = build(args.exe, args.tag, args.out, args.dll, args.sdl3)
    print(f"{args.tag} {archive} {archive.with_suffix('.zip.sha256').read_text().split()[0]}")


if __name__ == "__main__":
    main()
