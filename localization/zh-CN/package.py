"""Build the Windows launcher update and its SHA-256 sidecar.

Two shapes, both read by the launcher's updater:

* a full package carries ``SanctuaryPort.exe`` (a release that changes the launcher itself);
* a differential package carries only the files that changed since an earlier tag -
  ``run.py``, ``scripts/*.py``, assets - and no launcher at all. The updater overlays it
  over the installation and the next start runs it, so a script fix costs a few KB
  instead of a 20 MB rebuild.

Both declare the tag they install in ``update.json``; a differential package has no binary
to carry it, and the launcher reads it back to know it is up to date.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

CRATE = Path(__file__).resolve().parents[2] / "launcher-rust"
ROOT = CRATE.parent

# What an overlay may replace: the port's runtime, never the build tree or the docs.
RUNTIME = ("run.py", "run.sh", "scripts/", "assets/", "launcher/", "patches/",
           "recomplauncher.json")


def changed_since(tag):
    """The runtime files that differ between `tag` and HEAD, newest state, existing only."""
    out = subprocess.check_output(["git", "diff", "--name-only", f"{tag}..HEAD"],
                                  cwd=ROOT, text=True)
    files = []
    for line in out.splitlines():
        name = line.strip()
        if not name or not (ROOT / name).is_file():
            continue
        if any(name == entry or name.startswith(entry) for entry in RUNTIME):
            files.append(name)
    return files


def launcher_exe():
    """The release build of the launcher, rebuilt when stale."""
    metadata = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--locked", "--format-version", "1", "--no-deps"], cwd=CRATE
    ))
    package = next(p for p in metadata["packages"] if p["name"] == "sanctuaryport-launcher")
    return package, Path(metadata["target_directory"]) / "release" / (package["name"] + ".exe")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--delta-from", metavar="TAG",
                        help="pack only what changed since TAG (implies no launcher rebuild "
                             "unless --with-launcher)")
    parser.add_argument("--with-launcher", action="store_true",
                        help="also ship SanctuaryPort.exe (bootstrap a release that needs it)")
    args = parser.parse_args()
    if sys.platform != "win32" or args.revision < 1:
        parser.error("Use Windows and a positive localization revision number.")

    args.out.mkdir(parents=True, exist_ok=True)
    package, exe = launcher_exe()
    tag = f"zh-CN-v{package['version']}-zh{args.revision}"

    ship_launcher = args.with_launcher or not args.delta_from
    if ship_launcher:
        env = {**os.environ, "SANCTUARY_RELEASE_TAG": tag}
        subprocess.run(["cargo", "build", "--locked", "--release", "--bin", package["name"]],
                       env=env, cwd=CRATE, check=True)

    files = changed_since(args.delta_from) if args.delta_from else []

    archive = args.out / "SanctuaryPort-launcher-zh-CN.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        if ship_launcher:
            output.write(exe, "SanctuaryPort.exe")
        for name in files:
            output.write(ROOT / name, name)
        output.writestr("update.json", json.dumps({"tag": tag}, ensure_ascii=False) + "\n")

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_bytes(f"{digest}  {archive.name}\n".encode("ascii"))
    kind = "full" if ship_launcher else "differential"
    print(f"{tag} {kind} {archive} {digest}")
    for name in files:
        print(f"  + {name}")


if __name__ == "__main__":
    main()
