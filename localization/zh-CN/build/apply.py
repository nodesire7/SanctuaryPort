"""Write the Chinese localization into a pristine upstream checkout, before it is built.

The launcher embeds ``launcher-rust/assets/i18n.json`` and ``assets/trophies/Diablo_III.json``
with ``include_str!``, so a translation has to be part of the build: there is no file to
redirect at run time. This script is the whole "add our content" step - it runs on a throwaway
checkout of an upstream tag in CI, never on a branch, so upstream can be followed without a
merge conflict ever being possible.

usage: python apply.py CHECKOUT_DIR
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8")


def apply_launcher(checkout: Path) -> None:
    target = checkout / "launcher-rust/assets/i18n.json"
    table = read(target)
    translations = read(DATA / "launcher.json")
    extra = table.setdefault("extra", {})
    # Upstream already translates some texts (a partial `zh` table): keep those, let ours win
    # where both have a text, so an upstream addition is never lost to this overlay.
    merged = dict(extra.get("zh", {}))
    merged.update(translations)
    extra["zh"] = merged
    write(target, table)
    print(f"launcher: {len(translations)} texts, {len(merged)} after merging with upstream")


def apply_trophies(checkout: Path) -> None:
    target = checkout / "assets/trophies/Diablo_III.json"
    catalogue = read(target)
    translations = read(DATA / "trophies.json")
    groups, texts = translations["groups"], translations["trophies"]

    named = 0
    for group in catalogue["groups"]:
        name = groups.get(str(group["id"]))
        if name:
            group["name"] = name
            named += 1
    described = 0
    for trophy in catalogue["trophies"]:
        text = texts.get(str(trophy["id"]))
        if not text:
            continue
        trophy["name"] = text["name"]
        trophy["description"] = text["description"]
        described += 1
    write(target, catalogue)
    # A trophy listed upstream that we do not know yet keeps its English text: the page still
    # works, and the next translation pass fills it in.
    print(f"trophies: {described}/{len(catalogue['trophies'])} texts, {named} group names")


def main() -> None:
    checkout = Path(sys.argv[1]).resolve()
    if not (checkout / "launcher-rust/Cargo.toml").is_file():
        sys.exit(f"not an upstream checkout: {checkout}")
    apply_launcher(checkout)
    apply_trophies(checkout)
    print("localization applied")


if __name__ == "__main__":
    main()
