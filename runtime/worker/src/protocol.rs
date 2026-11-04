//! Line-delimited JSON protocol between the daemon and this worker.

use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Request {
    Ping {
        #[serde(default)]
        id: Option<u64>,
    },
    Stats {
        #[serde(default)]
        id: Option<u64>,
    },
    Load {
        #[serde(default)]
        id: Option<u64>,
        model: String,
        path: String,
        #[serde(default)]
        backend: Option<String>,
    },
    Unload {
        #[serde(default)]
        id: Option<u64>,
        model: String,
    },
    Transcribe {
        #[serde(default)]
        id: Option<u64>,
        model: String,
        /// base64 of little-endian f32 samples, 16 kHz mono.
        pcm: String,
        #[serde(default)]
        language: Option<String>,
        #[serde(default)]
        window_ms: Option<u32>,
        #[serde(default)]
        priority: Option<String>,
    },
    Cancel {
        #[serde(default)]
        id: Option<u64>,
        job: u64,
    },
    SessionOpen {
        #[serde(default)]
        id: Option<u64>,
        session: String,
        model: String,
        #[serde(default)]
        config: Option<SessionConfig>,
    },
    SessionAudio {
        #[serde(default)]
        id: Option<u64>,
        session: String,
        pcm: String,
    },
    SessionEnd {
        #[serde(default)]
        id: Option<u64>,
        session: String,
    },
    SessionCancel {
        #[serde(default)]
        id: Option<u64>,
        session: String,
    },
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct SessionConfig {
    /// VAD probability to start speech.
    pub enter_threshold: f32,
    /// VAD probability below which silence is counted.
    pub exit_threshold: f32,
    /// Audio kept before the detected speech start, ms.
    pub pre_roll_ms: u32,
    /// Trailing audio kept after the last speech frame, ms.
    pub post_roll_ms: u32,
    /// Silence that ends the utterance, ms.
    pub min_silence_ms: u32,
    /// Hard limit of one utterance, ms.
    pub max_utterance_ms: u32,
    /// Language hint for the model.
    pub language: Option<String>,
}

impl Default for SessionConfig {
    fn default() -> Self {
        Self {
            enter_threshold: 0.5,
            exit_threshold: 0.35,
            pre_roll_ms: 300,
            post_roll_ms: 300,
            min_silence_ms: 320,
            max_utterance_ms: 20_000,
            language: None,
        }
    }
}

#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Reply {
    Ok { id: Option<u64>, result: serde_json::Value },
    Error { id: Option<u64>, message: String },
    Event { session: String, event: String, #[serde(flatten)] data: serde_json::Value },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Priority {
    Interactive,
    Batch,
}

impl Priority {
    pub fn parse(value: Option<&str>) -> Self {
        match value {
            Some("batch") => Self::Batch,
            _ => Self::Interactive,
        }
    }
}
