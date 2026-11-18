//! Independent implementation of the observed file-service wire contract.
//! No vendor binary, certificate, private key or decompiled implementation is used.

use bytes::Bytes;
use http_body_util::{BodyExt, Full, Limited, combinators::UnsyncBoxBody};
use hyper::body::{Body, Frame, Incoming, SizeHint};
use hyper::header::{ACCEPT_RANGES, CONTENT_LENGTH, CONTENT_RANGE, CONTENT_TYPE, RANGE};
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::{TokioIo, TokioTimer};
use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};
use std::convert::Infallible;
use std::io::{self, BufRead, Write};
use std::net::{IpAddr, Ipv4Addr};
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll};
use std::time::{Duration, Instant};
use tokio::io::{AsyncRead, AsyncSeekExt, ReadBuf};
use tokio::net::TcpListener;
use tokio::sync::Semaphore;
use tokio::task::JoinSet;

type ResponseBody = UnsyncBoxBody<Bytes, io::Error>;
type ServiceResult<T> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Deserialize)]
struct Configuration {
    bind: Ipv4Addr,
    port: u16,
    user: String,
    task: u32,
    identity: Vec<u8>,
    identity_password: String,
    files: Vec<SharedFile>,
    #[serde(default)]
    send_buffer_bytes: Option<usize>,
}

#[derive(Clone, Deserialize)]
struct SharedFile {
    id: String,
    path: PathBuf,
    name: String,
    size: u64,
    #[serde(rename = "mimeType")]
    mime_type: String,
    md5: String,
}

#[derive(Default)]
struct Progress {
    peer: Option<IpAddr>,
    verified: HashSet<String>,
    sent: HashMap<String, u64>,
    started: Option<Instant>,
    terminal: bool,
}

struct Session {
    user: String,
    task: String,
    files: HashMap<String, SharedFile>,
    manifest: Value,
    total: u64,
    progress: Mutex<Progress>,
}

fn emit(value: Value) {
    println!("{value}");
    let _ = io::stdout().flush();
}

impl Session {
    fn fail(&self, text: String) {
        let mut progress = self.progress.lock().unwrap();
        if !progress.terminal {
            progress.terminal = true;
            emit(json!({"kind":"failed","text":text}));
        }
    }
    fn new(configuration: &Configuration) -> ServiceResult<Self> {
        if configuration.send_buffer_bytes.is_some_and(|size| !(65536..=4*1024*1024).contains(&size)) {
            return Err("Invalid TCP send buffer size".into());
        }
        if configuration.bind.is_unspecified() || configuration.user.is_empty()
            || configuration.task == 0 || configuration.files.is_empty()
            || configuration.files.len() > 250
        {
            return Err("Invalid transfer configuration".into());
        }
        let mut files = HashMap::new();
        let mut manifest = Vec::new();
        let mut total = 0_u64;
        for file in &configuration.files {
            if file.id.is_empty() || file.md5.len() != 32
                || !file.md5.bytes().all(|b| b.is_ascii_hexdigit())
                || !file.path.is_file() || file.path.metadata()?.len() != file.size
                || files.insert(file.id.clone(), file.clone()).is_some()
            {
                return Err("Invalid or changed shared file".into());
            }
            total = total.checked_add(file.size).ok_or("Transfer size overflow")?;
            manifest.push(json!({"id":file.id,"name":file.name,"size":file.size,
                "mimeType":file.mime_type,"md5":file.md5,"type":0}));
        }
        Ok(Self { user: configuration.user.clone(), task: configuration.task.to_string(), files,
            manifest: json!({"status":0,"data":manifest}), total, progress: Mutex::default() })
    }

    fn authorize(&self, query: &str, peer: IpAddr, manifest: bool) -> Option<HashMap<String, String>> {
        let url = reqwest::Url::parse(&format!("https://localhost/?{query}")).ok()?;
        let mut fields = HashMap::new();
        for (name, value) in url.query_pairs() {
            if fields.insert(name.into_owned(), value.into_owned()).is_some() { return None; }
        }
        if fields.get("user") != Some(&self.user) || fields.get("task") != Some(&self.task) {
            return None;
        }
        let mut progress = self.progress.lock().unwrap();
        match progress.peer {
            Some(current) if current != peer => return None,
            None if manifest => {
                progress.peer = Some(peer);
                emit(json!({"kind":"http_started"}));
            }
            None => return None,
            _ => {}
        }
        Some(fields)
    }

