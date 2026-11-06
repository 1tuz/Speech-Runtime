# Speech Runtime: native macOS menu application

Patch basis: `1tuz/Speech-Runtime` at `7c689817cdb79ea48821bf32f9acbd3a55031f8b`.
This is an **additive UI/packaging layer**, not another ASR research iteration.
The existing Swift daemon, Rust worker, endpoints and inference implementation remain in use.

## Included

- Native SwiftUI panel hosted by AppKit's status item/popover. No Dock icon, WebView or JavaScript.
- A large **Turn on / Turn off** button, explicit progress and error states.
- Native Liquid Glass for the main control on **macOS 26+**, with system material on macOS 15.
- System light/dark appearance, Reduce Transparency/Reduce Motion, English and Russian strings.
- CPU and summed RSS for UI + daemon + worker; separate process-reported current ANE memory.
- Active **sessions**, not an invented count of unique applications; interactive queue and model state.
- Two-second resource updates while visible; ten-second state checks while hidden. No continuous animation.
- User-session `launchd` service independent from UI lifetime; no root or privileged helper.
- Explicit login opt-in through `SMAppService.mainApp`, reflecting the OS's real approval status.
- Parakeet Ultra Q8_0 as the only model in the application's generated configuration.
- Pinned model download, streaming progress, publisher LFS SHA-256 verification, cached receipt,
  reuse of an existing matching Hugging Face cache, and a bundled wavo preparation helper.
- `.app` and `.dmg` build, optional Developer ID signing, local installation script.

## Build and install

Use an Apple Silicon Mac with Xcode 26+ / macOS 26+ SDK and the Rust toolchain required by
`runtime/worker/Cargo.lock`. Runtime deployment floor is macOS 15; native Liquid Glass needs 26+.

From the repository root after applying the patch:

```bash
bash runtime/macos-app/scripts/build-app.sh
bash runtime/macos-app/scripts/install-app.sh
```

Outputs: `dist/Speech Runtime.app`, `dist/SpeechRuntime-macos-arm64.dmg` and its SHA-256.
The installer uses `~/Applications`, so it does not ask for administrator privileges.
Alternatively copy the app from the DMG to `/Applications` before first launch.
Do not run it permanently from the mounted DMG or the build directory.

Packaging runs only the new fast UI-model tests. It does **not** run any 60/150-minute experiments,
WER sweeps or model inference. Models are downloaded only after the user explicitly turns on the service.

For distribution:

```bash
SIGNING_IDENTITY='Developer ID Application: YOUR NAME (TEAMID)' \
  bash runtime/macos-app/scripts/build-app.sh
```

Without that variable the result is ad-hoc signed for local use. No certificate or notarization
credential is included. A publicly distributed DMG still needs your Developer ID/notarization workflow.
The login-item path must be checked on the final installed, signed bundle; local ad-hoc builds
can be rejected by system approval policy. The UI displays errors instead of pretending it enabled login.

## Power, login and UI lifetime

| Action | Effect |
|---|---|
| Turn on | Verify/download model, prepare ANE, bootstrap the user LaunchAgent, warm Parakeet |
| Turn off | Confirm when sessions/work are active, boot out this app's agent, wait for owned processes to exit |
| Close panel | No change to service; fast resource sampling stops |
| Quit interface | Leave service and other clients running |
| Open at login ON | macOS opens the interface at the next login; it restores the last explicit service On/Off state |
| Open at login OFF | No automatic startup next login; the current service is left alone |
| Worker crash | Existing daemon supervision handles it; clients must reopen sessions and load their model again |

The agent plist is **not** installed in `~/Library/LaunchAgents`; it lives in Application Support
and is bootstrapped into `gui/<uid>` for this login session. This is deliberate: a permanently
registered `SMAppService.agent` would also run on subsequent logins even with a separate UI login
switch turned off. `SMAppService.mainApp` owns login persistence, `launchctl` owns the current
service session. Closing UI does not unload that job.

The global Power Off action interrupts ongoing work after confirmation; it does not claim to flush
all clients' unsent audio. A client needing a final result must send its own `streams/end` first.

## Installed files

