"""Build the Windows launcher update locally and its SHA-256 sidecar.

The localization workflow (``.github/workflows/localization.yml``) does the same on GitHub
after a cross build; this is the local path, for trying a package before tagging. Both hand
the wrapping to ``package_launcher.py``, so the zip has one shape.

The package holds ``SanctuaryPort.exe`` and ``update.json`` only: the launcher carries its own
fixes (the translation credit is rewritten by ``core/d3_credit.rs``), and the port's scripts and
binaries belong to the full port package - mixing them broke v0.6.7-zh3.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package_launcher  # noqa: E402

CRATE = Path(__file__).resolve().parents[2] / "launcher-rust"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32" or args.revision < 1:
        parser.error("Use Windows and a positive localization revision number.")

    metadata = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--locked", "--format-version", "1", "--no-deps"], cwd=CRATE
    ))
    package = next(p for p in metadata["packages"] if p["name"] == "sanctuaryport-launcher")
    tag = f"zh-CN-v{package['version']}-zh{args.revision}"
    env = {**os.environ, "SANCTUARY_RELEASE_TAG": tag}
    subprocess.run(["cargo", "build", "--locked", "--release", "--bin", package["name"]],
                   env=env, cwd=CRATE, check=True)
    exe = Path(metadata["target_directory"]) / "release" / (package["name"] + ".exe")
    archive = package_launcher.build(exe, tag, args.out)
    print(tag, archive, archive.with_suffix(".zip.sha256").read_text().split()[0])


if __name__ == "__main__":
    main()
