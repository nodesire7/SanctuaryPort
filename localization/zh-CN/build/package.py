"""Wrap a built launcher, its proxy and the port package into the update package.

The updater looks for ``SanctuaryPort-launcher-zh-CN.zip`` in the release of a ``zh-CN-v*``
tag and unpacks it over the installation. The package carries the whole port, not just the
launcher: a player who installed a localization for an older port then updates in one step,
with no second download to hunt for and no mixture of versions left behind. ``install_over``
keeps what belongs to the player (``user/``, ``game/``, settings, mods) either way.

Contents:

  <everything from the upstream port package>
  SanctuaryPort.exe      the launcher, built from the upstream sources with the Chinese texts
  bin/SDL3.dll           the proxy that rewrites the in-game credit in memory
  bin/SDL3.real.dll      the library the port shipped, byte for byte, under a new name
  update.json            the tag this package installs

usage: python package.py --exe PATH --tag TAG --out DIR [--port ZIP] [--dll PATH --sdl3 PATH]
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ARCHIVE = "SanctuaryPort-launcher-zh-CN.zip"
OVERRIDDEN = {"sanctuaryport.exe", "bin/sdl3.dll", "bin/sdl3.real.dll", "update.json"}


def port_entries(port: Path):
    """Every file of the upstream package, with its top folder removed."""
    with zipfile.ZipFile(port) as source:
        for info in source.infolist():
            if info.is_dir():
                continue
            parts = info.filename.split("/")
            inner = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
            if not inner or inner.lower() in OVERRIDDEN:
                continue
            yield inner, source.read(info)


def build(exe: Path, tag: str, out: Path, dll: Path = None, sdl3: Path = None,
          port: Path = None) -> Path:
    if (dll is None) != (sdl3 is None):
        raise SystemExit("--dll and --sdl3 go together")
    out.mkdir(parents=True, exist_ok=True)
    archive = out / ARCHIVE
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        if port is not None:
            for name, data in port_entries(port):
                package.writestr(name, data)
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
    parser.add_argument("--port", type=Path, help="the upstream port package to carry along")
    parser.add_argument("--dll", type=Path, help="the built SDL3 proxy")
    parser.add_argument("--sdl3", type=Path, help="the port's own SDL3.dll, shipped as SDL3.real.dll")
    args = parser.parse_args()
    archive = build(args.exe, args.tag, args.out, args.dll, args.sdl3, args.port)
    print(f"{args.tag} {archive} {archive.with_suffix('.zip.sha256').read_text().split()[0]}")


if __name__ == "__main__":
    main()
