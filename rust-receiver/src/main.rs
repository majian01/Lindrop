use md5::{Digest, Md5};
use reqwest::{Client, Method, RequestBuilder, Url};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::HashSet;
use std::error::Error;
use std::io::{self, BufRead, BufWriter, Write};
use std::net::{IpAddr, Ipv4Addr};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use tempfile::NamedTempFile;

mod server;

type Result<T> = std::result::Result<T, Box<dyn Error>>;

#[derive(Deserialize)]
struct Receive {
    target: Ipv4Addr,
    source: Option<Ipv4Addr>,
    #[serde(default = "standard_port")]
    port: u16,
    user: String,
    task: u32,
    folder: PathBuf,
}

fn standard_port() -> u16 {
    9999
}

#[derive(Deserialize)]
struct Manifest {
    data: Option<Vec<FileInfo>>,
}

#[derive(Deserialize)]
struct FileInfo {
    id: Value,
    name: Option<String>,
    size: Option<Value>,
    md5: Option<String>,
}

struct Session {
    client: Client,
    base: Url,
    user: String,
}

impl Session {
    fn new(input: &Receive) -> Result<Self> {
        // The native phone service uses its own certificate. Limit this client
        // to the Wi-Fi Direct endpoint supplied by the existing BLE handshake.
        let client = Client::builder()
            .local_address(input.source.map(IpAddr::V4))
            .https_only(true)
            .danger_accept_invalid_certs(true)
            .danger_accept_invalid_hostnames(true)
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .http1_only()
            .connect_timeout(Duration::from_secs(4))
            .read_timeout(Duration::from_secs(15))
            .build()?;
        let base = Url::parse(&format!("https://{}:{}/api/1.0/", input.target, input.port))?;
        Ok(Self {
            client,
            base,
            user: input.user.clone(),
        })
    }

    fn request(&self, method: Method, route: &str, task: u32) -> RequestBuilder {
        self.client
            .request(
                method,
                self.base.join(route).expect("static protocol route"),
            )
            .query(&[("user", &self.user), ("task", &task.to_string())])
    }

    async fn manifest(&self, task: u32) -> Result<Vec<FileInfo>> {
        for attempt in 0..3 {
            let result = self
                .request(Method::GET, "fileinfo", task)
                .timeout(Duration::from_secs(4))
                .send()
                .await;
            match result {
                Ok(response) => {
                    let manifest: Manifest = response.error_for_status()?.json().await?;
                    return Ok(manifest.data.unwrap_or_default());
                }
                Err(error) if attempt < 2 && (error.is_connect() || error.is_timeout()) => {
                    log(&format!("File service unavailable, retrying: {error}"));
                    tokio::time::sleep(Duration::from_millis(500)).await;
                }
                Err(error) => return Err(error.into()),
            }
        }
        unreachable!("the final attempt always returns")
    }

    fn post(&self, route: &str, task: u32, data: Value) -> RequestBuilder {
        self.request(Method::POST, route, task)
            .json(&json!({"status": 0, "data": data}))
    }
}

fn number(value: &Value) -> Option<u64> {
    value.as_u64().or_else(|| value.as_str()?.parse().ok())
}

fn filename(value: &str) -> String {
    let name: String = value
        .chars()
        .map(|c| {
            if c.is_control() || r#"<>:"/\|?*"#.contains(c) {
                '_'
            } else {
                c
            }
        })
        .collect();
    let name = name.trim().trim_end_matches('.');
    if name.is_empty() {
        "file".into()
    } else {
        name.into()
    }
}

fn install_file(mut file: NamedTempFile, folder: &Path, task: u32, name: &str) -> Result<PathBuf> {
    for suffix in 0.. {
        let prefix = if suffix == 0 {
            format!("{task:04x}")
        } else {
            format!("{task:04x}_{suffix}")
        };
        let path = folder.join(format!("{prefix}_{name}"));
        match file.persist_noclobber(&path) {
            Ok(_) => return Ok(path),
            Err(error) if error.error.kind() == io::ErrorKind::AlreadyExists => file = error.file,
            Err(error) => return Err(error.error.into()),
        }
    }
    unreachable!()
}

