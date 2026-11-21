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

/// A string a core from 0.3.0 on carries: the cache-less pipeline retry that keeps an AMD card
/// from stopping with `Failed to create graphics pipeline`. Ports packaged before `port.json`
/// existed are told apart by it, so a current port is not nagged about a missing file.
const PIPELINE_RETRY: &[u8] = b"retrying without caches";

/// The version the installed port declares, when it declares one.
pub fn declared(port_dir: &Path) -> Option<String> {
    let text = fs::read_to_string(port_dir.join(FILE)).ok()?;
    let json: serde_json::Value = serde_json::from_str(&text).ok()?;
    json.get("version")?.as_str().map(str::to_owned)
}

fn probe_path(port_dir: &Path) -> std::path::PathBuf {
    let name = if cfg!(windows) { "sanctuary-probe.exe" } else { "sanctuary-probe" };
    port_dir.join("bin").join(name)
}

/// Whether this core has the AMD pipeline retry, read out of the binary itself.
fn has_pipeline_retry(port_dir: &Path) -> bool {
    fs::read(probe_path(port_dir))
        .is_ok_and(|bytes| bytes.windows(PIPELINE_RETRY.len()).any(|window| window == PIPELINE_RETRY))
}

/// Whether `port_dir` holds a port at all, rather than an empty folder waiting for one.
fn holds_a_port(port_dir: &Path) -> bool {
    probe_path(port_dir).is_file() || port_dir.join("run.py").is_file() || port_dir.join("run.sh").is_file()
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
        // No declared version: a port packaged before port.json existed. Only warn when its core
        // is missing the fix that keeps an AMD card running - otherwise it is simply current.
        None if has_pipeline_retry(port_dir) => None,
        None => Some(format!(
            "This port is older than this launcher ({mine}) and lacks the graphics pipeline \
             retry that keeps an AMD card running. Install the full port package \
             (SanctuaryPort-windows.zip)."
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

    /// A port folder: a probe binary (with or without the pipeline-retry marker) and, when
    /// given, the version it declares.
    fn port(dir: &Path, json: Option<&str>, retry_marker: bool) {
        fs::create_dir_all(dir.join("bin")).unwrap();
        let probe = probe_path(dir);
        fs::write(&probe, if retry_marker { PIPELINE_RETRY } else { b"old core" }).unwrap();
        if let Some(json) = json {
            fs::write(dir.join(FILE), json).unwrap();
        }
    }

    #[test]
    fn an_older_port_is_named_with_both_versions() {
        let dir = tempfile::tempdir().unwrap();
        port(dir.path(), Some(r#"{"version": "0.2.0"}"#), true);
        let warning = verdict(dir.path(), "0.6.7", true).expect("a warning");
        assert!(warning.contains("0.2.0") && warning.contains("0.6.7"), "{warning}");
    }

    #[test]
    fn a_matching_or_newer_port_is_quiet() {
        let dir = tempfile::tempdir().unwrap();
        port(dir.path(), Some(r#"{"version": "0.6.7"}"#), true);
        assert_eq!(verdict(dir.path(), "0.6.7", true), None);
        fs::write(dir.path().join(FILE), r#"{"version": "0.7.0"}"#).unwrap();
        assert_eq!(verdict(dir.path(), "0.6.7", true), None);
    }

    #[test]
    fn a_port_before_port_json_is_judged_by_its_core() {
        // A current port packaged before the file existed: the retry marker keeps it quiet.
        let current = tempfile::tempdir().unwrap();
        port(current.path(), None, true);
        assert_eq!(verdict(current.path(), "0.6.7", true), None);
        // An old core without it is exactly what stopped the game on an AMD card.
        let old = tempfile::tempdir().unwrap();
        port(old.path(), None, false);
        let warning = verdict(old.path(), "0.6.7", true).expect("a warning");
        assert!(warning.contains("0.6.7") && warning.contains("AMD"), "{warning}");
        // Garbled contents count as absent.
        fs::write(old.path().join(FILE), "not json").unwrap();
        assert!(verdict(old.path(), "0.6.7", true).is_some());
    }

    #[test]
    fn a_source_checkout_and_an_empty_folder_are_not_warned_about() {
        let dir = tempfile::tempdir().unwrap();
        port(dir.path(), None, false);
        assert_eq!(verdict(dir.path(), "0.6.7", false), None);
        let empty = tempfile::tempdir().unwrap();
        assert_eq!(verdict(empty.path(), "0.6.7", true), None);
    }
}
