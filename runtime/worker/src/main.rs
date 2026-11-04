//! `sr-worker`: a long-lived ASR worker. Reads line JSON on stdin, writes line JSON on stdout.
//!
//! Every request carries an `id` (or a `session`); replies repeat it. The worker owns models, the
//! per-model priority queues and dictation endpointing; the daemon owns the HTTP API, sessions
//! from the client's point of view, authentication and the worker's supervision.

use std::collections::HashMap;
use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::thread;

use earshot::Detector;
use serde_json::json;

use sr_worker::endpoint::{Endpointer, Event, FRAME};
use sr_worker::engine::{EngineMsg, Kind, Meta, ModelHandle, Response};
use sr_worker::pcm;
use sr_worker::protocol::{Priority, Reply, Request};

struct Session {
    model: String,
    endpointer: Endpointer,
    detector: Box<Detector>,
    /// Samples that did not fill a 256-sample frame yet.
    carry: Vec<f32>,
    utterance: u64,
    jobs: Vec<Arc<AtomicBool>>,
}

struct Worker {
    models: HashMap<String, ModelHandle>,
    /// Client request id -> cancel flag of its job.
    client_jobs: HashMap<u64, Arc<AtomicBool>>,
    sessions: HashMap<String, Session>,
    results: Sender<EngineMsg>,
    out: Arc<Mutex<std::io::Stdout>>,
    next_job: AtomicU64,
}

impl Worker {
    fn send(&self, reply: &Reply) {
        let mut out = self.out.lock().unwrap();
        let line = serde_json::to_string(reply).unwrap_or_else(|e| format!("{{\"type\":\"error\",\"message\":\"{e}\"}}"));
        let _ = writeln!(out, "{line}");
        let _ = out.flush();
    }

    fn handle(&mut self, req: Request) {
        match req {
            Request::Ping { id } => self.ok(id, json!({"pong": true})),
            Request::Stats { id } => {
                let models: Vec<_> = self
                    .models
                    .iter()
                    .map(|(name, m)| {
                        let (done, failed, uptime) = m.counters();
                        let (hi, lo) = m.depth();
                        json!({
                            "model": name,
                            "loaded": m.is_loaded(),
                            "device": m.device(),
                            "queued_interactive": hi,
                            "queued_batch": lo,
                            "done": done,
                            "failed": failed,
                            "uptime_s": uptime,
                        })
                    })
                    .collect();
                self.ok(id, json!({"models": models, "sessions": self.sessions.len()}));
            }
            Request::Load { id, model, path, backend } => self.load(id, model, path, backend),
            Request::Unload { id, model } => {
                let Some(handle) = self.models.get(&model) else {
                    self.err(id, format!("model {model:?} is not loaded"));
                    return;
                };
                handle.submit(Kind::Unload, Priority::Batch, Meta::Client(id.unwrap_or(0)));
            }
            Request::Transcribe { id, model, pcm: data, language, window_ms, priority } => {
                let Some(handle) = self.models.get(&model) else {
                    self.err(id, format!("model {model:?} is not loaded; send load first"));
                    return;
                };
                let pcm = match pcm::decode_f32(&data) {
                    Ok(p) => p,
                    Err(e) => return self.err(id, e),
                };
                if pcm.is_empty() {
                    return self.err(id, "empty audio".to_string());
                }
                let request_id = id.unwrap_or_else(|| self.next_job.fetch_add(1, Ordering::SeqCst));
                let (_, flag) = handle.submit(
                    Kind::Transcribe { pcm, language, window_ms },
                    Priority::parse(priority.as_deref()),
                    Meta::Client(request_id),
                );
                self.client_jobs.insert(request_id, flag);
            }
            Request::Cancel { id, job } => {
                let cancelled = self.client_jobs.remove(&job).map(|flag| {
                    flag.store(true, Ordering::SeqCst);
                    true
                });
                self.ok(id, json!({"cancelled": cancelled.unwrap_or(false)}));
            }
            Request::SessionOpen { id, session, model, config } => {
                if !self.models.contains_key(&model) {
                    self.err(id, format!("model {model:?} is not loaded; send load first"));
                    return;
                }
                let cfg = config.unwrap_or_default();
                self.sessions.insert(session.clone(), Session {
                    model,
                    endpointer: Endpointer::new(cfg),
                    detector: Detector::default_boxed(),
                    carry: Vec::new(),
                    utterance: 0,
                    jobs: Vec::new(),
                });
                self.ok(id, json!({"session_open": session}));
            }
            Request::SessionAudio { id, session, pcm: data } => {
                let pcm = match pcm::decode_f32(&data) {
                    Ok(p) => p,
                    Err(e) => return self.err(id, e),
                };
                self.session_audio(&session, &pcm);
                self.ok(id, json!({"session_audio": session, "samples": pcm.len()}));
            }
            Request::SessionEnd { id, session } => self.session_end(id, &session),
            Request::SessionCancel { id, session } => {
                if let Some(s) = self.sessions.remove(&session) {
                    for flag in s.jobs {
                        flag.store(true, Ordering::SeqCst);
                    }
                    self.send(&Reply::Event {
                        session: session.clone(),
                        event: "cancelled".into(),
                        data: json!({}),
                    });
                    self.ok(id, json!({"session_cancel": session}));
                } else {
                    self.err(id, format!("no session {session:?}"));
                }
            }
        }
    }

