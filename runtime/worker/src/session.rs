//! Per-session job bookkeeping: which engine jobs belong to a session, whether
//! the client asked to end it, and the utterance/failed counters the `ended`
//! event reports. Pure state, no I/O, so the end/drain rules are unit-tested.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

pub struct SessionJobs {
    /// Engine job id -> cancel flag, for jobs not yet answered.
    jobs: HashMap<u64, Arc<AtomicBool>>,
    ending: bool,
    /// Utterances submitted over the session's lifetime.
    submitted: u64,
    /// Jobs that came back `Failed` (or could not be submitted).
    failed: u64,
}

impl Default for SessionJobs {
    fn default() -> Self {
        Self::new()
    }
}

impl SessionJobs {
    pub fn new() -> Self {
        Self {
            jobs: HashMap::new(),
            ending: false,
            submitted: 0,
            failed: 0,
        }
    }

    /// Records a freshly submitted utterance job.
    pub fn track(&mut self, job: u64, flag: Arc<AtomicBool>) {
        self.jobs.insert(job, flag);
        self.submitted += 1;
    }

    /// Drops a job that failed to submit; it still counts as an utterance.
    pub fn track_failed(&mut self) {
        self.submitted += 1;
        self.failed += 1;
    }

    /// Removes an answered job; returns whether it was still tracked.
    /// Every engine response must pass through here (contract: no growth).
    pub fn answer(&mut self, job: u64) -> bool {
        self.jobs.remove(&job).is_some()
    }

    /// Counts a response as failed for the `ended` event.
    pub fn note_failed(&mut self) {
        self.failed += 1;
    }

    /// Marks the session as ending; no new audio is accepted after this.
    pub fn set_ending(&mut self) {
        self.ending = true;
    }

    pub fn is_ending(&self) -> bool {
        self.ending
    }

    pub fn pending(&self) -> u64 {
        self.jobs.len() as u64
    }

    pub fn submitted(&self) -> u64 {
        self.submitted
    }

    pub fn failed(&self) -> u64 {
        self.failed
    }

    /// True once the client ended the session and every job answered.
    pub fn should_end(&self) -> bool {
        self.ending && self.jobs.is_empty()
    }

    /// Flags every in-flight job as cancelled (session_cancel).
    pub fn cancel_all(&mut self) {
        for flag in self.jobs.values() {
            flag.store(true, Ordering::SeqCst);
        }
        self.jobs.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn flag() -> Arc<AtomicBool> {
        Arc::new(AtomicBool::new(false))
    }

    #[test]
    fn end_flushes_a_trailing_utterance() {
        use crate::endpoint::{Endpointer, Event};
        use crate::protocol::SessionConfig;

        // Speech that never hits min_silence: only flush() can end it.
        let mut ep = Endpointer::new(SessionConfig::default());
        let mut jobs = SessionJobs::new();
        let mut trailing = None;
        for _ in 0..(500 / 16) {
            ep.feed(&[0.0; crate::endpoint::FRAME], 0.9);
        }
        jobs.set_ending();
        if let Some(Event::Utterance { pcm, detected_ms, end_ms, .. }) = ep.flush() {
            assert!(!pcm.is_empty(), "flush must keep the spoken audio");
            assert_eq!(detected_ms, 500 - 500 % 16, "detected at the stream head");
            assert!(end_ms <= detected_ms, "trimmed audio cannot pass the cut point");
            trailing = Some(());
        }
        assert!(trailing.is_some(), "flush must end a speaking utterance");
        assert!(ep.flush().is_none(), "second flush has nothing to end");
    }

    #[test]
    fn ended_only_after_the_last_response() {
        let mut jobs = SessionJobs::new();
        jobs.set_ending();
        jobs.track(1, flag());
        jobs.track(2, flag());
        assert!(!jobs.should_end(), "one job still in flight");
        assert!(jobs.answer(1), "job 1 was tracked");
        assert!(!jobs.should_end(), "job 2 still in flight");
        jobs.answer(2);
        assert!(jobs.should_end(), "all jobs answered while ending");
    }

    #[test]
    fn no_job_entries_remain_afterwards() {
        let mut jobs = SessionJobs::new();
        jobs.track(7, flag());
        jobs.track(8, flag());
        assert_eq!(jobs.pending(), 2);
        assert!(!jobs.answer(99), "unknown job ids answer false");
        jobs.answer(7);
        jobs.answer(8);
        assert_eq!(jobs.pending(), 0, "no bookkeeping growth");
        assert!(!jobs.answer(7), "a second answer for the same job is a no-op");
        jobs.set_ending();
        assert!(jobs.should_end());
    }

    #[test]
    fn cancel_all_flags_and_clears() {
        let mut jobs = SessionJobs::new();
        let f1 = flag();
        jobs.track(1, Arc::clone(&f1));
        jobs.cancel_all();
        assert!(f1.load(Ordering::SeqCst), "cancel flags in-flight jobs");
        assert_eq!(jobs.pending(), 0);
        assert!(!jobs.is_ending());
    }

    #[test]
    fn counters_feed_the_ended_event() {
        let mut jobs = SessionJobs::new();
        jobs.track(1, flag());
        jobs.track_failed();
        jobs.note_failed();
        assert_eq!((jobs.submitted(), jobs.failed()), (2, 2));
        jobs.answer(1);
        jobs.set_ending();
        assert!(jobs.should_end());
        assert_eq!((jobs.submitted(), jobs.failed()), (2, 2));
    }
}
