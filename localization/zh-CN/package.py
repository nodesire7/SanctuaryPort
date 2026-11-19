"""Build the Windows launcher-only update and its SHA-256 sidecar."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

CRATE = Path(__file__).resolve().parents[2] / "launcher-rust"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32" or args.revision < 1:
        parser.error("Use Windows and a positive localization revision number.")
    args.out.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--locked", "--format-version", "1", "--no-deps"], cwd=CRATE
    ))
    package = next(p for p in metadata["packages"] if p["name"] == "sanctuaryport-launcher")
    tag = f"zh-CN-v{package['version']}-zh{args.revision}"
    env = {**os.environ, "SANCTUARY_RELEASE_TAG": tag}
    subprocess.run(["cargo", "build", "--locked", "--release", "--bin", package["name"]],
                   env=env, cwd=CRATE, check=True)
    exe = Path(metadata["target_directory"]) / "release" / (package["name"] + ".exe")
    archive = args.out / "SanctuaryPort-launcher-zh-CN.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.write(exe, "SanctuaryPort.exe")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_bytes(f"{digest}  {archive.name}\n".encode("ascii"))
    print(tag, archive, digest)


if __name__ == "__main__":
    main()
