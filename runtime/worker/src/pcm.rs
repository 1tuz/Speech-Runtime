//! PCM helpers: base64 f32 frames and WAV output for the CLI.

use base64::Engine as _;

pub fn decode_f32(base64_text: &str) -> Result<Vec<f32>, String> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(base64_text.trim())
        .map_err(|e| format!("pcm base64: {e}"))?;
    if bytes.len() % 4 != 0 {
        return Err(format!("pcm length {} is not a multiple of 4", bytes.len()));
    }
    let mut out = Vec::with_capacity(bytes.len() / 4);
    for chunk in bytes.chunks_exact(4) {
        out.push(f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]));
    }
    Ok(out)
}

pub fn encode_f32(pcm: &[f32]) -> String {
    let mut bytes = Vec::with_capacity(pcm.len() * 4);
    for sample in pcm {
        bytes.extend_from_slice(&sample.to_le_bytes());
    }
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_samples() {
        let pcm = vec![0.0, 0.5, -0.25, 1.0];
        assert_eq!(decode_f32(&encode_f32(&pcm)).unwrap(), pcm);
    }

    #[test]
    fn rejects_partial_sample() {
        let bad = base64::engine::general_purpose::STANDARD.encode([1u8, 2, 3]);
        assert!(decode_f32(&bad).is_err());
    }
}