```
Speech Runtime.app/Contents/MacOS/
  SpeechRuntimeApp      # UI
  speech-runtime       # existing daemon
  sr-worker            # existing worker
  sr-model-prepare     # one-shot setup helper, same wavo/Cargo.lock
  sr                   # existing CLI

~/Library/Application Support/SpeechRuntime/
  app-config.json      # own config, existing config.json is not overwritten
  token                # existing local API credential, never shown/copied by UI
  Models/parakeet-ultra-Q8_0.gguf
  parakeet-receipt.json
  com.speechruntime.agent.plist
  daemon.log
```

ANE's compiled cache remains owned by wavo in `~/Library/Caches/wavo/`.
The first load in a different executable can still require system compilation. The UI waits for
readiness and handles a lost cold-load HTTP response without creating another model instance.
No automatic fallback to GPU or GigaAM is performed.

To update, turn the service off and quit the interface, then run the installer again. Model files,
preferences, and the API token remain outside the bundle and are preserved. To uninstall, turn off
login startup, turn off the service, quit the interface and remove the app. Model/data removal is
manual so uninstalling the UI never silently deletes recordings or unrelated developer data.

## Other applications

The existing loopback API remains at `http://127.0.0.1:8477`. The app refuses to adopt or kill a
manually started development daemon on that port. Stop that daemon before enabling the packaged one.

Each independent client uses the **same** token and daemon, but owns its own session:

1. `POST /v1/models/load` with `{"model":"parakeet-ultra","backend":"ane"}`.
2. `POST /v1/streams` with `{"model":"parakeet-ultra"}`.
3. Send mono 16 kHz PCM to `/v1/streams/audio`, specifying `X-Sample-Format`.
4. Poll `/v1/streams/events` with the returned cursor.
5. End or cancel only the client's own session.

Loading is cached by the existing worker; it does not start one ASR process per application.
The current daemon does not auto-load a model on `open_stream`: clients must perform step 1 after
idle unload or a worker restart. This patch does not misrepresent that behavior as automatic recovery.
The existing Swift `RuntimeClient` can be used; the included `examples/two_clients.py` is a bounded
transport smoke with two half-second silent streams, not a transcription benchmark.

The GUI does not capture the microphone or screen audio and therefore does not request recording
permissions. Audio capture remains in client applications. Reading a shared token file assumes
same-user, non-sandboxed clients; sandboxed client credential handoff is not newly solved here.

## Resource accounting

CPU is the delta of `ri_user_time + ri_system_time`, divided by monotonic elapsed time:
100% means one core, so values can exceed 100%. A new PID/start time resets the baseline.
RSS is explicitly a sum of process RSS, not unique system memory; shared pages may be counted twice.
ANE current accounting comes from the existing worker's health reply and is displayed separately.
Unknown values are dashes, not zero. No root, `task_for_pid`, `powermetrics` or periodic `ps` is used.

## Minimal acceptance, not another research loop

1. Build the bundle and run the existing short unit tests that the build invokes.
2. Install, click On once, observe model ready, then open/close the panel and check CPU/RAM updates.
3. Run `python3 runtime/macos-app/examples/two_clients.py` once.
4. Quit only the interface; confirm the CLI still reaches the daemon; reopen the app and click Off.
5. Check login ON/OFF on the installed bundle and system light/dark/reduced-transparency settings.

No rerun of existing audio benchmarks is a prerequisite for this UI patch.

## Validation performed while creating this patch

- Swift 6.2.1/Linux: 7 pure model/contract/CPU/launch-plist tests passed.
- macOS-target Swift **syntax parse**, shell syntax, Python syntax, plist parsing and localization keys checked.
- Additive patch application checked on a clean staging tree; remote base layout read through GitHub.
- **Not performed:** AppKit/SwiftUI type-checking with a macOS SDK, native rendering, actual ANE preparation,
  launchctl/login registration, codesigning or a macOS `.app` build. Those require the target Mac.
  Linux tests do not establish native UI or service-manager correctness.

The implementation is supplied as source, not as a claimed tested macOS binary.
See `SOURCES.md` for the ready-made APIs/components evaluated. No Keepresso source is copied.
