//! Time a bounded prefix of the actual ZIP, then retry verified downloads in speed order.
//!
//! GitHub's download hosts are slow or unreachable from some networks (China especially while
//! the same files are served much faster by a mirror in front of them). Every candidate is
//! probed with a ranged request for a small prefix, the measured throughput decides the order,
//! and each attempt still has to pass the published SHA-256 before it counts.
//!
//! This file is written into the launcher sources at build time (`build/apply.py`); it is not
//! part of upstream.
use super::{download, UpdateError, USER_AGENT};
use std::io::Read;
use std::path::Path;
use std::time::{Duration, Instant};

const SAMPLE: usize = 256 * 1024;
const MIRRORS: [&str; 2] = ["https://ghfast.top", "https://gh-proxy.org"];

pub(super) fn candidates(url: &str) -> Vec<String> {
    let mut urls = vec![url.to_owned()];
    if url.starts_with("https://github.com/nodesire7/SanctuaryPort/releases/download/") {
        urls.extend(MIRRORS.iter().map(|base| format!("{base}/{url}")));
    }
    urls
}

fn probe(url: &str) -> Option<f64> {
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(5)))
        .build()
        .into();
    let start = Instant::now();
    let response = agent
        .get(url)
        .header("User-Agent", USER_AGENT)
        .header("Range", format!("bytes=0-{}", SAMPLE - 1))
        .header("Accept-Encoding", "identity")
        .call()
        .ok()?;
    let mut body = Vec::with_capacity(SAMPLE);
    response
        .into_body()
        .into_reader()
        .take(SAMPLE as u64)
        .read_to_end(&mut body)
        .ok()?;
    // An HTML error page from a mirror is not a candidate however fast it arrived.
    if body.len() < 4 || &body[..4] != b"PK\x03\x04" {
        return None;
    }
    let seconds = start.elapsed().as_secs_f64();
    (seconds > 0.0).then(|| body.len() as f64 / seconds)
}

fn ranked(urls: &[String]) -> Vec<&str> {
    let mut measured: Vec<(&str, f64)> = urls
        .iter()
        .map(|url| (url.as_str(), probe(url)))
        .filter_map(|(url, speed)| speed.map(|speed| (url, speed)))
        .collect();
    measured.sort_by(|a, b| b.1.total_cmp(&a.1));
    let mut order: Vec<&str> = measured.into_iter().map(|(url, _)| url).collect();
    // A candidate that did not answer the probe is still worth a full attempt, last.
    let unprobed: Vec<&str> = urls
        .iter()
        .map(String::as_str)
        .filter(|url| !order.contains(url))
        .collect();
    order.extend(unprobed);
    order
}

pub(super) fn download_zip(
    urls: &[String],
    target: &Path,
    digest: Option<&str>,
    progress: &mut dyn FnMut(u8),
) -> Result<(), UpdateError> {
    let mut errors = Vec::new();
    for url in ranked(urls) {
        match download(url, target, digest, progress) {
            Ok(()) => return Ok(()),
            Err(error) => errors.push(format!("{url}: {error}")),
        }
    }
    Err(UpdateError::Http(errors.join("\n")))
}
