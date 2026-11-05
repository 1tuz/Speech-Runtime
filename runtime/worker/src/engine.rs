//! One inference thread per model, with a two-lane priority queue in front of each.
//!
//! wavo serialises calls inside one `Model` (its GPU arena and the Neural Engine program are
//! shared), so a model gets exactly one thread. Interactive jobs jump ahead of queued batch jobs;
//! an inference already running cannot be preempted, since wavo has no cancellation, so a cancel
//! is honoured before a job starts and discards the result if it arrives later.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Instant;

use wavo::{Backend, Model, Options};

use crate::protocol::Priority;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Meta {
    Client(u64),
    Session {
        session: String,
        utterance: u64,
        last_voice_ms: u64,
        start_ms: u64,
        end_ms: u64,
        detected_ms: u64,
        audio_ms: u64,
        /// Why the utterance ended: `silence`, `max_utterance` or `client_end`.
        end_reason: &'static str,
    },
}

#[derive(Debug, Clone)]
pub enum Response {
    Loaded { model: String, device: String, load_ms: u64 },
    Unloaded { model: String },
    Text { text: String, asr_ms: u64, queue_ms: u64, tokens: Vec<TokenOut> },
    Failed { message: String },
    Cancelled,
}

#[derive(Debug, Clone)]
pub struct TokenOut {
    pub text: String,
    pub start_ms: u32,
}

pub struct EngineMsg {
    pub job: u64,
    pub meta: Meta,
    pub response: Response,
}

pub struct Job {
    pub id: u64,
    pub cancelled: Arc<AtomicBool>,
    pub meta: Meta,
    pub kind: Kind,
    /// When submit() enqueued this job; queue_ms is measured from here.
    pub queued_at: Instant,
}

pub enum Kind {
    Transcribe { pcm: Vec<f32>, language: Option<String>, window_ms: Option<u32> },
    Load { path: PathBuf, backend: Option<String> },
    Unload,
}

struct Queues {
    interactive: VecDeque<Job>,
    batch: VecDeque<Job>,
    /// Job the inference thread is running, not yet answered.
    running: Option<(u64, Instant)>,
    stop: bool,
}

pub struct ModelHandle {
    model: String,
    queues: Arc<(Mutex<Queues>, Condvar)>,
    next_id: Arc<AtomicU64>,
    loaded: Arc<AtomicBool>,
    unloading: Arc<AtomicBool>,
    /// Jobs handed to the inference thread but not answered yet.
    inflight: Arc<AtomicU64>,
    device: Arc<Mutex<Option<String>>>,
    done: Arc<AtomicU64>,
    failed: Arc<AtomicU64>,
    started: Instant,
}

impl ModelHandle {
    /// Starts the inference thread; it loads the model on the first `Load` job.
    pub fn spawn(model: String, results: Sender<EngineMsg>) -> Self {
        let model_name = model.clone();
        let queues = Arc::new((
            Mutex::new(Queues {
                interactive: VecDeque::new(),
                batch: VecDeque::new(),
                running: None,
                stop: false,
            }),
            Condvar::new(),
        ));
        let next_id = Arc::new(AtomicU64::new(1));
        let loaded = Arc::new(AtomicBool::new(false));
        let unloading = Arc::new(AtomicBool::new(false));
        let inflight = Arc::new(AtomicU64::new(0));
        let device: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
        let done = Arc::new(AtomicU64::new(0));
        let failed = Arc::new(AtomicU64::new(0));

        {
            let queues = Arc::clone(&queues);
            let loaded = Arc::clone(&loaded);
            let device = Arc::clone(&device);
            let done = Arc::clone(&done);
            let failed = Arc::clone(&failed);
            let unloading = Arc::clone(&unloading);
            let inflight = Arc::clone(&inflight);
            let results = results.clone();
            thread::Builder::new()
                .name(format!("model-{model}"))
                .spawn(move || {
                    let mut held: Option<Model> = None;
                    loop {
                        // Pull a job under the lock, park its id in `running` and stamp the
                        // queue wait; unload sees the lane as busy until the job is answered.
                        let (job, queue_ms) = {
                            let (lock, cv) = &*queues;
                            let mut q = lock.lock().unwrap();
                            loop {
                                if q.stop {
                                    return;
                                }
                                let job = q
                                    .interactive
                                    .pop_front()
                                    .or_else(|| q.batch.pop_front());
                                if let Some(job) = job {
                                    let queue_ms = job.queued_at.elapsed().as_millis() as u64;
                                    q.running = Some((job.id, job.queued_at));
                                    break (job, queue_ms);
                                }
                                q = cv.wait(q).unwrap();
                            }
                        };
                        inflight.fetch_add(1, Ordering::SeqCst);
                        let response = run_with_cancel_gate(&job.cancelled, || {
                            run(
                                &model_name,
                                &job.kind,
                                &mut held,
                                &done,
                                &failed,
                                &loaded,
                                &device,
                                queue_ms,
                            )
                        });
                        inflight.fetch_sub(1, Ordering::SeqCst);
                        {
                            let (lock, _) = &*queues;
                            let mut q = lock.lock().unwrap();
                            q.running = None;
                            if matches!(job.kind, Kind::Unload) {
                                unloading.store(false, Ordering::SeqCst);
                            }
                        }
                        let _ = results.send(EngineMsg { job: job.id, meta: job.meta, response });
                    }
                })
                .expect("spawn model thread");
        }
        Self {
            model,
            queues,
            next_id,
            loaded,
            unloading,
            inflight,
            device,
            done,
            failed,
            started: Instant::now(),
        }
    }

