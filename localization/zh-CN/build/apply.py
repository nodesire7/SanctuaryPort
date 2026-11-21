"""Write the Chinese localization into a pristine upstream checkout, before it is built.

Two things happen here, both on a throwaway checkout of an upstream tag:

  * the launcher's texts (``launcher-rust/assets/i18n.json``) and trophy list gain the Chinese
    translation. They are embedded with ``include_str!``, so the translation has to be part of
    the build - there is no file to redirect at run time;
  * the updater is pointed at this fork's releases and its downloads are routed through the
    mirrors that are reachable from the players' networks (``overlay/update_routes.rs``).

Every anchor below is required: an upstream change that moves one stops the build loudly
instead of shipping a launcher that silently never updates.

usage: python apply.py CHECKOUT_DIR
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
OVERLAY = HERE.parent / "overlay"

UPSTREAM_REPO = "CaduGimenes/SanctuaryPort"
FORK_REPO = "nodesire7/SanctuaryPort"

UPDATE_RS = "launcher-rust/src/core/update.rs"
ROUTES_RS = "launcher-rust/src/core/update_routes.rs"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
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
    write_json(target, table)
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
    write_json(target, catalogue)
    # A trophy listed upstream that we do not know yet keeps its English text: the page still
    # works, and the next translation pass fills it in.
    print(f"trophies: {described}/{len(catalogue['trophies'])} texts, {named} group names")


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"update.rs no longer holds {what} exactly once; the patch moved")
    return text.replace(old, new)


def apply_update(checkout: Path) -> None:
    """Point the updater at this fork and route its downloads through the mirrors."""
    source = checkout / UPDATE_RS
    text = source.read_text(encoding="utf-8")

    text = replace_once(text, f'repos/{UPSTREAM_REPO}/releases', f'repos/{FORK_REPO}/releases',
                        "the release API")
    text = replace_once(text, f'github.com/{UPSTREAM_REPO}/releases/latest',
                        f'github.com/{FORK_REPO}/releases/latest', "the release page")

    # The mirror module goes next to update.rs and is used from its download site only.
    anchor = "use crate::core::release::{self, ReleaseId};\n"
    if anchor not in text:
        raise SystemExit("update.rs no longer imports release the way the patch expects")
    if "mod routes;" not in text:
        text = text.replace(anchor, anchor + "\n#[path = \"update_routes.rs\"]\nmod routes;\n",
                            1)

    download_call = "download(url, &archive, expected.as_deref(), report).map_err(text)?;"
    patched_call = ("routes::download_zip(&routes::candidates(url), &archive, "
                    "expected.as_deref(), report).map_err(text)?;")
    text = replace_once(text, download_call, patched_call, "the Windows download")

    source.write_text(text, encoding="utf-8")
    (checkout / ROUTES_RS).write_text((OVERLAY / "update_routes.rs").read_text(encoding="utf-8"),
                                      encoding="utf-8")
    print(f"updater: releases of {FORK_REPO}, downloads through ghfast.top / gh-proxy.org")


def main() -> None:
    checkout = Path(sys.argv[1]).resolve()
    if not (checkout / "launcher-rust/Cargo.toml").is_file():
        sys.exit(f"not an upstream checkout: {checkout}")
    apply_launcher(checkout)
    apply_trophies(checkout)
    apply_update(checkout)
    print("localization applied")


if __name__ == "__main__":
    main()
