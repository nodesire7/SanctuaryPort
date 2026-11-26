"""Wrap a built launcher, its game runtime and the port package into the update package.

The updater looks for ``SanctuaryPort-launcher-zh-CN.zip`` in the release of a ``zh-CN-v*``
tag and unpacks it over the installation. The package carries the whole port, not just the
launcher: a player who installed a localization for an older port then updates in one step,
with no second download to hunt for and no mixture of versions left behind. ``install_over``
keeps what belongs to the player (``user/``, ``game/``, settings, mods) either way.

Contents:

  <everything from the upstream port package>
  SanctuaryPort.exe      the launcher, built from the upstream sources with the Chinese texts
  bin/SDL3.dll           the unmodified upstream runtime
  scripts/zh_credit.pyc   asset-only localization using the runtime file-mount API
  update.json            the tag this package installs

usage: python package.py --exe PATH --tag TAG --out DIR --port ZIP
"""
import argparse
import hashlib
import json
import py_compile
import sys
import tempfile
import zipfile
from pathlib import Path

ARCHIVE = "SanctuaryPort-launcher-zh-CN.zip"
OVERRIDDEN = {"sanctuaryport.exe", "update.json"}


def localized_manifest(data: bytes) -> bytes:
    """Keep upstream launch arguments and config values, applying labels by setting ID/value."""
    manifest = json.loads(data)
    source = Path(__file__).resolve().parents[3] / "recomplauncher.json"
    zh = json.loads(source.read_bytes())
    manifest["repo"] = zh["repo"]
    manifest["game"]["label"] = zh["game"]["label"]
    if "update" in manifest["game"]:
        manifest["game"]["update"]["label"] = zh["game"]["update"]["label"]
    settings = {item["id"]: item for item in zh["settings"]}
    for item in manifest["settings"]:
        translated = settings.get(item["id"])
        if translated is None:
            continue
        for field in ("label", "help"):
            if field in item and field in translated:
                item[field] = translated[field]
        labels = {json.dumps(c["set"], sort_keys=True): c["label"] for c in translated.get("choices", [])}
        for choice in item.get("choices", []):
            key = json.dumps(choice["set"], sort_keys=True)
            if key in labels:
                choice["label"] = labels[key]
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


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


def credit_module(out: Path, port: Path) -> bytes:
    # The shipped interpreter loads sourceless bytecode only for its own Python version.
    with zipfile.ZipFile(port) as archive:
        stdlib = f"/python/python{sys.version_info.major}{sys.version_info.minor}.zip"
        if not any(name.endswith(stdlib) for name in archive.namelist()):
            raise SystemExit(f"build the credit module with the embedded Python version ({stdlib})")
    here = Path(__file__).resolve().parent
    reader = (here.parents[2] / "scripts/d3_credit.py").read_text(encoding="utf-8")
    mounts = (here.parent / "overlay/credit_mounts.py").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(dir=out) as scratch:
        source = Path(scratch) / "zh_credit.py"
        source.write_text(reader + "\n" + mounts, encoding="utf-8")
        compiled = py_compile.compile(str(source), dfile="zh_credit.py", doraise=True,
                                      invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        return Path(compiled).read_bytes()


def localize_runner(data: bytes) -> bytes:
    anchor = b"        process = subprocess.Popen(command,"
    if data.count(anchor) != 1:
        raise SystemExit("the game runner changed; check its file mount arguments")
    before = (b"        if diablo3:\n"
              b"            import zh_credit\n"
              b"            command.extend(zh_credit.mount_args([game] if mod_view else layers,\n"
              b"                                               out / 'zh-credit'))\n")
    return data.replace(anchor, before + anchor)


def protect_game_start(data: bytes) -> bytes:
    # At intermediate render heights upstream loads both font sets. This translation's
    # expanded fonts overflow the game's font table during startup when both are loaded.
    anchor = b"    skipped={}\n    try:\n        writes=compile_patches("
    if data.count(anchor) != 1:
        raise SystemExit("the patch compiler changed; review the translated font compatibility fix")
    guard = (
        '    if sha == "680387c887625dbeaef0c46fac54a893fcb45eac428c85632acb06e6062a3176"'
        ' and "Text Size - Both Font Sets" in names:\n'
        '        import zh_credit\n'
        '        if zh_credit.has_expanded_chinese_fonts(a.game_dir, os.environ.get("SANCTUARY_APP0_LOWER")):\n'
        '            names = [name for name in names if name != "Text Size - Both Font Sets"]\n'
        '            skipped["Text Size - Both Font Sets"] = "使用当前中文字体包时保留游戏原有字体加载方式"\n'
    ).encode()
    return data.replace(anchor, anchor.replace(b"    try:", guard + b"    try:"))


def build(exe: Path, tag: str, out: Path, port: Path, trophies: Path = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    archive = out / ARCHIVE
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for name, data in port_entries(port):
            if trophies is not None and name.lower() == "assets/trophies/diablo_iii.json":
                data = trophies.read_bytes()
            elif name.lower() == "recomplauncher.json":
                data = localized_manifest(data)
            elif name == "run.py":
                data = localize_runner(data)
            elif name == "scripts/patches.py":
                data = protect_game_start(data)
            package.writestr(name, data)
        package.write(exe, "SanctuaryPort.exe")
        package.writestr("scripts/zh_credit.pyc", credit_module(out, port))
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
    parser.add_argument("--port", type=Path, required=True, help="the upstream port package to carry along")
    parser.add_argument("--trophies", type=Path, help="localized trophy catalogue from the patched checkout")
    args = parser.parse_args()
    archive = build(args.exe, args.tag, args.out, args.port, args.trophies)
    print(f"{args.tag} {archive} {archive.with_suffix('.zip.sha256').read_text().split()[0]}")


if __name__ == "__main__":
    main()
