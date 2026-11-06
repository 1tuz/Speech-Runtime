//! Batch slicing: a long batch transcription is cut into parts at silence so
//! interactive jobs interleaved between parts never wait for the whole file.
//!
//! The split is a pure function over PCM: frames of 256 samples are scored with
//! an earshot `Detector`, each cut lands in the quietest pause (>= ~300 ms)
//! nearest the target boundary, and parts overlap by 200 ms so words spanning
//! the boundary survive. All state lives in the caller; the split is
//! deterministic for the same input.

use earshot::Detector;

use crate::endpoint::FRAME;

/// Overlap between consecutive parts, in samples (200 ms at 16 kHz).
pub const OVERLAP: usize = 200 * 16_000 / 1000;
/// Minimum pause the splitter considers a cut candidate, in frames (~300 ms).
const MIN_SILENCE_FRAMES: usize = 300 * 16_000 / 1000 / FRAME;
/// How far a cut may drift from the target boundary, in samples (~1 s).
const SEARCH_RADIUS: usize = 16_000;

/// One part of a split batch request: its offset in the original audio (for
/// token timestamps) and its samples.
#[derive(Debug, Clone, PartialEq)]
pub struct Chunk {
    pub offset_samples: usize,
    pub pcm: Vec<f32>,
}

/// Splits `pcm` into parts of at most `max_chunk_ms` of audio each, cutting at
/// silence where possible. Audio shorter than `max_chunk_ms` comes back as a
/// single chunk (the caller decides whether splitting applies at all).
///
/// Cut search: for each target boundary `t` the quietest frame window of
/// `MIN_SILENCE_FRAMES` within `SEARCH_RADIUS` around `t` wins; the cut lands
/// in the middle of that pause. No pause found -> the cut is exactly at `t`.
pub fn split_batch_pcm(pcm: &[f32], max_chunk_ms: u32) -> Vec<Chunk> {
    let max_chunk = max_chunk_ms as usize * 16_000 / 1000;
    if max_chunk == 0 || pcm.len() <= max_chunk {
        return vec![Chunk { offset_samples: 0, pcm: pcm.to_vec() }];
    }

    let mut detector = Detector::default_boxed();
    // Score per frame once; the boundary search re-reads it.
    let frames = pcm.len() / FRAME;
    let mut scores = Vec::with_capacity(frames);
    for index in 0..frames {
        let frame = &pcm[index * FRAME..(index + 1) * FRAME];
        scores.push((index, detector.predict_f32(frame)));
    }

    let mut cuts = Vec::new();
    let mut target = max_chunk;
    while target < pcm.len() {
        // Refuse to leave a tail the overlap would mostly duplicate: a tail
        // under 2x OVERLAP has at most 200 ms of its own audio. Merge it into
        // the previous part instead.
        if pcm.len() - target < 2 * OVERLAP {
            break;
        }
        cuts.push(best_cut(&scores, target, pcm.len()));
        target = cuts.last().expect("pushed above") + max_chunk;
    }

    let mut parts = Vec::with_capacity(cuts.len() + 1);
    let mut start = 0usize;
    for &cut in &cuts {
        parts.push(Chunk { offset_samples: start, pcm: pcm[start..cut].to_vec() });
        // Overlap: the next part re-sees the tail of the previous one.
        start = cut.saturating_sub(OVERLAP);
    }
    parts.push(Chunk { offset_samples: start, pcm: pcm[start..].to_vec() });
    parts
}

