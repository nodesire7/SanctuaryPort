//! Time a bounded prefix of the actual ZIP, then retry verified downloads in speed order.
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
    let mut sample = Vec::with_capacity(SAMPLE);
    let read = response
        .into_body()
        .into_reader()
        .take(SAMPLE as u64)
        .read_to_end(&mut sample);
    // Slow connections can still provide a useful sample before the time limit.
    if read.is_err_and(|error| error.kind() != std::io::ErrorKind::TimedOut) {
        return None;
    }
    if !sample.starts_with(b"PK\x03\x04") || sample.len() < 1024 {
        return None;
    }
    Some(sample.len() as f64 / start.elapsed().as_secs_f64().max(0.000001))
}

fn ranked(urls: &[String]) -> Vec<&str> {
    std::thread::scope(|scope| {
        let workers: Vec<_> = urls
            .iter()
            .map(|url| (url.as_str(), scope.spawn(move || probe(url))))
            .collect();
        let mut speeds: Vec<_> = workers
            .into_iter()
            .map(|(url, worker)| (url, worker.join().ok().flatten().unwrap_or_default()))
            .collect();
        speeds.sort_by(|a, b| b.1.total_cmp(&a.1));
        speeds.into_iter().map(|(url, _)| url).collect()
    })
}

pub(super) fn download_zip(
    urls: &[String],
    target: &Path,
    digest: &str,
    progress: &mut dyn FnMut(u8),
) -> Result<(), UpdateError> {
    let mut errors = Vec::new();
    for url in ranked(urls) {
        match download(url, target, Some(digest), progress) {
            Ok(()) => return Ok(()),
            Err(error) => errors.push(format!("{url}: {error}")),
        }
    }
    Err(UpdateError::Http(errors.join("\n")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::sync::mpsc;
    use std::thread;

    // A real HTTP exchange for each probe and full download. Delays apply only to probes.
    fn server(body: Vec<u8>, delay_ms: u64) -> (String, mpsc::Receiver<bool>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/update.zip", listener.local_addr().unwrap());
        let (tx, requests) = mpsc::channel();
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(4);
            while Instant::now() < deadline {
                let Ok((mut socket, _)) = listener.accept() else {
                    thread::sleep(Duration::from_millis(2));
                    continue;
                };
                socket.set_nonblocking(false).unwrap();
                let headers: Vec<_> = BufReader::new(&socket)
                    .lines()
                    .map_while(Result::ok)
                    .take_while(|s| !s.is_empty())
                    .collect();
                let probe = headers
                    .iter()
                    .any(|s| s.to_ascii_lowercase().starts_with("range:"));
                if tx.send(probe).is_err() {
                    break;
                }
                if probe {
                    thread::sleep(Duration::from_millis(delay_ms));
                }
                write!(
                    socket,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
                .unwrap();
                socket.write_all(&body).unwrap();
                if !probe {
                    break;
                }
            }
        });
        (url, requests)
    }

    #[test]
    fn fastest_valid_sample_is_tried_first_and_corruption_retries() {
        let mut good = vec![0u8; 64 * 1024];
        good[..4].copy_from_slice(b"PK\x03\x04");
        let mut bad = good.clone();
        bad[1024] = 1;
        let (slow, slow_hits) = server(good.clone(), 350);
        let (fast, fast_hits) = server(bad, 0);
        let (html, html_hits) = server(vec![b'X'; 8192], 0);
        let temp = tempfile::tempdir().unwrap();
        let file = temp.path().join("result");
        let digest = format!("{:x}", Sha256::digest(&good));
        let urls = [slow, html, fast];
        download_zip(&urls, &file, &digest, &mut |_| {}).unwrap();
        assert_eq!(std::fs::read(file).unwrap(), good);
        assert_eq!(fast_hits.try_iter().collect::<Vec<_>>(), [true, false]);
        assert_eq!(slow_hits.try_iter().collect::<Vec<_>>(), [true, false]);
        assert_eq!(html_hits.try_iter().collect::<Vec<_>>(), [true]);
    }

    #[test]
    fn a_failed_download_keeps_the_installed_file() {
        let (url, _hits) = server(vec![0; 2048], 0);
        let temp = tempfile::tempdir().unwrap();
        let file = temp.path().join("result");
        std::fs::write(&file, b"installed").unwrap();
        assert!(download_zip(&[url], &file, &"0".repeat(64), &mut |_| {}).is_err());
        assert_eq!(std::fs::read(file).unwrap(), b"installed");
    }

    #[test]
    fn installer_updates_only_the_executable() {
        let source = tempfile::tempdir().unwrap();
        let target = tempfile::tempdir().unwrap();
        for (dir, value) in [(source.path(), b"new"), (target.path(), b"old")] {
            for file in [
                "SanctuaryPort.exe",
                "settings.json",
                "bin/sanctuary-probe.exe",
                "game/localized",
                "assets/trophies/Diablo_III.json",
                "user/save",
            ] {
                let path = dir.join(file);
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(path, value).unwrap();
            }
        }
        super::super::install_launcher(source.path(), target.path(), 1, &mut || {}).unwrap();
        assert_eq!(
            std::fs::read(target.path().join("SanctuaryPort.exe")).unwrap(),
            b"new"
        );
        for file in [
            "settings.json",
            "bin/sanctuary-probe.exe",
            "game/localized",
            "assets/trophies/Diablo_III.json",
            "user/save",
        ] {
            assert_eq!(std::fs::read(target.path().join(file)).unwrap(), b"old");
        }
    }

    #[test]
    #[ignore = "downloads the current public zh-CN release"]
    fn published_launcher_downloads_and_validates() {
        let list = super::super::list_releases(true).unwrap();
        let release = list.first().expect("published Windows launcher");
        println!("Checking {}", release.url);
        let digest = release.sha256.as_deref().expect("published SHA-256");
        let urls = candidates(&release.url);
        assert_eq!(urls.len(), 3);
        let temp = tempfile::tempdir().unwrap();
        let target = temp.path().join("package.zip");
        download_zip(&urls, &target, digest, &mut |_| {}).unwrap();
        let mut zip = zip::ZipArchive::new(std::fs::File::open(target).unwrap()).unwrap();
        assert_eq!(zip.len(), 1);
        assert_eq!(zip.by_index(0).unwrap().name().unwrap(), "SanctuaryPort.exe");
    }
}