    /// Enqueues a job unless the model is draining for an unload.
    /// Returns the job id and its cancel flag.
    pub fn submit(
        &self,
        kind: Kind,
        priority: Priority,
        meta: Meta,
    ) -> Result<(u64, Arc<AtomicBool>), String> {
        let cancelled = Arc::new(AtomicBool::new(false));
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let job = Job { id, cancelled: Arc::clone(&cancelled), meta, kind, queued_at: Instant::now() };
        let (lock, cv) = &*self.queues;
        let mut q = lock.lock().unwrap();
        if self.unloading.load(Ordering::SeqCst) {
            return Err(format!("model {:?} is unloading", self.model));
        }
        match priority {
            Priority::Interactive => q.interactive.push_back(job),
            Priority::Batch => q.batch.push_back(job),
        }
        cv.notify_one();
        Ok((id, cancelled))
    }

    /// Requests an unload: under the queue lock refuses when jobs are queued or
    /// a job is running, else marks the model unloading and enqueues Unload in front.
    pub fn request_unload(&self, caller: Option<u64>) -> Result<(), String> {
        let (lock, cv) = &*self.queues;
        let mut q = lock.lock().unwrap();
        if !q.interactive.is_empty() || !q.batch.is_empty() || q.running.is_some() {
            return Err(format!("model {:?} is in use", self.model));
        }
        self.unloading.store(true, Ordering::SeqCst);
        let job_id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let job = Job {
            id: job_id,
            cancelled: Arc::new(AtomicBool::new(false)),
            meta: Meta::Client(caller.unwrap_or(0)),
            kind: Kind::Unload,
            queued_at: Instant::now(),
        };
        q.interactive.push_front(job);
        cv.notify_one();
        Ok(())
    }

    /// Jobs handed to the inference thread but not answered yet.
    pub fn inflight(&self) -> u64 {
        self.inflight.load(Ordering::SeqCst)
    }

    pub fn is_loaded(&self) -> bool {
        self.loaded.load(Ordering::SeqCst)
    }

    pub fn device(&self) -> Option<String> {
        self.device.lock().unwrap().clone()
    }

    pub fn depth(&self) -> (usize, usize) {
        let (lock, _) = &*self.queues;
        let q = lock.lock().unwrap();
        (q.interactive.len(), q.batch.len())
    }

    pub fn counters(&self) -> (u64, u64, u64) {
        (self.done.load(Ordering::SeqCst), self.failed.load(Ordering::SeqCst), self.started.elapsed().as_secs())
    }

    pub fn shutdown(&self) {
        let (lock, cv) = &*self.queues;
        let mut q = lock.lock().unwrap();
        q.stop = true;
        cv.notify_all();
    }
}

