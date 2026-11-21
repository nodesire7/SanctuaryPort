//! Does the port in this folder belong to this launcher?
//!
//! A localized release replaces `SanctuaryPort.exe` alone. Everything else - `bin/
//! sanctuary-probe.exe`, `run.py`, `scripts/`, the DLLs - comes from the full port package, and
//! an installed copy can be years older. That combination looks healthy in the launcher and
//! fails deep inside the game: a core from before 0.3.0 lacks the cache-less pipeline retry
//! (GitHub #12), so an AMD card stops with `Failed to create graphics pipeline: ErrorUnknown`
//! and nothing in the window ever suggested the port was out of date.
//!
//! The port package declares its version in `port.json` at its root. No file, or an older
//! version, and the launcher says so instead of starting a mismatched game.

use std::fs;
use std::path::Path;

use crate::core::release::version_tuple;
use crate::VERSION;

/// The file a port package writes at its root: `{"version": "0.6.7"}`.
pub const FILE: &str = "port.json";

/// The version the installed port declares, when it declares one.
pub fn declared(port_dir: &Path) -> Option<String> {
    let text = fs::read_to_string(port_dir.join(FILE)).ok()?;
    let json: serde_json::Value = serde_json::from_str(&text).ok()?;
    json.get("version")?.as_str().map(str::to_owned)
}

/// Whether `port_dir` holds a port at all, rather than an empty folder waiting for one.
fn holds_a_port(port_dir: &Path) -> bool {
    let probe = if cfg!(windows) { "sanctuary-probe.exe" } else { "sanctuary-probe" };
    port_dir.join("bin").join(probe).is_file()
        || port_dir.join("run.py").is_file()
        || port_dir.join("run.sh").is_file()
}

/// The warning for a launcher newer than the port it starts, else `None`.
///
/// `packaged` is the launcher's own state: a source checkout has no `port.json` and is not
/// meant to, so it is never warned about. `mine` is the launcher's version, passed in so the
/// rule can be tested without a built package.
pub fn verdict(port_dir: &Path, mine: &str, packaged: bool) -> Option<String> {
    if !packaged || !holds_a_port(port_dir) {
        return None;
    }
    match declared(port_dir) {
        Some(theirs) if version_tuple(&theirs) >= version_tuple(mine) => None,
        Some(theirs) => Some(format!(
            "The port in this folder is version {theirs}, this launcher is {mine}. Install the \
             full port package (SanctuaryPort-windows.zip) so the two match: an older port can \
             stop the game with a graphics pipeline error."
        )),
        None => Some(format!(
            "This port does not say which version it is, so it is older than this launcher \
             ({mine}). Install the full port package (SanctuaryPort-windows.zip): an older port \
             can stop the game with a graphics pipeline error."
        )),
    }
}

/// [`verdict`] for this build: the launcher's own version, and whether it is a packaged one.
pub fn stale(port_dir: &Path) -> Option<String> {
    verdict(port_dir, VERSION, crate::core::update::is_packaged())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn port(dir: &Path, json: Option<&str>) {
        fs::create_dir_all(dir.join("bin")).unwrap();
        fs::write(dir.join("bin").join(if cfg!(windows) {
            "sanctuary-probe.exe"
        } else {
            "sanctuary-probe"
        }), b"")
        .unwrap();
        if let Some(json) = json {
            fs::write(dir.join(FILE), json).unwrap();
        }
    }

    #[test]
    fn an_older_port_is_named_with_both_versions() {
        let dir = tempfile::tempdir().unwrap();
        port(dir.path(), Some(r#"{"version": "0.2.0"}"#));
        let warning = verdict(dir.path(), "0.6.7", true).expect("a warning");
        assert!(warning.contains("0.2.0") && warning.contains("0.6.7"), "{warning}");
    }

    #[test]
    fn a_matching_or_newer_port_is_quiet() {
        let dir = tempfile::tempdir().unwrap();
        port(dir.path(), Some(r#"{"version": "0.6.7"}"#));
        assert_eq!(verdict(dir.path(), "0.6.7", true), None);
        fs::write(dir.path().join(FILE), r#"{"version": "0.7.0"}"#).unwrap();
        assert_eq!(verdict(dir.path(), "0.6.7", true), None);
    }

    #[test]
    fn a_port_without_the_file_is_older_than_any_launcher() {
        let dir = tempfile::tempdir().unwrap();
        port(dir.path(), None);
        let warning = verdict(dir.path(), "0.6.7", true).expect("a warning");
        assert!(warning.contains("0.6.7"), "{warning}");
        // Garbled contents count as absent.
        fs::write(dir.path().join(FILE), "not json").unwrap();
        assert!(verdict(dir.path(), "0.6.7", true).is_some());
    }

    #[test]
    fn a_source_checkout_and_an_empty_folder_are_not_warned_about() {
        let dir = tempfile::tempdir().unwrap();
        port(dir.path(), None);
        assert_eq!(verdict(dir.path(), "0.6.7", false), None);
        let empty = tempfile::tempdir().unwrap();
        assert_eq!(verdict(empty.path(), "0.6.7", true), None);
    }
}