    fn sent(&self, file: &SharedFile, current: u64) {
        let mut progress = self.progress.lock().unwrap();
        let started = *progress.started.get_or_insert_with(Instant::now);
        let previous = progress.sent.entry(file.id.clone()).or_default();
        *previous = (*previous).max(current.min(file.size));
        let done: u64 = progress.sent.values().sum();
        emit(json!({"kind":"progress","name":file.name,"current":done,"total":self.total,
            "speed":done as f64 / started.elapsed().as_secs_f64().max(0.001)}));
    }
}

fn response(status: StatusCode, value: Value) -> Response<ResponseBody> {
    let bytes = value.to_string();
    Response::builder().status(status).header(CONTENT_TYPE, "application/json")
        .header(CONTENT_LENGTH, bytes.len())
        .body(Full::new(Bytes::from(bytes)).map_err(|never| match never {}).boxed_unsync()).unwrap()
}

fn receipt(status: &str, data: Value) -> Response<ResponseBody> {
    response(StatusCode::OK, json!({"status":status,"data":data}))
}

fn error(status: StatusCode) -> Response<ResponseBody> {
    response(status, json!({"status":"1","data":{}}))
}

fn byte_range(header: Option<&str>, size: u64) -> Option<(u64, u64, bool)> {
    let Some(header) = header else { return Some((0, size, false)); };
    let range = header.strip_prefix("bytes=")?;
    let (first, last) = range.split_once('-')?;
    if range.contains(',') || size == 0 { return None; }
    if first.is_empty() {
        let count: u64 = last.parse().ok()?;
        if count == 0 { return None; }
        Some((size.saturating_sub(count), size.min(count), true))
    } else {
        let start: u64 = first.parse().ok()?;
        if start >= size { return None; }
        let end = if last.is_empty() { size - 1 } else { last.parse::<u64>().ok()?.min(size - 1) };
        if end < start { return None; }
        Some((start, end - start + 1, true))
    }
}

struct FileBody {
    file: tokio::fs::File,
    info: SharedFile,
    session: Arc<Session>,
    remaining: u64,
    offset: u64,
    buffer: Vec<u8>,
    last: Instant,
}

impl Body for FileBody {
    type Data = Bytes;
    type Error = io::Error;

    fn poll_frame(self: Pin<&mut Self>, context: &mut Context<'_>)
        -> Poll<Option<Result<Frame<Self::Data>, Self::Error>>>
    {
        let this = self.get_mut();
        if this.remaining == 0 { return Poll::Ready(None); }
        let count = this.remaining.min(this.buffer.len() as u64) as usize;
        let mut buffer = ReadBuf::new(&mut this.buffer[..count]);
        match Pin::new(&mut this.file).poll_read(context, &mut buffer) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(Err(error)) => {
                this.session.fail(format!("Cannot read shared file: {error}"));
                this.remaining = 0;
                Poll::Ready(Some(Err(error)))
            },
            Poll::Ready(Ok(())) => {
                let read = buffer.filled().len() as u64;
                if read == 0 {
                    this.remaining = 0;
                    this.session.fail("Shared file ended before its announced size".into());
                    return Poll::Ready(Some(Err(io::ErrorKind::UnexpectedEof.into())));
                }
                let bytes = Bytes::copy_from_slice(buffer.filled());
                this.remaining -= read;
                this.offset += read;
                if this.remaining == 0 || this.last.elapsed() >= Duration::from_millis(250) {
                    this.session.sent(&this.info, this.offset);
                    this.last = Instant::now();
                }
                Poll::Ready(Some(Ok(Frame::data(bytes))))
            }
        }
    }

    fn is_end_stream(&self) -> bool { self.remaining == 0 }
    fn size_hint(&self) -> SizeHint { SizeHint::with_exact(self.remaining) }
}