    fn load(&mut self, id: Option<u64>, model: String, path: String, backend: Option<String>) {
        if let Some(handle) = self.models.get(&model) {
            if handle.is_loaded() {
                self.ok(id, json!({
                    "model": model,
                    "device": handle.device(),
                    "cached": true,
                }));
                return;
            }
        }
        let handle = self
            .models
            .entry(model.clone())
            .or_insert_with(|| ModelHandle::spawn(model.clone(), self.results.clone()));
        handle.submit(
            Kind::Load { path: PathBuf::from(path), backend },
            Priority::Interactive,
            Meta::Client(id.unwrap_or(0)),
        );
    }

    fn session_audio(&mut self, id: &str, pcm: &[f32]) {
        if !self.sessions.contains_key(id) {
            return self.err(None, format!("no session {id:?}"));
        }
        let mut events = Vec::new();
        let (mut utterance, model, language);
        {
            let session = self.sessions.get_mut(id).expect("checked above");
            let mut buf = Vec::with_capacity(session.carry.len() + pcm.len());
            buf.extend_from_slice(&session.carry);
            buf.extend_from_slice(pcm);
            let frames = buf.len() / FRAME;
            for index in 0..frames {
                let frame = &buf[index * FRAME..(index + 1) * FRAME];
                let score = session.detector.predict_f32(frame);
                if let Some(event) = session.endpointer.feed(frame, score) {
                    events.push(event);
                }
            }
            session.carry = buf[frames * FRAME..].to_vec();
            utterance = session.utterance;
            model = session.model.clone();
            language = session.endpointer.config().language.clone();
        }

        for event in events {
            match event {
                Event::SpeechStart { at_ms } => self.send(&Reply::Event {
                    session: id.to_string(),
                    event: "speech_start".into(),
                    data: json!({"at_ms": at_ms}),
                }),
                Event::Utterance { pcm, start_ms, end_ms, last_voice_ms } => {
                    let Some(handle) = self.models.get(&model) else {
                        self.err(None, format!("model {model:?} not loaded"));
                        continue;
                    };
                    let audio_ms = pcm.len() as u64 * 1000 / 16_000;
                    let (_, flag) = handle.submit(
                        Kind::Transcribe { pcm, language: language.clone(), window_ms: None },
                        Priority::Interactive,
                        Meta::Session { session: id.to_string(), utterance, last_voice_ms },
                    );
                    if let Some(session) = self.sessions.get_mut(id) {
                        session.utterance += 1;
                        session.jobs.push(flag);
                    }
                    self.send(&Reply::Event {
                        session: id.to_string(),
                        event: "utterance".into(),
                        data: json!({
                            "utterance": utterance,
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "last_voice_ms": last_voice_ms,
                            "audio_ms": audio_ms,
                        }),
                    });
                    utterance += 1;
                }
            }
        }
    }

