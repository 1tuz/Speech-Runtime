//! One-shot preparation helper bundled with the menu app; no audio is processed here.
//! Uses the worker's own Cargo.lock and wavo version (no second ASR dependency graph).
use std::{ops::ControlFlow, path::PathBuf};

fn main() {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 1 {
        eprintln!("usage: sr-model-prepare /absolute/path/to/model.gguf");
        std::process::exit(2);
    }
    let path = PathBuf::from(&args[0]);
    if !path.is_absolute() || !path.is_file() {
        eprintln!("model must be an existing absolute file path");
        std::process::exit(2);
    }
    match wavo::Model::prepare(&path, |step| {
        eprintln!("prepare: {step:?}");
        ControlFlow::Continue(())
    }) {
        Ok(true) => println!("prepared"),
        Ok(false) => {
            eprintln!("Neural Engine preparation is unavailable on this machine");
            std::process::exit(1);
        }
        Err(error) => {
            eprintln!("prepare failed: {error}");
            std::process::exit(1);
        }
    }
}