async fn route(mut request: Request<Incoming>, peer: IpAddr, session: Arc<Session>)
    -> ServiceResult<Response<ResponseBody>>
{
    let path = request.uri().path().to_owned();
    let is_manifest = request.method() == Method::GET && path == "/api/1.0/fileinfo";
    let Some(fields) = session.authorize(request.uri().query().unwrap_or(""), peer, is_manifest) else {
        return Ok(error(StatusCode::NOT_FOUND));
    };
    if is_manifest { return Ok(response(StatusCode::OK, session.manifest.clone())); }
    if matches!(*request.method(), Method::GET | Method::HEAD) && path == "/api/1.0/file" {
        let Some(file) = fields.get("id").and_then(|id| session.files.get(id)).cloned() else {
            return Ok(error(StatusCode::BAD_REQUEST));
        };
        let range = request.headers().get(RANGE).and_then(|value| value.to_str().ok());
        let Some((offset, length, partial)) = byte_range(range, file.size) else {
            let mut reply = error(StatusCode::RANGE_NOT_SATISFIABLE);
            reply.headers_mut().insert(CONTENT_RANGE, format!("bytes */{}", file.size).parse()?);
            return Ok(reply);
        };
        let mut opened = match tokio::fs::File::open(&file.path).await {
            Ok(file) => file,
            Err(error_text) => {
                session.fail(format!("Cannot open shared file: {error_text}"));
                return Ok(error(StatusCode::INTERNAL_SERVER_ERROR));
            }
        };
        if opened.metadata().await?.len() != file.size {
            session.fail("Shared file changed after it was announced".into());
            return Ok(error(StatusCode::CONFLICT));
        }
        opened.seek(std::io::SeekFrom::Start(offset)).await?;
        let mut reply = Response::builder()
            .status(if partial { StatusCode::PARTIAL_CONTENT } else { StatusCode::OK })
            .header(CONTENT_TYPE, &file.mime_type).header(CONTENT_LENGTH, length)
            .header(ACCEPT_RANGES, "bytes");
        if partial {
            reply = reply.header(CONTENT_RANGE, format!("bytes {}-{}/{}", offset, offset + length - 1, file.size));
        }
        let body = if request.method() == Method::HEAD || length == 0 {
            Full::new(Bytes::new()).map_err(|never| match never {}).boxed_unsync()
        } else {
            session.progress.lock().unwrap().started.get_or_insert_with(Instant::now);
            FileBody { file: opened, info: file, session, remaining: length, offset,
                buffer: vec![0; 256 * 1024], last: Instant::now() }.boxed_unsync()
        };
        return Ok(reply.body(body)?);
    }
    if request.method() != Method::POST { return Ok(error(StatusCode::NOT_FOUND)); }
    if !matches!(path.as_str(), "/api/1.0/report" | "/api/1.0/fileverify" | "/api/1.0/taskcomplete") {
        return Ok(error(StatusCode::NOT_FOUND));
    }
    let bytes = tokio::time::timeout(Duration::from_secs(5), Limited::new(request.body_mut(), 65536).collect()).await;
    let value: Value = match bytes {
        Ok(Ok(body)) => match serde_json::from_slice(&body.to_bytes()) {
            Ok(value) => value,
            Err(_) => return Ok(error(StatusCode::BAD_REQUEST)),
        },
        _ => return Ok(error(StatusCode::BAD_REQUEST)),
    };
    if !value["data"].is_object() { return Ok(error(StatusCode::BAD_REQUEST)); }
    let success = value["status"] == 0 || value["status"] == "0";
    let data = &value["data"];
    if path.ends_with("/report") {
        return Ok(Response::builder().header(CONTENT_LENGTH, 0)
            .body(Full::new(Bytes::new()).map_err(|never| match never {}).boxed_unsync())?);
    }
    if path.ends_with("/fileverify") {
        let info = data["id"].as_str().and_then(|id| session.files.get(id));
        let good = success && info.is_some_and(|file|
            data["md5"].as_str().is_some_and(|md5| file.md5.eq_ignore_ascii_case(md5)));
        if good {
            let info = info.unwrap();
            session.progress.lock().unwrap().verified.insert(info.id.clone());
            session.sent(info, info.size);
        } else if let Some(info) = info {
            session.progress.lock().unwrap().verified.remove(&info.id);
        }
        return Ok(receipt(if good { "0" } else { "9" },
            json!({"reason":if good {"ok"} else {"md5 verification failed"}})));
    }
    let mut progress = session.progress.lock().unwrap();
    let good = success && progress.verified.len() == session.files.len()
        && data["reason"].as_str().is_none_or(|reason| reason == "success");
    if !progress.terminal {
        progress.terminal = true;
        emit(json!({"kind":if good {"complete"} else {"failed"}}));
    }
    Ok(receipt("0", json!({"action":"0","nexttaskid":"","nexttaskfilecnt":"0"})))
}