    fn session_end(&mut self, id: Option<u64>, session: &str) {
        let Some(entry) = self.sessions.get_mut(session) else {
            return self.err(id, format!("no session {session:?}"));
        };
        let pending = entry.jobs.len() as u64;
        entry.detector.reset();
        self.ok(id, json!({"session_end": session, "pending": pending}));
    }

    fn ok(&self, id: Option<u64>, result: serde_json::Value) {
        self.send(&Reply::Ok { id, result });
    }

    fn err(&self, id: Option<u64>, message: String) {
        self.send(&Reply::Error { id, message });
    }
}

fn main() {
    let out = Arc::new(Mutex::new(std::io::stdout()));
    let (tx, rx) = channel::<EngineMsg>();

    let worker = Arc::new(Mutex::new(Worker {
        models: HashMap::new(),
        client_jobs: HashMap::new(),
        sessions: HashMap::new(),
        results: tx,
        out: Arc::clone(&out),
        next_job: AtomicU64::new(1_000_000),
    }));

    // Router: turns engine replies into protocol replies and session events.
    {
        let worker = Arc::clone(&worker);
        thread::spawn(move || {
            while let Ok(msg) = rx.recv() {
                let mut w = worker.lock().unwrap();
                match msg.meta {
                    Meta::Client(id) if matches!(
                        msg.response,
                        Response::Loaded { .. } | Response::Unloaded { .. }
                    ) =>
                    {
                        match msg.response {
                            Response::Loaded { model, device, load_ms } => w.send(&Reply::Ok {
                                id: (id != 0).then_some(id),
                                result: json!({"loaded": model, "device": device, "load_ms": load_ms}),
                            }),
                            Response::Unloaded { model } => w.send(&Reply::Ok {
                                id: (id != 0).then_some(id),
                                result: json!({"unloaded": model}),
                            }),
                            _ => unreachable!(),
                        }
                    }
                    Meta::Client(id) => {
                        w.client_jobs.remove(&id);
                        match msg.response {
                            Response::Text { text, asr_ms, tokens } => w.send(&Reply::Ok {
                                id: Some(id),
                                result: json!({
                                    "text": text,
                                    "asr_ms": asr_ms,
                                    "tokens": tokens.iter().map(|t| json!({"text": t.text, "start_ms": t.start_ms})).collect::<Vec<_>>(),
                                }),
                            }),
                            Response::Cancelled => w.send(&Reply::Error { id: Some(id), message: "cancelled".into() }),
                            Response::Failed { message } => w.send(&Reply::Error { id: Some(id), message }),
                            other => w.send(&Reply::Error { id: Some(id), message: format!("unexpected: {other:?}") }),
                        }
                    }
                    Meta::Session { session, utterance, last_voice_ms } => {
                        match msg.response {
                            Response::Text { text, asr_ms, tokens } => w.send(&Reply::Event {
                                session,
                                event: "final".into(),
                                data: json!({
                                    "utterance": utterance,
                                    "text": text,
                                    "asr_ms": asr_ms,
                                    "last_voice_ms": last_voice_ms,
                                    "tokens": tokens.iter().map(|t| json!({"text": t.text, "start_ms": t.start_ms})).collect::<Vec<_>>(),
                                }),
                            }),
                            Response::Cancelled => w.send(&Reply::Event {
                                session,
                                event: "cancelled".into(),
                                data: json!({"utterance": utterance}),
                            }),
                            Response::Failed { message } => w.send(&Reply::Event {
                                session,
                                event: "error".into(),
                                data: json!({"utterance": utterance, "message": message}),
                            }),
                            other => w.send(&Reply::Event {
                                session,
                                event: "error".into(),
                                data: json!({"message": format!("unexpected: {other:?}")}),
                            }),
                        }
                    }
                }
            }
        });
    }

    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match serde_json::from_str::<Request>(line) {
            Ok(req) => {
                let mut w = worker.lock().unwrap();
                w.handle(req);
            }
            Err(e) => {
                let w = worker.lock().unwrap();
                w.err(None, format!("bad request: {e}"));
            }
        }
    }
}
