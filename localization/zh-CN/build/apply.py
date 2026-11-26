"""Write the Chinese localization into a pristine upstream checkout, before it is built.

Everything this project adds is written here, on a throwaway checkout of an upstream tag:

  * the launcher's texts (``launcher-rust/assets/i18n.json``) and trophy list gain the Chinese
    translation. They are embedded with ``include_str!``, so the translation has to be part of
    the build - there is no file to redirect at run time;
  * the updater is pointed at this fork's releases, its downloads are routed through the
    mirrors that are reachable from the players' networks (``overlay/update_routes.rs``), and a
    blocked API falls back to the release page;
  * the Advanced page gains this project's credit and forum link, which upstream has no place
    for. Crash reports go to this project's tracker.

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
FORUM_URL = "https://bbs.gbtgame.me"
CREDIT_LINE = "汉化作者：A-FEI-   ·   GBTGAME 论坛  bbs.gbtgame.me"

UPDATE_RS = "launcher-rust/src/core/update.rs"
ROUTES_RS = "launcher-rust/src/core/update_routes.rs"
APP_RS = "launcher-rust/src/ui/app.rs"
ADVANCED_RS = "launcher-rust/src/ui/pages/advanced.rs"
CATALOG_RS = "launcher-rust/src/core/catalog.rs"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8")


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"the patch anchor for {what} moved; upstream changed it")
    return text.replace(old, new)


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
    if described != len(texts) or named != len(groups):
        raise SystemExit("upstream trophy IDs changed; check the existing localization before publishing")
    write_json(target, catalogue)
    # A trophy listed upstream that we do not know yet keeps its English text: the page still
    # works, and the next translation pass fills it in.
    print(f"trophies: {described}/{len(catalogue['trophies'])} texts, {named} group names")


def apply_update(checkout: Path) -> None:
    """Point the updater at this fork and route its downloads through the mirrors."""
    source = checkout / UPDATE_RS
    text = source.read_text(encoding="utf-8")

    text = replace_once(text, f'repos/{UPSTREAM_REPO}/releases', f'repos/{FORK_REPO}/releases',
                        "the release API")
    text = replace_once(text, f'github.com/{UPSTREAM_REPO}/releases/latest',
                        f'github.com/{FORK_REPO}/releases/latest', "the release page")

    anchor = "use crate::core::release::{self, ReleaseId};\n"
    if anchor not in text:
        raise SystemExit("update.rs no longer imports release the way the patch expects")
    if "mod routes;" not in text:
        text = text.replace(anchor, anchor + "\n#[path = \"update_routes.rs\"]\nmod routes;\n",
                            1)

    text = replace_once(text, "let expected = release_checksum(url).ok();",
                        "let expected = release_checksum(url).map_err(text)?;",
                        "the Windows package checksum")
    text = replace_once(text, "download(url, &archive, expected.as_deref(), report)",
                        "routes::download_zip(&routes::candidates(url), &archive, Some(&expected), report)",
                        "the Windows download")

    # Both the public listing and the live UI use the same upstream ETag/rate-limit cache.
    uncached = """ReleaseCache::default().list(
        Instant::now(),
        |etag| fetch_list(&releases_api(), etag),
        |text| parse_list(text, windows),
    )"""
    text = replace_once(text, uncached,
                        "routes::list_with_fallback(&mut ReleaseCache::default(), windows)",
                        "the uncached release listing")
    cached = """cache
            .list(
                Instant::now(),
                |etag| fetch_list(&releases_api(), etag),
                |text| parse_list(text, windows),
            )"""
    text = replace_once(text, cached, "routes::list_with_fallback(&mut cache, windows)",
                        "the UI release listing")

    source.write_text(text, encoding="utf-8")
    (checkout / ROUTES_RS).write_text((OVERLAY / "update_routes.rs").read_text(encoding="utf-8"),
                                      encoding="utf-8")

    # Crash reports belong to the project that ships this build.
    report = checkout / "launcher-rust/src/core/crash_report.rs"
    reports = report.read_text(encoding="utf-8")
    reports = replace_once(reports, f'github.com/{UPSTREAM_REPO}/issues/new',
                           f'github.com/{FORK_REPO}/issues/new', "the issue link")
    report.write_text(reports, encoding="utf-8")

    print(f"updater: releases of {FORK_REPO}, mirrors + release page fallback, issue link moved")


def apply_branding(checkout: Path) -> None:
    """Put this project's credit and forum link on the Advanced page.

    Upstream has no such element; the row is added at build time, like the translations, so
    this branch still holds no upstream file.
    """
    app = checkout / APP_RS
    text = app.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "    /// One read of `nav_pad`.\n    NavPoll,\n",
        "    /// One read of `nav_pad`.\n    NavPoll,\n"
        "    /// Open the localization forum (this build's addition; upstream has no such link).\n"
        "    OpenForum,\n",
        "the message list",
    )
    text = replace_once(
        text,
        "        Message::OpenFolder(folder) => {\n",
        "        Message::OpenForum => {\n"
        f'            let _ = crate::core::desktop::open_url("{FORUM_URL}");\n'
        "        }\n"
        "        Message::OpenFolder(folder) => {\n",
        "the folder handler",
    )
    app.write_text(text, encoding="utf-8")

    advanced = checkout / ADVANCED_RS
    page = advanced.read_text(encoding="utf-8")
    page = replace_once(
        page,
        '            Some(&hint("F12 in the game saves a screenshot.")),\n'
        "        ));\n"
        "    page.into()\n",
        '            Some(&hint("F12 in the game saves a screenshot.")),\n'
        "        ))\n"
        '        .push(section(state.rtl, "汉化"))\n'
        f'        .push(text("{CREDIT_LINE}"))\n'
        '        .push(flat("打开汉化论坛".to_string(), Message::OpenForum));\n'
        "    page.into()\n",
        "the page tail",
    )
    advanced.write_text(page, encoding="utf-8")
    # Seamless updates: a launcher that never checks on its own leaves the player on an old
    # build until they go looking. The build turns the check on by default.
    catalog = checkout / CATALOG_RS
    defaults = catalog.read_text(encoding="utf-8")
    defaults = replace_once(defaults, 'app("check_updates", Flag, Bool(false), None),',
                            'app("check_updates", Flag, Bool(true), None),',
                            "the update-check default")
    catalog.write_text(defaults, encoding="utf-8")

    print(f"branding: {CREDIT_LINE} + forum link on the Advanced page, update check on by default")


def main() -> None:
    checkout = Path(sys.argv[1]).resolve()
    if not (checkout / "launcher-rust/Cargo.toml").is_file():
        sys.exit(f"not an upstream checkout: {checkout}")
    apply_launcher(checkout)
    apply_trophies(checkout)
    apply_update(checkout)
    apply_branding(checkout)
    print("localization applied")


if __name__ == "__main__":
    main()
