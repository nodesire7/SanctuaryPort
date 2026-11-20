"""Build the Windows update package and its SHA-256 sidecar.

The package is an **overlay**: the updater unpacks it over the installation, replacing the
port's own files and leaving the player's alone (``user/``, ``out/``, ``mods/``, saves,
``sanctuary.ini``, ``settings.json``). Two shapes:

* the port's runtime - ``run.py``, ``scripts/*.py``, ``assets/``, ``launcher/``, ``patches/``,
  ``recomplauncher.json`` - always. It is a few hundred KB, so shipping all of it costs
  nothing and cannot leave an installation with a ``run.py`` that expects a ``scripts/`` it
  does not have (that is exactly how v0.6.7-zh3 broke on its first publish: builds shipped
  only the launcher, so the scripts on disk were months older than the release they were
  diffed against);
* ``SanctuaryPort.exe`` only with ``--with-launcher``: the releases that change the launcher
  itself, and the one that has to carry the overlay-capable updater to older installs.

The tag the package installs goes in ``update.json``; the launcher reads it back, so an
overlay without a new executable still moves the installed version forward.
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

# Everything the port runs from: shipped whole at every release.
RUNTIME = ("run.py", "run.sh", "recomplauncher.json",
           "scripts", "assets", "launcher", "patches")
# Files in those folders that belong to a source checkout, not to an installation.
SKIP = {"__pycache__", ".pyc", ".pyo"}


def runtime_files():
    """The port's own files, relative to the repository root, sorted."""
    names = []
    for entry in RUNTIME:
        path = ROOT / entry
        if path.is_file():
            names.append(path)
        elif path.is_dir():
            names.extend(p for p in sorted(path.rglob("*")) if p.is_file())
    return [(p, p.relative_to(ROOT)) for p in names
            if not any(part in SKIP or part.endswith(tuple(SKIP)) for part in p.parts)]


def launcher_exe():
    """The crate, and where its release build lands."""
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
    parser.add_argument("--with-launcher", action="store_true",
                        help="also ship SanctuaryPort.exe (a launcher change, or the release "
                             "that brings overlay support to older installations)")
    args = parser.parse_args()
    if sys.platform != "win32" or args.revision < 1:
        parser.error("Use Windows and a positive localization revision number.")

    args.out.mkdir(parents=True, exist_ok=True)
    package, exe = launcher_exe()
    tag = f"zh-CN-v{package['version']}-zh{args.revision}"

    if args.with_launcher:
        env = {**os.environ, "SANCTUARY_RELEASE_TAG": tag}
        subprocess.run(["cargo", "build", "--locked", "--release", "--bin", package["name"]],
                       env=env, cwd=CRATE, check=True)

    files = runtime_files()
    archive = args.out / "SanctuaryPort-launcher-zh-CN.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        if args.with_launcher:
            output.write(exe, "SanctuaryPort.exe")
        for source, name in files:
            output.write(source, name.as_posix())
        output.writestr("update.json", json.dumps({"tag": tag}, ensure_ascii=False) + "\n")

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_bytes(f"{digest}  {archive.name}\n".encode("ascii"))
    print(f"{tag} {'full' if args.with_launcher else 'runtime'} {archive} {digest}")
    print(f"  {len(files)} runtime files ({sum(p.stat().st_size for p, _ in files):,} bytes)")


if __name__ == "__main__":
    main()