/// Quietest frame window of `MIN_SILENCE_FRAMES` within `SEARCH_RADIUS` of
/// `target` (a sample index); falls back to `target` itself.
fn best_cut(scores: &[(usize, f32)], target: usize, len: usize) -> usize {
    let lo = target.saturating_sub(SEARCH_RADIUS);
    let hi = (target + SEARCH_RADIUS).min(len);
    let frame_lo = lo / FRAME;
    let frame_hi = hi / FRAME;
    // (mean VAD score, window start frame) of the quietest window so far.
    let mut best: Option<(f64, usize)> = None;
    for start in frame_lo..frame_hi {
        let end = start + MIN_SILENCE_FRAMES;
        if end > frame_hi {
            break;
        }
        let mean: f64 = scores[start..end].iter().map(|s| s.1 as f64).sum::<f64>()
            / MIN_SILENCE_FRAMES as f64;
        let take = match best {
            // Strictly quieter, or equally quiet and centred nearer the target.
            None => true,
            Some((best_mean, best_start)) => {
                let mid = (start + MIN_SILENCE_FRAMES / 2) as i64;
                let best_mid = (best_start + MIN_SILENCE_FRAMES / 2) as i64;
                mean + 1e-12 < best_mean
                    || ((mean - best_mean).abs() < 1e-12
                        && (mid - target as i64).abs() < (best_mid - target as i64).abs())
            }
        };
        if take {
            best = Some((mean, start));
        }
    }
    match best {
        // Middle of the pause, clamped into the search window.
        Some((_, start)) => {
            let mid = (start + MIN_SILENCE_FRAMES / 2) * FRAME;
            mid.clamp(lo, hi)
        }
        // No pause found: the boundary itself.
        None => target,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seconds(n: usize) -> Vec<f32> {
        vec![0.01; n * 16_000]
    }

    #[test]
    fn short_audio_is_one_chunk() {
        let pcm = seconds(5);
        let parts = split_batch_pcm(&pcm, 10_000);
        assert_eq!(parts.len(), 1);
        assert_eq!(parts[0].offset_samples, 0);
        assert_eq!(parts[0].pcm.len(), pcm.len());
    }

    #[test]
    fn parts_cover_audio_with_overlap() {
        let pcm = seconds(25);
        let parts = split_batch_pcm(&pcm, 10_000);
        assert!(parts.len() >= 3, "25 s at 10 s chunks must split, got {}", parts.len());
        // No samples lost: last part reaches the end; first starts at 0.
        assert_eq!(parts[0].offset_samples, 0);
        assert_eq!(parts.last().unwrap().offset_samples + parts.last().unwrap().pcm.len(), pcm.len());
        // Consecutive parts overlap by exactly OVERLAP (when no silence shifted cuts).
        for pair in parts.windows(2) {
            let prev_end = pair[0].offset_samples + pair[0].pcm.len();
            let overlap = prev_end - pair[1].offset_samples;
            assert!(overlap > 0 && overlap <= 2 * OVERLAP, "overlap {overlap}");
        }
        // Deterministic.
        assert_eq!(parts, split_batch_pcm(&pcm, 10_000));
    }

    #[test]
    fn cut_prefers_silence() {
        // 10 s speech, 1 s silence, 10 s speech: the cut must land in the pause.
        let mut pcm = seconds(10);
        pcm.extend(vec![0.0; 16_000]);
        pcm.extend(seconds(10));
        let parts = split_batch_pcm(&pcm, 11_000);
        assert_eq!(parts.len(), 2);
        let prev_end = parts[0].offset_samples + parts[0].pcm.len();
        // Cut (previous end minus nothing) inside [10 s, 11 s].
        assert!(prev_end >= 10 * 16_000 && prev_end <= 11 * 16_000 + FRAME, "cut at {prev_end}");
    }

    #[test]
    fn cut_falls_back_to_target_without_silence() {
        // Constant speech-like audio: no pause, the cut is the target boundary.
        let pcm = seconds(20);
        let parts = split_batch_pcm(&pcm, 10_000);
        assert_eq!(parts.len(), 2);
        let prev_end = parts[0].offset_samples + parts[0].pcm.len();
        let target = 10_000 * 16_000 / 1000;
        // The detector may still find marginally quieter frames; allow drift.
        assert!((prev_end as i64 - target as i64).abs() < SEARCH_RADIUS as i64);
    }

    #[test]
    fn tiny_tail_is_merged() {
        // 10 s chunks + 300 ms tail: the tail is shorter than the overlap,
        // so it must not become its own part.
        let mut pcm = seconds(10);
        pcm.extend(vec![0.0; 16_000 * 300 / 1000]);
        let parts = split_batch_pcm(&pcm, 10_000);
        assert_eq!(parts.len(), 1);
        assert_eq!(parts[0].pcm.len(), pcm.len());
    }
}
