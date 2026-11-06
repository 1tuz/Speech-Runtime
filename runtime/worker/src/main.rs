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

use sr_worker::batchsplit;
use sr_worker::endpoint::{Endpointer, Event, FRAME};
use sr_worker::engine::{EngineMsg, Kind, Meta, ModelHandle, Response, TokenOut};
use sr_worker::pcm;
use sr_worker::protocol::{Priority, Reply, Request};
use sr_worker::session::SessionJobs;
/// Default split size for long batch transcriptions: ~1 s of inference per
/// part, half of the 2 s interactive e2e budget under batch load.
const DEFAULT_MAX_CHUNK_MS: u32 = 10_000;

struct Session {
    model: String,
    endpointer: Endpointer,
    detector: Box<Detector>,
    /// Samples that did not fill a 256-sample frame yet.
    carry: Vec<f32>,
    /// Next utterance index handed to the engine and events.
    utterance: u64,
    jobs: SessionJobs,
}

struct Worker {
    models: HashMap<String, ModelHandle>,
    /// Client request id -> cancel flag of its job (or of its part group).
    client_jobs: HashMap<u64, Arc<AtomicBool>>,
    /// Split batch requests being reassembled: group id -> collected parts.
    groups: HashMap<u64, GroupState>,
    /// Next split group id.
    next_group: AtomicU64,
    sessions: HashMap<String, Session>,
    results: Sender<EngineMsg>,
    out: Arc<Mutex<std::io::Stdout>>,
    next_job: AtomicU64,
}

/// Parts of one split batch request awaiting reassembly.
struct GroupState {
    /// Answered parts, indexed by `seq`.
    parts: Vec<Option<Part>>,
    /// Where each part sits in the original audio, ms (token offsets).
    offsets_ms: Vec<u64>,
    request_id: u64,
}

/// One answered part of a split batch request.
#[derive(Clone)]
struct Part {
    text: String,
    asr_ms: u64,
    queue_ms: u64,
    tokens: Vec<TokenOut>,
}

impl GroupState {
    fn new(offsets_ms: Vec<u64>, request_id: u64) -> Self {
        Self { parts: vec![None; offsets_ms.len()], offsets_ms, request_id }
    }

    /// Stores an answered part; true when the group is complete.
    fn store(&mut self, seq: u64, part: Part) -> bool {
        if let Some(slot) = self.parts.get_mut(seq as usize) {
            *slot = Some(part);
        }
        self.parts.iter().all(|p| p.is_some())
    }
}

/// The single reply for a fully answered group: texts joined with a space,
/// token timestamps shifted by each part's offset, summed asr and the worst
/// queue wait (parts run one at a time, so the max is the honest figure).
fn group_reply(state: &GroupState) -> Reply {
    let mut texts: Vec<String> = Vec::new();
    let mut tokens = Vec::new();
    let mut asr_ms = 0u64;
    let mut queue_ms = 0u64;
    let mut part_asr_ms = Vec::new();
    for (index, slot) in state.parts.iter().enumerate() {
        let part = slot.as_ref().expect("complete group");
        if !part.text.is_empty() {
            texts.push(part.text.clone());
        }
        asr_ms += part.asr_ms;
        queue_ms = queue_ms.max(part.queue_ms);
        part_asr_ms.push(part.asr_ms);
        let offset = state.offsets_ms[index];
        for token in &part.tokens {
            tokens.push(json!({"text": token.text, "start_ms": token.start_ms as u64 + offset}));
        }
    }
    Reply::Ok {
        id: Some(state.request_id),
        result: json!({
            "text": texts.join(" "),
            "asr_ms": asr_ms,
            "queue_ms": queue_ms,
            "tokens": tokens,
            "parts": state.parts.len(),
            "part_asr_ms": part_asr_ms,
        }),
    }
}

impl Worker {
    fn send(&self, reply: &Reply) {
        let mut out = self.out.lock().unwrap();
        let line = serde_json::to_string(reply).unwrap_or_else(|e| format!("{{\"type\":\"error\",\"message\":\"{e}\"}}"));
        let _ = writeln!(out, "{line}");
        let _ = out.flush();
    }

