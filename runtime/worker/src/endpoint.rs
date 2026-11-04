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
            return Some(self.finish());
        }
        None
    }

    /// Ends the current utterance without waiting for silence, for a client stop.
    pub fn flush(&mut self) -> Option<Event> {
        if self.speaking {
            Some(self.finish())
        } else {
            None
        }
    }

    fn finish(&mut self) -> Event {
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
            Event::Utterance { pcm, start_ms, end_ms, last_voice_ms } => {
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
            Event::Utterance { pcm, start_ms, end_ms, last_voice_ms } => {
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
                Event::Utterance { start_ms, end_ms, .. } if end_ms - start_ms >= 19_000 => {
                    Some((*start_ms, *end_ms))
                }
                _ => None,
            })
            .expect("max_utterance_ms must force a split");
        assert!(forced.1 - forced.0 <= 20_100, "forced split at {forced:?}");
        // Speech continues after the forced split, so a fresh start follows.
        assert!(matches!(events.last(), Some(Event::SpeechStart { .. })));
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
}
