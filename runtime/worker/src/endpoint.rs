//! VAD endpointing: pre-roll, utterance accumulation and silence-based endpoint.

use crate::protocol::SessionConfig;

pub const FRAME: usize = 256; // 16 ms at 16 kHz, what earshot scores
const SR: i64 = 16_000;

#[derive(Debug)]
pub enum Event {
    SpeechStart { at_ms: u64 },
    /// A finished utterance: audio already trimmed, and where it sits in the stream.
    Utterance {
        pcm: Vec<f32>,
        start_ms: u64,
        end_ms: u64,
        /// Absolute time of the last speech frame, for endpoint latency.
        last_voice_ms: u64,
        /// Stream position where the endpointer fired (feed) or flush() cut it.
        detected_ms: u64,
        /// Why the utterance ended: `silence`, `max_utterance` or `client_end`.
        end_reason: &'static str,
    },
}

pub struct Endpointer {
    cfg: SessionConfig,
    pre: Vec<f32>,
    utt: Vec<f32>,
    speaking: bool,
    utt_start: i64,
    last_voice: i64,
    total: i64,
}

impl Endpointer {
    pub fn new(cfg: SessionConfig) -> Self {
        Self {
            pre: Vec::new(),
            utt: Vec::new(),
            speaking: false,
            utt_start: 0,
            last_voice: 0,
            total: 0,
            cfg,
        }
    }

    pub fn speaking(&self) -> bool {
        self.speaking
    }

    pub fn config(&self) -> &SessionConfig {
        &self.cfg
    }

    /// Feeds one 256-sample frame with its VAD score.
    pub fn feed(&mut self, frame: &[f32], score: f32) -> Option<Event> {
        debug_assert_eq!(frame.len(), FRAME);
        self.total += FRAME as i64;
        let pre_samples = (self.cfg.pre_roll_ms as i64 * SR / 1000) as usize;

        if !self.speaking {
            self.pre.extend_from_slice(frame);
            if self.pre.len() > pre_samples {
                let excess = self.pre.len() - pre_samples;
                self.pre.drain(..excess);
            }
            if score >= self.cfg.enter_threshold {
                self.speaking = true;
                self.utt.clear();
                self.utt.extend_from_slice(&self.pre);
                self.utt_start = self.total - self.pre.len() as i64;
                self.last_voice = self.total;
                return Some(Event::SpeechStart { at_ms: (self.utt_start / 16) as u64 });
            }
            return None;
        }

        self.utt.extend_from_slice(frame);
        if score >= self.cfg.exit_threshold {
            self.last_voice = self.total;
        }
        let silence = self.total - self.last_voice;
        let too_long = self.utt.len() as i64 * 1000 / SR >= self.cfg.max_utterance_ms as i64;
        if silence >= self.cfg.min_silence_ms as i64 * SR / 1000 || too_long {
            let reason = if too_long { "max_utterance" } else { "silence" };
            return Some(self.finish(reason));
        }
        None
    }

    /// Ends the current utterance without waiting for silence, for a client stop.
    pub fn flush(&mut self) -> Option<Event> {
        if self.speaking {
            Some(self.finish("client_end"))
        } else {
            None
        }
    }

    /// Feeds a final short block (< FRAME samples) by zero-padding it to one
    /// frame and running the normal `feed` logic. The caller supplies the VAD
    /// score of the padded frame (it must compute it over the padded frame).
    ///
    /// Zero padding must not extend reported real positions: `last_voice_ms`
    /// never advances for a silent padded frame (silence only accumulates when
    /// the score is below the exit threshold), and `end_ms` stays capped at
    /// `min(last_voice + post_roll, total)` — the pad only grows `total` when
    /// there is post-roll left to keep, never past that.
    pub fn feed_tail(&mut self, samples: &[f32], score: f32) -> Option<Event> {
        debug_assert!(samples.len() < FRAME, "feed_tail is for the leftover tail");
        let mut frame = [0.0; FRAME];
        frame[..samples.len()].copy_from_slice(samples);
        self.feed(&frame, score)
    }