async fn serve(configuration: Configuration, mut stopped: tokio::sync::oneshot::Receiver<()>)
    -> ServiceResult<()>
{
    let session = Arc::new(Session::new(&configuration)?);
    let identity = native_tls::Identity::from_pkcs12(&configuration.identity, &configuration.identity_password)?;
    let mut tls = native_tls::TlsAcceptor::builder(identity);
    tls.min_protocol_version(Some(native_tls::Protocol::Tlsv12));
    let tls = tokio_native_tls::TlsAcceptor::from(tls.build()?);
    let listener = TcpListener::bind((configuration.bind, configuration.port)).await?;
    emit(json!({"kind":"ready","port":listener.local_addr()?.port()}));
    let slots = Arc::new(Semaphore::new(16));
    let mut connections = JoinSet::new();
    let (shutdown, _) = tokio::sync::watch::channel(false);
    loop {
        tokio::select! {
            _ = &mut stopped => break,
            Some(_) = connections.join_next(), if !connections.is_empty() => {},
            result = listener.accept() => {
                let (socket, peer) = result?;
                let Ok(slot) = slots.clone().try_acquire_owned() else { continue; };
                socket.set_nodelay(true)?;
                let options = socket2::SockRef::from(&socket);
                if let Some(size) = configuration.send_buffer_bytes {
                    if let Err(error) = options.set_send_buffer_size(size) {
                        emit(json!({"kind":"log","text":format!("TCP send buffer setting unavailable: {error}")}));
                    }
                }
                let sent = options.send_buffer_size().unwrap_or(0);
                let received = options.recv_buffer_size().unwrap_or(0);
                emit(json!({"kind":"log","text":format!("TCP buffers: send={sent}, receive={received}"),
                    "send_buffer_bytes":sent,"receive_buffer_bytes":received}));
                let tls = tls.clone();
                let session = session.clone();
                let mut stopping = shutdown.subscribe();
                connections.spawn(async move {
                    let _slot = slot;
                    let stream = match tokio::time::timeout(Duration::from_secs(5), tls.accept(socket)).await {
                        Ok(Ok(stream)) => stream,
                        _ => return,
                    };
                    let service = hyper::service::service_fn(move |request| {
                        let session = session.clone();
                        async move {
                            let reply = route(request, peer.ip(), session).await.unwrap_or_else(|error_text| {
                                emit(json!({"kind":"log","text":format!("HTTPS request failed: {error_text}")}));
                                error(StatusCode::INTERNAL_SERVER_ERROR)
                            });
                            Ok::<_, Infallible>(reply)
                        }
                    });
                    let mut builder = hyper::server::conn::http1::Builder::new();
                    builder.timer(TokioTimer::new()).header_read_timeout(Duration::from_secs(10));
                    let connection = builder.serve_connection(TokioIo::new(stream), service);
                    tokio::pin!(connection);
                    tokio::select! {
                        _ = &mut connection => {},
                        _ = stopping.changed() => {
                            connection.as_mut().graceful_shutdown();
                            let _ = tokio::time::timeout(Duration::from_millis(400), &mut connection).await;
                        }
                    }
                });
            }
        }
    }
    drop(listener);
    let _ = shutdown.send(true);
    let _ = tokio::time::timeout(Duration::from_millis(500), async {
        while connections.join_next().await.is_some() {}
    }).await;
    connections.abort_all();
    Ok(())
}

pub(crate) fn run() -> crate::Result<Vec<PathBuf>> {
    let line = io::stdin().lock().lines().next().ok_or("Missing serve request")??;
    let configuration = serde_json::from_str(&line)?;
    let (stop, stopped) = tokio::sync::oneshot::channel();
    std::thread::spawn(move || {
        for line in io::stdin().lock().lines().map_while(Result::ok) {
            if line == "stop" { break; }
        }
        let _ = stop.send(());
    });
    tokio::runtime::Builder::new_current_thread().enable_all().build()?
        .block_on(serve(configuration, stopped)).map_err(|error| error.to_string())?;
    Ok(Vec::new())
}
