//! Time a bounded prefix of the actual ZIP, then retry verified downloads in speed order.
//!
//! GitHub's download hosts are slow or unreachable from some networks (China especially while
//! the same files are served much faster by a mirror in front of them). Every candidate is
//! probed with a ranged request for a small prefix, the measured throughput decides the order,
//! and each attempt still has to pass the published SHA-256 before it counts.
//!
//! This file is written into the launcher sources at build time (`build/apply.py`); it is not
//! part of upstream.
use super::{download, http_error, release, UpdateError, RELEASES_PAGE, USER_AGENT};
use std::io::Read;
use std::path::Path;
use std::time::{Duration, Instant};
use crate::core::release::ReleaseId;

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
    let mut measured = std::thread::scope(|scope| {
        let probes: Vec<_> = urls.iter()
            .map(|url| (url.as_str(), scope.spawn(move || probe(url))))
            .collect();
        probes.into_iter()
            .filter_map(|(url, worker)| worker.join().ok().flatten().map(|speed| (url, speed)))
            .collect::<Vec<_>>()
    });
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

const ASSET: &str = "SanctuaryPort-launcher-zh-CN.zip";
const DOWNLOAD: &str = "https://github.com/nodesire7/SanctuaryPort/releases/download";

/// The latest Windows release, read from the public release page's redirect.
///
/// The anonymous API (`RELEASES_API`) is rate limited per address and unreachable from some
/// networks; `github.com/.../releases/latest` answers with a redirect to the same tag. The
/// package's checksum is fetched beside the package like a normal release, so a fallback
/// install is verified the same way.
pub(super) fn latest_release() -> Result<release::Release, UpdateError> {
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .max_redirects(0)
        .timeout_global(Some(Duration::from_secs(12)))
        .build()
        .into();
    let response = agent
        .head(RELEASES_PAGE)
        .header("User-Agent", USER_AGENT)
        .call()
        .map_err(http_error)?;
    let page = response
        .headers()
        .get("Location")
        .and_then(|value| value.to_str().ok())
        .ok_or_else(|| UpdateError::Http("GitHub did not name a latest release".into()))?;
    let tag = page.rsplit('/').next().unwrap_or_default();
    if !tag.starts_with("zh-CN-v")
        || !tag
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'.' || byte == b'-')
    {
        return Err(UpdateError::Http(format!("unexpected release tag: {tag}")));
    }
    let id = ReleaseId::parse(tag, false)
        .ok_or_else(|| UpdateError::Http(format!("unreadable release tag: {tag}")))?;
    Ok(release::Release {
        id,
        published: String::new(),
        url: format!("{DOWNLOAD}/{tag}/{ASSET}"),
        page: page.to_owned(),
    })
}
/// Preserve the upstream cache, including its backoff, when the public API is unavailable.
pub(super) fn list_with_fallback(
    cache: &mut super::ReleaseCache, windows: bool,
) -> Result<Vec<release::Release>, UpdateError> {
    let api = super::releases_api();
    let now = Instant::now();
    let result = cache.list(now, |etag| super::fetch_list(&api, etag),
                            |text| super::parse_list(text, windows));
    match result {
        Err(api_error) if windows && api == super::RELEASES_API => {
            let latest = latest_release().map_err(|_| api_error)?;
            let list = vec![latest];
            cache.list = Some(list.clone());
            cache.etag = None;
            cache.fetched = Some(now);
            Ok(list)
        }
        other => other,
    }
}