#[allow(clippy::too_many_arguments)]
fn run(
    model_name: &str,
    kind: &Kind,
    held: &mut Option<Model>,
    done: &AtomicU64,
    failed: &AtomicU64,
    loaded: &AtomicBool,
    device: &Mutex<Option<String>>,
    queue_ms: u64,
) -> Response {
    match kind {
        Kind::Load { path, backend } => {
            let backend = match backend.as_deref() {
                Some("gpu") => Backend::Gpu,
                Some("ane") => Backend::NeuralEngine,
                _ => Backend::Auto,
            };
            let started = Instant::now();
            match Model::load_with(path, backend) {
                Ok(m) => {
                    let dev = m.device().to_string();
                    *device.lock().unwrap() = Some(dev.clone());
                    *held = Some(m);
                    loaded.store(true, Ordering::SeqCst);
                    Response::Loaded {
                        model: model_name.to_string(),
                        device: dev,
                        load_ms: started.elapsed().as_millis() as u64,
                    }
                }
                Err(e) => {
                    failed.fetch_add(1, Ordering::SeqCst);
                    Response::Failed { message: format!("load: {e}") }
                }
            }
        }
        Kind::Unload => {
            *held = None;
            loaded.store(false, Ordering::SeqCst);
            *device.lock().unwrap() = None;
            Response::Unloaded { model: model_name.to_string() }
        }
        Kind::Transcribe { pcm, language, window_ms } => {
            let Some(m) = held.as_ref() else {
                return Response::Failed { message: "model not loaded".into() };
            };
            let started = Instant::now();
            let mut options = Options::default();
            options.language = language.clone().filter(|_| m.takes_language());
            let outcome = match window_ms {
                Some(window) => {
                    options.window_ms = Some(*window);
                    m.transcribe_long(pcm, &options, |_| std::ops::ControlFlow::Continue(()))
                        .map_err(|e| e.to_string())
                }
                None => m.transcribe_with(pcm, &options).map_err(|e| e.to_string()),
            };
            match outcome {
                Ok(t) => {
                    done.fetch_add(1, Ordering::SeqCst);
                    Response::Text {
                        text: t.text,
                        asr_ms: started.elapsed().as_millis() as u64,
                        queue_ms,
                        tokens: t
                            .tokens
                            .iter()
                            .map(|tok| TokenOut { text: tok.piece.clone(), start_ms: tok.start_ms })
                            .collect(),
                    }
                }
                Err(message) => {
                    failed.fetch_add(1, Ordering::SeqCst);
                    Response::Failed { message: format!("transcribe: {message}") }
                }
            }
        }
    }
}

/// Both cancel gates around one inference call, in the order the worker applies them.
///
/// The pre-run gate avoids starting work for an already-cancelled job; the post-run gate
/// catches a cancel that landed while `run` was in flight, since wavo cannot abort
/// inference (RESEARCH 2.5). Keeping the pair in one function is what makes the wiring
/// testable: a fake `run` can flip the flag between the two checks.
fn run_with_cancel_gate<F>(cancelled: &AtomicBool, run: F) -> Response
where
    F: FnOnce() -> Response,
{
    if cancelled.load(Ordering::SeqCst) {
        return Response::Cancelled;
    }
    let response = run();
    finalize_response(cancelled.load(Ordering::SeqCst), response)
}

/// Decides what the router sees when a job answers: a cancel that landed while
/// inference was running drops the result (wavo has no abort), except an
/// unload ack, which its caller still needs.
pub fn finalize_response(cancelled: bool, response: Response) -> Response {
    if cancelled && !matches!(response, Response::Unloaded { .. }) {
        Response::Cancelled
    } else {
        response
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text() -> Response {
        Response::Text { text: "hi".into(), asr_ms: 1, queue_ms: 0, tokens: Vec::new() }
    }

    #[test]
    fn text_becomes_cancelled_when_the_flag_landed_after_run() {
        assert!(matches!(finalize_response(true, text()), Response::Cancelled));
    }

    /// The defect RESEARCH 2.5 names: a cancel must not leave a final behind. This drives the
    /// gate pair, not the helper alone, so deleting the post-run re-check turns it red.
    #[test]
    fn cancel_landing_during_run_suppresses_the_final() {
        let cancelled = AtomicBool::new(false);
        let response = run_with_cancel_gate(&cancelled, || {
            cancelled.store(true, Ordering::SeqCst); // arrives mid-inference
            text()
        });
        assert!(matches!(response, Response::Cancelled), "late cancel let a final through");
    }

    #[test]
    fn already_cancelled_job_never_runs() {
        let cancelled = AtomicBool::new(true);
        let response = run_with_cancel_gate(&cancelled, || panic!("run must not start"));
        assert!(matches!(response, Response::Cancelled));
    }

    #[test]
    fn unload_ack_survives_a_late_cancel() {
        let cancelled = AtomicBool::new(false);
        let response = run_with_cancel_gate(&cancelled, || {
            cancelled.store(true, Ordering::SeqCst);
            Response::Unloaded { model: "m".into() }
        });
        assert!(matches!(response, Response::Unloaded { .. }), "unload caller must still see its ack");
    }

    #[test]
    fn text_is_preserved_when_not_cancelled() {
        let response = Response::Text {
            text: "hi".into(),
            asr_ms: 1,
            queue_ms: 0,
            tokens: Vec::new(),
        };
        match finalize_response(false, response) {
            Response::Text { text, .. } => assert_eq!(text, "hi"),
            other => panic!("expected text, got {other:?}"),
        }
    }

    #[test]
    fn unload_ack_is_preserved_even_when_cancelled() {
        let response = Response::Unloaded { model: "m".into() };
        match finalize_response(true, response) {
            Response::Unloaded { model } => assert_eq!(model, "m"),
            other => panic!("expected unloaded, got {other:?}"),
        }
    }
}