    /// Emits `ended` and drops the session once every job answered. Called from
    /// the router after each session-tagged response.
    fn retire_if_drained(&mut self, session: &str) {
        let Some(s) = self.sessions.get(session) else { return };
        if !s.jobs.should_end() {
            return;
        }
        let (utterances, failed) = (s.jobs.submitted(), s.jobs.failed());
        self.send(&Reply::Event {
            session: session.to_string(),
            event: "ended".into(),
            data: json!({"utterances": utterances, "failed": failed}),
        });
        self.sessions.remove(session);
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
                        let (hi, _lo) = m.depth();
                        json!({
                            "model": name,
                            "loaded": m.is_loaded(),
                            "device": m.device(),
                            "queued_interactive": hi,
                            "inflight": m.inflight(),
                            "done": done,
                            "failed": failed,
                            "uptime_s": uptime,
                        })
                    })
                    .collect();
                let ending = self.sessions.values().filter(|s| s.jobs.is_ending()).count();
                self.ok(id, json!({
                    "models": models,
                    "sessions": self.sessions.len(),
                    "ending_sessions": ending,
                    "memory": sr_worker::mem::read_memory().to_json(),
                }));
            }
            Request::Load { id, model, path, backend } => self.load(id, model, path, backend),
            Request::Unload { id, model } => {
                let Some(handle) = self.models.get(&model) else {
                    self.err(id, format!("model {model:?} is not loaded"));
                    return;
                };
                if self.sessions.values().any(|s| s.model == model) {
                    self.err(id, format!("model {model:?} is in use"));
                    return;
                }
                if let Err(e) = handle.request_unload(id) {
                    self.err(id, e);
                }
            }
            Request::Transcribe { id, model, pcm: data, language, window_ms, priority, max_chunk_ms } => {
                if !self.models.contains_key(&model) {
                    return self.err(id, format!("model {model:?} is not loaded; send load first"));
                }
                let (pcm, language, window_ms, priority, chunk_ms, request_id) = {
                    let pcm = match pcm::decode_f32(&data) {
                        Ok(p) => p,
                        Err(e) => {
                            self.err(id, e);
                            return;
                        }
                    };
                    if pcm.is_empty() {
                        self.err(id, "empty audio".to_string());
                        return;
                    }
                    (
                        pcm,
                        language,
                        window_ms,
                        Priority::parse(priority.as_deref()),
                        max_chunk_ms.unwrap_or(DEFAULT_MAX_CHUNK_MS),
                        id.unwrap_or_else(|| self.next_job.fetch_add(1, Ordering::SeqCst)),
                    )
                };
                // Only long batch transcriptions are split: interactive jobs
                // are short by nature and sessions own their own slicing.
                // The parts are computed while `handle` still borrows `self`
                // immutably; the borrow ends before `submit_group` takes the
                // mutable one.
                let parts = if priority == Priority::Batch
                    && window_ms.is_none()
                    && pcm.len() as u64 * 1000 / 16_000 > chunk_ms as u64
                {
                    batchsplit::split_batch_pcm(&pcm, chunk_ms)
                } else {
                    Vec::new()
                };
                if parts.len() > 1 {
                    if let Err(e) = self.submit_group(&model, &parts, &language, request_id) {
                        return self.err(id, e);
                    }
                    return;
                }
                let Some(handle) = self.models.get(&model) else {
                    return self.err(id, format!("model {model:?} is not loaded"));
                };
                match handle.submit(
                    Kind::Transcribe { pcm, language, window_ms },
                    priority,
                    Meta::Client(request_id),
                ) {
                    Ok((_, flag)) => {
                        self.client_jobs.insert(request_id, flag);
                    }
                    Err(e) => self.err(id, e),
                }
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
                    jobs: SessionJobs::new(),
                });
                self.ok(id, json!({"session_open": session}));
            }
            Request::SessionAudio { id, session, pcm: data } => {
                let pcm = match pcm::decode_f32(&data) {
                    Ok(p) => p,
                    Err(e) => return self.err(id, e),
                };
                if self.session_audio(id, &session, &pcm) {
                    self.ok(id, json!({"session_audio": session, "samples": pcm.len()}));
                }
            }
            Request::SessionEnd { id, session } => self.session_end(id, &session),
            Request::SessionCancel { id, session } => {
                let Some(mut s) = self.sessions.remove(&session) else {
                    return self.err(id, format!("no session {session:?}"));
                };
                s.jobs.cancel_all();
                self.send(&Reply::Event {
                    session: session.clone(),
                    event: "cancelled".into(),
                    data: json!({}),
                });
                self.send(&Reply::Event {
                    session: session.clone(),
                    event: "ended".into(),
                    data: json!({"utterances": s.jobs.submitted(), "failed": s.jobs.failed()}),
                });
                self.ok(id, json!({"session_cancel": session}));
            }
        }
    }

    /// Submits the parts of one split batch request: one Batch job each, all
    /// sharing one cancel flag, reassembled by the router into a single reply.
    fn submit_group(
        &mut self,
        model: &str,
        parts: &[batchsplit::Chunk],
        language: &Option<String>,
        request_id: u64,
    ) -> Result<(), String> {
        let Some(handle) = self.models.get(model) else {
            return Err(format!("model {model:?} is not loaded"));
        };
        let group = self.next_group.fetch_add(1, Ordering::SeqCst);
        let offsets_ms: Vec<u64> = parts
            .iter()
            .map(|p| p.offset_samples as u64 * 1000 / 16_000)
            .collect();
        let total = parts.len() as u64;
        let flag = Arc::new(AtomicBool::new(false));
        for (seq, part) in parts.iter().enumerate() {
            handle.submit_with_flag(
                Kind::Transcribe {
                    pcm: part.pcm.clone(),
                    language: language.clone(),
                    window_ms: None,
                },
                Priority::Batch,
                Meta::BatchChunk {
                    group,
                    seq: seq as u64,
                    total,
                    request_id,
                },
                Arc::clone(&flag),
            )?;
        }
        self.groups.insert(group, GroupState::new(offsets_ms, request_id));
        self.client_jobs.insert(request_id, flag);
        Ok(())
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
        if let Err(e) = handle.submit(
            Kind::Load { path: PathBuf::from(path), backend },
            Priority::Interactive,
            Meta::Client(id.unwrap_or(0)),
        ) {
            self.err(id, e);
        }
    }

    /// Returns false when the frame was rejected (no session / ending),
    /// so the caller answers with an error instead of ok.
    fn session_audio(&mut self, req_id: Option<u64>, id: &str, pcm: &[f32]) -> bool {
        let Some(session) = self.sessions.get_mut(id) else {
            self.err(req_id, format!("no session {id:?}"));
            return false;
        };
        if session.jobs.is_ending() {
            self.err(req_id, format!("session {id:?} is ending"));
            return false;
        }
        let mut events = Vec::new();
        let (mut utterance, model, language);
        {
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
                Event::Utterance { pcm, start_ms, end_ms, last_voice_ms, detected_ms, end_reason } => {
                    let audio_ms = pcm.len() as u64 * 1000 / 16_000;
                    let submitted = match self.models.get(&model) {
                        Some(handle) => handle.submit(
                            Kind::Transcribe { pcm, language: language.clone(), window_ms: None },
                            Priority::Interactive,
                            Meta::Session {
                                session: id.to_string(),
                                utterance,
                                last_voice_ms,
                                start_ms,
                                end_ms,
                                detected_ms,
                                audio_ms,
                                end_reason,
                            },
                        ),
                        None => Err(format!("model {model:?} not loaded")),
                    };
                    match submitted {
                        Ok((job, flag)) => {
                            if let Some(session) = self.sessions.get_mut(id) {
                                session.jobs.track(job, flag);
                                session.utterance += 1;
                            }
                        }
                        Err(e) => {
                            if let Some(session) = self.sessions.get_mut(id) {
                                session.jobs.track_failed();
                            }
                            // Exactly one ack/error per request: the error
                            // answers the whole session_audio request.
                            self.err(req_id, e);
                            return false;
                        }
                    }
                    self.send(&Reply::Event {
                        session: id.to_string(),
                        event: "utterance".into(),
                        data: json!({
                            "utterance": utterance,
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "last_voice_ms": last_voice_ms,
                            "detected_ms": detected_ms,
                            "audio_ms": audio_ms,
                            "end_reason": end_reason,
                        }),
                    });
                    utterance += 1;
                }
            }
        }
        true
    }

    fn session_end(&mut self, id: Option<u64>, session: &str) {
        let Some(entry) = self.sessions.get_mut(session) else {
            return self.err(id, format!("no session {session:?}"));
        };
        if entry.jobs.is_ending() {
            // Second end while ending: repeat the ack, no second flush.
            let pending = entry.jobs.pending();
            let (utterances, failed) = (entry.jobs.submitted(), entry.jobs.failed());
            self.ok(id, json!({
                "session_end": session,
                "ending": true,
                "pending": pending,
                "utterances": utterances,
                "failed": failed,
            }));
            return;
        }
        // Flush the carry first: a non-empty leftover becomes one padded frame
        // through feed_tail (with the detector score of that padded frame), so
        // the leftover reaches the utterance exactly once. If feed_tail itself
        // ends the utterance, flush() below has nothing left to end.
        let mut events = Vec::new();
        let carry = std::mem::take(&mut entry.carry);
        if !carry.is_empty() {
            let mut frame = vec![0.0f32; FRAME];
            frame[..carry.len()].copy_from_slice(&carry);
            let score = entry.detector.predict_f32(&frame);
            if let Some(event) = entry.endpointer.feed_tail(&carry, score) {
                events.push(event);
            }
        }
        if let Some(event) = entry.endpointer.flush() {
            events.push(event);
        }
        entry.detector.reset();
        entry.jobs.set_ending();
        let (mut utterance, model, language) = (
            entry.utterance,
            entry.model.clone(),
            entry.endpointer.config().language.clone(),
        );
        // One request gets one reply: if a tail job cannot be queued, the client is told
        // through the error reply below and must not also receive the success ack.
        let mut submit_failed = false;
        for event in events {
            let Event::Utterance { pcm, start_ms, end_ms, last_voice_ms, detected_ms, end_reason } = event else {
                continue; // feed_tail/flush only produce utterances
            };
            let audio_ms = pcm.len() as u64 * 1000 / 16_000;
            let submitted = match self.models.get(&model) {
                Some(handle) => handle.submit(
                    Kind::Transcribe { pcm, language: language.clone(), window_ms: None },
                    Priority::Interactive,
                    Meta::Session {
                        session: session.to_string(),
                        utterance,
                        last_voice_ms,
                        start_ms,
                        end_ms,
                        detected_ms,
                        audio_ms,
                        end_reason,
                    },
                ),
                None => Err(format!("model {model:?} not loaded")),
            };
            match submitted {
                Ok((job, flag)) => {
                    if let Some(entry) = self.sessions.get_mut(session) {
                        entry.jobs.track(job, flag);
                        entry.utterance = utterance + 1;
                    }
                }
                Err(e) => {
                    if let Some(entry) = self.sessions.get_mut(session) {
                        entry.jobs.track_failed();
                    }
                    // The tail was never queued, so there is no `utterance` to announce and no
                    // `final` to wait for; announcing one would leave the client waiting forever.
                    self.err(id, e);
                    submit_failed = true;
                    continue;
                }
            }
            self.send(&Reply::Event {
                session: session.to_string(),
                event: "utterance".into(),
                data: json!({
                    "utterance": utterance,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "last_voice_ms": last_voice_ms,
                    "detected_ms": detected_ms,
                    "audio_ms": audio_ms,
                    "end_reason": end_reason,
                }),
            });
            utterance += 1;
        }
        if submit_failed {
            self.retire_if_drained(session);
            return;
        }
        let (pending, utterances, failed) = {
            let entry = self.sessions.get(session).expect("session still present");
            (entry.jobs.pending(), entry.jobs.submitted(), entry.jobs.failed())
        };
        self.ok(id, json!({
            "session_end": session,
            "ending": true,
            "pending": pending,
            "utterances": utterances,
            "failed": failed,
        }));
        self.retire_if_drained(session);
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
        groups: HashMap::new(),
        next_group: AtomicU64::new(1),
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
                            Response::Text { text, asr_ms, queue_ms, tokens } => w.send(&Reply::Ok {
                                id: Some(id),
                                result: json!({
                                    "text": text,
                                    "asr_ms": asr_ms,
                                    "queue_ms": queue_ms,
                                    "tokens": tokens.iter().map(|t| json!({"text": t.text, "start_ms": t.start_ms})).collect::<Vec<_>>(),
                                }),
                            }),
                            Response::Cancelled => w.send(&Reply::Error { id: Some(id), message: "cancelled".into() }),
                            Response::Failed { message } => w.send(&Reply::Error { id: Some(id), message }),
                            other => w.send(&Reply::Error { id: Some(id), message: format!("unexpected: {other:?}") }),
                        }
                    }
                    Meta::BatchChunk { group, seq, request_id, .. } => {
                        // Unknown group: every part of it already answered
                        // (a failure retired it); drop the straggler.
                        if let Some(state) = w.groups.get_mut(&group) {
                            match msg.response {
                                Response::Text { text, asr_ms, queue_ms, tokens } => {
                                    let part = Part { text, asr_ms, queue_ms, tokens };
                                    if state.store(seq, part) {
                                        let reply = group_reply(state);
                                        w.groups.remove(&group);
                                        w.client_jobs.remove(&request_id);
                                        w.send(&reply);
                                    }
                                }
                                // First failure/cancel answers and retires the
                                // group; stragglers are dropped by the unknown-
                                // group check above (the shared cancel flag
                                // turns them into Cancelled anyway).
                                Response::Failed { message } => {
                                    w.groups.remove(&group);
                                    w.client_jobs.remove(&request_id);
                                    w.send(&Reply::Error { id: Some(request_id), message });
                                }
                                Response::Cancelled => {
                                    w.groups.remove(&group);
                                    w.client_jobs.remove(&request_id);
                                    w.send(&Reply::Error { id: Some(request_id), message: "cancelled".into() });
                                }
                                other => {
                                    let message = format!("unexpected: {other:?}");
                                    w.groups.remove(&group);
                                    w.client_jobs.remove(&request_id);
                                    w.send(&Reply::Error { id: Some(request_id), message });
                                }
                            }
                        }
                    }
                    Meta::Session { session, utterance, last_voice_ms, start_ms, end_ms, detected_ms, audio_ms, end_reason } => {
                        // A job whose session was cancelled/removed, or that
                        // is no longer tracked, must not produce a late final.
                        let job_tracked = w
                            .sessions
                            .get_mut(&session)
                            .map(|s| s.jobs.answer(msg.job))
                            .unwrap_or(false);
                        let session_present = w.sessions.contains_key(&session);
                        for reply in session_replies(
                            session_present,
                            job_tracked,
                            &session,
                            utterance,
                            last_voice_ms,
                            start_ms,
                            end_ms,
                            detected_ms,
                            audio_ms,
                            end_reason,
                            msg.response.clone(),
                        ) {
                            w.send(&reply);
                        }
                        // note_failed only when we actually reported it.
                        if job_tracked {
                            if let Response::Failed { .. } = &msg.response {
                                if let Some(s) = w.sessions.get_mut(&session) {
                                    s.jobs.note_failed();
                                }
                            }
                        }
                        w.retire_if_drained(&session);
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

/// Replies for one session-tagged engine response. Pure decision: no late
/// `final` for a dead session (removed/cancelled) or an untracked job; the
/// response is reported only when the session is still present and the job was
/// still tracked when it answered.
#[allow(clippy::too_many_arguments)]
fn session_replies(
    session_present: bool,
    job_tracked: bool,
    session: &str,
    utterance: u64,
    last_voice_ms: u64,
    start_ms: u64,
    end_ms: u64,
    detected_ms: u64,
    audio_ms: u64,
    end_reason: &str,
    response: Response,
) -> Vec<Reply> {
    if !session_present || !job_tracked {
        return Vec::new();
    }
    let mut replies = Vec::with_capacity(1);
    match response {
        Response::Text { text, asr_ms, queue_ms, tokens } => replies.push(Reply::Event {
            session: session.to_string(),
            event: "final".into(),
            data: json!({
                "utterance": utterance,
                "text": text,
                "asr_ms": asr_ms,
                "queue_ms": queue_ms,
                "audio_ms": audio_ms,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "last_voice_ms": last_voice_ms,
                "detected_ms": detected_ms,
                "end_reason": end_reason,
                "tokens": tokens.iter().map(|t| json!({"text": t.text, "start_ms": t.start_ms})).collect::<Vec<_>>(),
            }),
        }),
        Response::Cancelled => replies.push(Reply::Event {
            session: session.to_string(),
            event: "cancelled".into(),
            data: json!({"utterance": utterance}),
        }),
        Response::Failed { message } => replies.push(Reply::Event {
            session: session.to_string(),
            event: "error".into(),
            data: json!({"utterance": utterance, "message": message}),
        }),
        other => replies.push(Reply::Event {
            session: session.to_string(),
            event: "error".into(),
            data: json!({"message": format!("unexpected: {other:?}")}),
        }),
    }
    replies
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text_response() -> Response {
        Response::Text { text: "hi".into(), asr_ms: 1, queue_ms: 0, tokens: Vec::new() }
    }

    #[test]
    fn untracked_job_produces_no_replies() {
        let replies = session_replies(
            true, false, "s", 1, 100, 0, 100, 100, 100, "silence", text_response(),
        );
        assert!(replies.is_empty(), "no late final for an untracked job");
    }

    #[test]
    fn removed_session_produces_no_replies() {
        let replies = session_replies(
            false, true, "s", 1, 100, 0, 100, 100, 100, "silence", text_response(),
        );
        assert!(replies.is_empty(), "no late final for a removed session");
    }

    #[test]
    fn tracked_job_gets_final_with_end_reason() {
        let replies = session_replies(
            true, true, "s", 1, 100, 0, 100, 100, 100, "silence", text_response(),
        );
        assert_eq!(replies.len(), 1);
        match &replies[0] {
            Reply::Event { event, data, .. } => {
                assert_eq!(event, "final");
                assert_eq!(data["end_reason"], "silence");
                assert_eq!(data["utterance"], 1);
            }
            other => panic!("expected final event, got {other:?}"),
        }
    }

    #[test]
    fn cancelled_and_failed_report_only_when_tracked() {
        let cancelled = session_replies(
            true, true, "s", 2, 100, 0, 100, 100, 100, "silence", Response::Cancelled,
        );
        assert_eq!(cancelled.len(), 1);
        assert!(matches!(
            &cancelled[0],
            Reply::Event { event, .. } if event == "cancelled"
        ));

        let failed = session_replies(
            true, true, "s", 3, 100, 0, 100, 100, 100, "silence",
            Response::Failed { message: "boom".into() },
        );
        assert_eq!(failed.len(), 1);
        assert!(matches!(
            &failed[0],
            Reply::Event { event, .. } if event == "error"
        ));

        let dead = session_replies(
            true, false, "s", 4, 100, 0, 100, 100, 100, "silence",
            Response::Failed { message: "boom".into() },
        );
        assert!(dead.is_empty(), "untracked failures are silent");
    }

    fn part_text(text: &str, asr_ms: u64, queue_ms: u64, token_start: u32) -> Part {
        Part {
            text: text.into(),
            asr_ms,
            queue_ms,
            tokens: vec![TokenOut { text: text.into(), start_ms: token_start }],
        }
    }

    #[test]
    fn group_reply_joins_parts_and_shifts_tokens() {
        let mut state = GroupState::new(vec![0, 10_000], 7);
        assert!(!state.store(1, part_text("world", 5, 40, 0)));
        assert!(state.store(0, part_text("hello", 4, 30, 100)));
        match group_reply(&state) {
            Reply::Ok { id, result } => {
                assert_eq!(id, Some(7));
                assert_eq!(result["text"], "hello world");
                assert_eq!(result["asr_ms"], 9);
                // The max is the honest queue wait for a part group.
                assert_eq!(result["queue_ms"], 40);
                assert_eq!(result["parts"], 2);
                assert_eq!(result["part_asr_ms"][0], 4);
                assert_eq!(result["part_asr_ms"][1], 5);
                let tokens = result["tokens"].as_array().unwrap();
                assert_eq!(tokens[0]["text"], "hello");
                assert_eq!(tokens[0]["start_ms"], 100);
                // Token of the second part, shifted by the 10 s offset.
                assert_eq!(tokens[1]["text"], "world");
                assert_eq!(tokens[1]["start_ms"], 10_000);
            }
            other => panic!("expected ok, got {other:?}"),
        }
    }

    #[test]
    fn group_replies_only_once_all_parts_answered() {
        let mut state = GroupState::new(vec![0, 5_000, 10_000], 3);
        assert!(!state.store(0, part_text("a", 1, 0, 0)));
        assert!(!state.store(2, part_text("c", 1, 0, 0)));
        assert!(state.store(1, part_text("b", 1, 0, 0)));
    }

    #[test]
    fn empty_part_text_does_not_double_spaces() {
        let mut state = GroupState::new(vec![0, 5_000], 1);
        state.store(0, Part { text: String::new(), asr_ms: 1, queue_ms: 0, tokens: Vec::new() });
        state.store(1, part_text("word", 1, 0, 0));
        match group_reply(&state) {
            Reply::Ok { result, .. } => assert_eq!(result["text"], "word"),
            other => panic!("expected ok, got {other:?}"),
        }
    }

    #[test]
    fn cancel_flag_is_shared_by_all_parts() {
        // The router contract: one cancel of a group request must discard every
        // still-queued part, which only holds if they share one flag.
        let flag = Arc::new(AtomicBool::new(false));
        let parts: Vec<Arc<AtomicBool>> = (0..3).map(|_| Arc::clone(&flag)).collect();
        parts[2].store(true, Ordering::SeqCst);
        assert!(parts.iter().all(|p| p.load(Ordering::SeqCst)));
    }
}