async fn receive_file(
    session: &Session,
    input: &Receive,
    task: u32,
    info: FileInfo,
    paths: &mut Vec<PathBuf>,
) -> Result<()> {
    let id = info
        .id
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| info.id.to_string());
    let name = filename(info.name.as_deref().unwrap_or("file"));
    let expected = info
        .size
        .as_ref()
        .map(|size| number(size).ok_or("Invalid file size"))
        .transpose()?;
    let _ = session
        .post(
            "report",
            task,
            json!({"code": 3, "id": id, "dstname": name,"dstpath": ""}),
        )
        .timeout(Duration::from_secs(2))
        .send()
        .await;

    let mut file = NamedTempFile::new_in(&input.folder)?;
    let timer = Instant::now();
    let mut response = session
        .request(Method::GET, "file", task)
        .query(&[("id", &id)])
        .send()
        .await?
        .error_for_status()?;
    let mut writer = BufWriter::with_capacity(256 * 1024, file.as_file_mut());
    let mut sum = Md5::new();
    let mut size = 0_u64;
    let mut last_report = Instant::now();
    let mut last_ui = Instant::now();
    let mut progress: Option<tokio::task::JoinHandle<()>> = None;
    while let Some(chunk) = response.chunk().await? {
        size += chunk.len() as u64;
        if expected.is_some_and(|limit| size > limit) {
            return Err("Download is larger than the announced size".into());
        }
        writer.write_all(&chunk)?;
        sum.update(&chunk);
        if last_ui.elapsed() >= Duration::from_millis(250) {
            last_ui = Instant::now();
            println!(
                "{}",
                json!({"kind": "progress", "name": name, "current": size,
                "total": expected.unwrap_or(0), "speed": size as f64 / timer.elapsed().as_secs_f64().max(0.001)})
            );
        }
        if last_report.elapsed() >= Duration::from_secs(2)
            && progress
                .as_ref()
                .is_none_or(|pending| pending.is_finished())
        {
            last_report = Instant::now();
            let request = session
                .post(
                    "report",
                    task,
                    json!({"code": 4, "id": id,
                "cur": size, "total": expected.unwrap_or(0), "dstname": name}),
                )
                .timeout(Duration::from_secs(2));
            progress = Some(tokio::spawn(async move {
                let _ = request.send().await;
            }));
        }
    }
    writer.flush()?;
    drop(writer);
    let elapsed = timer.elapsed().as_secs_f64().max(0.001);
    if let Some(progress) = progress {
        progress.abort();
    }
    let digest = format!("{:x}", sum.finalize());
    if expected.is_some_and(|value| value != size) {
        return Err(format!("Size mismatch: received {size}, expected {expected:?}").into());
    }
    if info
        .md5
        .as_deref()
        .is_some_and(|value| !value.is_empty() && !value.eq_ignore_ascii_case(&digest))
    {
        return Err("File checksum mismatch".into());
    }
    let path = install_file(file, &input.folder, input.task, &name)?;
    println!(
        "{}",
        json!({"kind": "progress", "name": name, "current": size,
        "total": size, "speed": size as f64 / elapsed})
    );
    paths.push(path);
    log(&format!(
        "Rust download: {size} bytes in {elapsed:.3} s, {:.2} MB/s",
        size as f64 / elapsed / 1e6
    ));
    if let Err(error) = session
        .post(
            "fileverify",
            task,
            json!({"code": 5, "id": id,
        "md5": digest, "dstname": name, "dstpath": ""}),
        )
        .send()
        .await
        .and_then(|response| response.error_for_status())
    {
        log(&format!("Phone did not confirm checksum: {error}"));
    }
    Ok(())
}

async fn receive(input: Receive, paths: &mut Vec<PathBuf>) -> Result<()> {
    let session = Session::new(&input)?;
    let mut seen = HashSet::new();
    let mut task = input.task;
    while task != 0 && seen.insert(task) {
        let files = session.manifest(task).await?;
        if files.is_empty() {
            break;
        }
        for file in files {
            receive_file(&session, &input, task, file, paths).await?;
        }
        let result = async {
            session
                .post("taskcomplete", task, json!({}))
                .send()
                .await?
                .error_for_status()?
                .json::<Value>()
                .await
        }
        .await;
        match result {
            Ok(reply) => {
                let Some(next) = number(&reply["data"]["nexttaskid"])
                    .and_then(|value| u32::try_from(value).ok())
                else {
                    break;
                };
                task = next;
            }
            Err(error) => {
                log(&format!("Phone did not confirm task completion: {error}"));
                break;
            }
        }
    }
    Ok(())
}

fn log(text: &str) {
    println!("{}", json!({"kind": "log", "text": text}));
}

fn run() -> Result<Vec<PathBuf>> {
    let line = io::stdin()
        .lock()
        .lines()
        .next()
        .ok_or("Missing receive request")??;
    let input = serde_json::from_str(&line)?;
    let (stop, stopped) = tokio::sync::oneshot::channel();
    std::thread::spawn(move || {
        for line in io::stdin()
            .lock()
            .lines()
            .map_while(std::result::Result::ok)
        {
            if line == "stop" {
                let _ = stop.send(());
                break;
            }
        }
    });
    let mut paths = Vec::new();
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?
        .block_on(async {
            tokio::select! {
                result = receive(input, &mut paths) => result,
                _ = async {
                    if stopped.await.is_err() {
                        std::future::pending::<()>().await;
                    }
                } => Ok(()),
            }
        })?;
    Ok(paths)
}

fn main() {
    let result = if std::env::args().nth(1).as_deref() == Some("--serve") {
        server::run()
    } else {
        run()
    };
    match result {
        Ok(paths) => println!("{}", json!({"kind": "done", "files": paths})),
        Err(error) => {
            println!("{}", json!({"kind": "error", "text": error.to_string()}));
            std::process::exit(1);
        }
    }
}