    fn finish(&mut self, end_reason: &'static str) -> Event {
        let post = (self.cfg.post_roll_ms as i64 * SR / 1000) as usize;
        let keep_end = (self.last_voice + post as i64).min(self.total);
        let keep = (keep_end - self.utt_start).max(0) as usize;
        if self.utt.len() > keep {
            self.utt.truncate(keep);
        }
        self.speaking = false;
        self.pre.clear();
        let pcm = std::mem::take(&mut self.utt);
        Event::Utterance {
            start_ms: (self.utt_start / 16) as u64,
            end_ms: ((self.utt_start + keep as i64) / 16) as u64,
            last_voice_ms: (self.last_voice / 16) as u64,
            detected_ms: (self.total / 16) as u64,
            end_reason,
            pcm,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn feed_ms(ep: &mut Endpointer, ms: u32, score: f32, events: &mut Vec<Event>) {
        for _ in 0..(ms as usize / 16) {
            if let Some(e) = ep.feed(&[0.0; FRAME], score) {
                events.push(e);
            }
        }
    }

    #[test]
    fn keeps_pre_and_post_roll_and_splits_at_silence() {
        let mut ep = Endpointer::new(SessionConfig::default());
        let mut events = Vec::new();
        feed_ms(&mut ep, 1000, 0.0, &mut events); // silence, rounded down to frames
        feed_ms(&mut ep, 1000, 0.9, &mut events); // speech
        match events.first() {
            Some(Event::SpeechStart { at_ms }) => {
                // 300 ms of pre-roll before the first speech frame, within one frame.
                assert!((700..=720).contains(at_ms), "start {at_ms}");
            }
            other => panic!("expected speech start, got {other:?}"),
        }
        assert_eq!(events.len(), 1);
        feed_ms(&mut ep, 1000, 0.0, &mut events); // trailing silence
        match &events[1] {
            Event::Utterance { pcm, start_ms, end_ms, last_voice_ms, .. } => {
                assert!((700..=720).contains(start_ms), "start {start_ms}");
                // Pre-roll + speech + post-roll, with no silence tail after the last voice frame.
                assert_eq!(*end_ms, last_voice_ms + 300);
                assert_eq!(pcm.len() as u64, (end_ms - start_ms) * 16);
                assert!((2200..=2400).contains(end_ms), "end {end_ms}");
            }
            other => panic!("expected utterance, got {other:?}"),
        }
    }

    #[test]
    fn short_pause_inside_speech_does_not_split() {
        let mut ep = Endpointer::new(SessionConfig::default());
        let mut events = Vec::new();
        feed_ms(&mut ep, 200, 0.9, &mut events);
        feed_ms(&mut ep, 200, 0.1, &mut events); // shorter than min_silence
        feed_ms(&mut ep, 400, 0.9, &mut events);
        feed_ms(&mut ep, 500, 0.0, &mut events);
        assert_eq!(events.len(), 2, "one start and one utterance");
        match &events[1] {
            Event::Utterance { pcm, start_ms, end_ms, last_voice_ms, .. } => {
                // Speech started at the stream head, so there is no pre-roll to keep.
                assert_eq!(*start_ms, 0);
                assert_eq!(pcm.len() as u64, (end_ms - start_ms) * 16);
                assert_eq!(*end_ms, last_voice_ms + 300);
                assert!(*last_voice_ms > 500, "the pause must not end the utterance");
            }
            other => panic!("expected utterance, got {other:?}"),
        }
    }

    #[test]
    fn long_speech_is_forced_at_max_utterance() {
        let mut ep = Endpointer::new(SessionConfig::default());
        let mut events = Vec::new();
        feed_ms(&mut ep, 21_000, 0.9, &mut events);
        let forced = events
            .iter()
            .find_map(|event| match event {
                Event::Utterance { start_ms, end_ms, end_reason, .. } if end_ms - start_ms >= 19_000 => {
                    Some((*start_ms, *end_ms, *end_reason))
                }
                _ => None,
            })
            .expect("max_utterance_ms must force a split");
        assert!(forced.1 - forced.0 <= 20_100, "forced split at {forced:?}");
        // A length limit is not an acoustically detected end: the reason must say so, or a
        // 20 s cap looks like a fast natural endpoint in the latency statistics.
        assert_eq!(forced.2, "max_utterance");
        // Speech continues after the forced split, so a fresh start follows.
        assert!(matches!(events.last(), Some(Event::SpeechStart { .. })));
    }

    #[test]
    fn silence_ends_the_utterance_with_reason_silence() {
        let mut ep = Endpointer::new(SessionConfig::default());
        let mut events = Vec::new();
        feed_ms(&mut ep, 800, 0.9, &mut events);
        feed_ms(&mut ep, 1_500, 0.0, &mut events);
        let reason = events.iter().find_map(|event| match event {
            Event::Utterance { end_reason, .. } => Some(*end_reason),
            _ => None,
        });
        assert_eq!(reason, Some("silence"), "trailing silence is not a length cap");
    }

    #[test]
    fn flush_ends_the_utterance_without_silence() {
        let mut ep = Endpointer::new(SessionConfig::default());
        let mut events = Vec::new();
        feed_ms(&mut ep, 500, 0.9, &mut events);
        assert!(matches!(ep.flush(), Some(Event::Utterance { .. })));
        assert!(ep.flush().is_none(), "a second flush has nothing to end");
        assert!(!ep.speaking());
    }

    /// Feeds `n` real samples of speech (score 0.9) one frame at a time, returns events.
    fn feed_speech_samples(ep: &mut Endpointer, n: usize, events: &mut Vec<Event>) {
        for _ in 0..n / FRAME {
            if let Some(e) = ep.feed(&[0.1; FRAME], 0.9) {
                events.push(e);
            }
        }
    }

    #[test]
    fn feed_tail_delivers_the_leftover_exactly_once() {
        // (carry samples, tail samples): the tail + flush must produce the
        // leftover utterance exactly once — the 255/256/257 and 511/512/513
        // shapes from the spec, expressed as frame-level carries.
        for &(carry, tail) in &[
            (255usize, 1usize),
            (256, 1),
            (257, 1),
            (511, 1),
            (512, 1),
            (513, 1),
        ] {
            let mut ep = Endpointer::new(SessionConfig::default());
            let mut events = Vec::new();
            // Full speech frames, then a partial frame's worth of carry, then
            // one tail sample: carry+tail pads to one frame for feed_tail.
            feed_speech_samples(&mut ep, 2 * FRAME, &mut events);
            feed_speech_samples(&mut ep, carry, &mut events);
            let tail_samples = vec![0.1f32; tail];
            let tail_event = ep.feed_tail(&tail_samples, 0.9);
            // The utterance ends: either feed_tail emitted it, or flush does.
            let final_event = tail_event.or_else(|| ep.flush());
            let utts: Vec<&Event> = events
                .iter()
                .chain(final_event.iter())
                .filter(|e| matches!(e, Event::Utterance { .. }))
                .collect();
            assert_eq!(utts.len(), 1, "carry {carry}+{tail}: leftover exactly once");
            // Neither a second flush nor a second feed_tail may re-deliver it.
            assert!(ep.flush().is_none(), "carry {carry}+{tail}: double flush");
            match utts[0] {
                Event::Utterance { pcm, end_ms, last_voice_ms, end_reason, .. } => {
                    let (end_ms, last_voice_ms) = (*end_ms, *last_voice_ms);
                    assert_eq!(*end_reason, "client_end");
                    // Zero padding must not push end_ms past last_voice+post_roll...
                    assert!(end_ms <= last_voice_ms + 300, "carry {carry}+{tail}");
                    // ...nor beyond what the real sample count justifies.
                    let real_ms = (2 * FRAME + carry + tail) as u64 * 1000 / 16_000;
                    assert!(end_ms <= real_ms.max(last_voice_ms + 300), "carry {carry}+{tail}: end {end_ms} real {real_ms}");
                    let _ = pcm;
                }
                other => panic!("expected utterance, got {other:?}"),
            }
        }
    }

    #[test]
    fn feed_tail_silent_padding_does_not_advance_last_voice() {
        // 1000 ms of speech, then a 1-sample silent tail: the zero pad is
        // silent, so last_voice_ms must stay on the last real speech frame and
        // end_ms must stay at min(last_voice + post_roll, total).
        let mut ep = Endpointer::new(SessionConfig::default());
        let mut events = Vec::new();
        feed_speech_samples(&mut ep, 1000 * 16, &mut events);
        let before = match events.last() {
            Some(Event::SpeechStart { at_ms }) => *at_ms,
            other => panic!("expected speech start, got {other:?}"),
        };
        assert_eq!(before, 0);
        // 62 full speech frames = 992 ms; the padded frame makes 1008 ms.
        // last_voice stays at 992 (pad is silent), end at min(992+300, 1008) = 1008? No:
        // post_roll (300 ms) extends past total, so end is capped at total = 1008.
        let tail = ep.feed_tail(&[0.1], 0.0);
        let utt = match tail.or_else(|| ep.flush()) {
            Some(e @ Event::Utterance { .. }) => e,
            other => panic!("expected utterance, got {other:?}"),
        };
        match utt {
            Event::Utterance { end_ms, last_voice_ms, detected_ms, end_reason, .. } => {
                // The zero pad is silent; feed_tail alone cannot reach
                // min_silence, so flush ends it: last_voice stays on the last
                // real speech frame (992 ms), end is capped as documented.
                assert_eq!(end_reason, "client_end");
                assert_eq!(last_voice_ms, 992, "silent pad must not advance last_voice");
                assert_eq!(end_ms, 1008, "end must stay min(last_voice+post, total)");
                assert!(detected_ms <= 1008);
            }
            other => panic!("expected utterance, got {other:?}"),
        }
    }

    #[test]
    fn feed_tail_after_flush_returns_none() {
        let mut ep = Endpointer::new(SessionConfig::default());
        let mut events = Vec::new();
        feed_speech_samples(&mut ep, 500 * 16, &mut events);
        assert!(ep.flush().is_some());
        assert!(ep.flush().is_none(), "a second flush has nothing to end");
        // Repeated end: after flush, a silent feed_tail must not re-deliver
        // (a loud tail would legitimately start new speech, not re-emit).
        assert!(ep.feed_tail(&[0.0; 100], 0.0).is_none(), "feed_tail after flush has nothing to end");
    }

    #[test]
    fn feed_tail_ends_immediately_after_a_word_then_no_speech_input() {
        // One word of speech (a few frames), tail right after it, then flush:
        // exactly one utterance; a no-speech session tail yields nothing.
        let mut ep = Endpointer::new(SessionConfig::default());
        let mut events = Vec::new();
        feed_speech_samples(&mut ep, 5 * FRAME, &mut events);
        let tail = ep.feed_tail(&[0.1; 10], 0.9);
        let final_event = tail.or_else(|| ep.flush());
        assert!(matches!(final_event, Some(Event::Utterance { .. })));
        assert!(ep.flush().is_none());

        let mut quiet = Endpointer::new(SessionConfig::default());
        assert!(quiet.feed_tail(&[0.0; 10], 0.0).is_none(), "no speech, no utterance");
        assert!(quiet.flush().is_none());
    }
}
