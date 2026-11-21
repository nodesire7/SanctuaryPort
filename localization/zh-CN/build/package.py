"""Wrap a built launcher and its tag into the localization update package.

The updater looks for ``SanctuaryPort-launcher-zh-CN.zip`` in the release of a ``zh-CN-v*``
tag and unpacks it over the installation: ``SanctuaryPort.exe`` replaces the launcher and
``update.json`` says which tag is installed. Only the launcher is in the package - run.py,
scripts/ and bin/ belong to the port release and must not be mixed across versions.

usage: python package.py --exe PATH --tag TAG --out DIR
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ARCHIVE = "SanctuaryPort-launcher-zh-CN.zip"


def build(exe: Path, tag: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    archive = out / ARCHIVE
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.write(exe, "SanctuaryPort.exe")
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
    args = parser.parse_args()
    archive = build(args.exe, args.tag, args.out)
    print(f"{args.tag} {archive} {archive.with_suffix('.zip.sha256').read_text().split()[0]}")


if __name__ == "__main__":
    main()
