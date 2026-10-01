# aw-taskwarrior: Rust Standalone Polling Daemon

A standalone Rust daemon that tracks TaskWarrior activity into ActivityWatch, replacing the Python hook-based approach with a simple polling model.

## Features

- **Continuous polling**: Queries `task rc.hooks:off +ACTIVE export` every 5 seconds (configurable)
- **Dynamic pulsetime**: Online tasks (120s pulsetime) vs. OFFLINE-tagged tasks (12h pulsetime) for gap-tolerant tracking
- **Graceful shutdown**: Responds to SIGINT/SIGTERM with clean shutdown
- **Resilient polling**: Transient task CLI failures fall back to last known task state
- **ActivityWatch ready**: Discoverable by aw-qt via `aw-*` naming convention, ready for manual or automatic start

## Building

```bash
cd rust-client
cargo build --release
# Binary: target/release/aw-taskwarrior
```

## Configuration

Optional TOML config at `~/.config/aw-taskwarrior/config.toml`:

```toml
poll_interval_seconds = 5
aw_server_host = "localhost"
aw_server_port = 5600
offline_tag = "OFFLINE"
online_pulsetime_seconds = 120
offline_pulsetime_seconds = 43200
# api_key = "optional_auth_key"
```

All settings have hardcoded defaults; config file is optional.

## Running

```bash
./target/release/aw-taskwarrior
```

The daemon will:
1. Load config (or use defaults)
2. Create/connect to ActivityWatch bucket `aw-watcher-taskwarrior_{hostname}`
3. Enter poll loop, querying TaskWarrior every 5 seconds
4. Send heartbeats to AW with dynamic pulsetime based on task tags
5. Exit cleanly on Ctrl-C

## Integration with aw-qt

1. Build the binary and place on `$PATH` (e.g., `~/.local/bin/`)
2. Start aw-qt, which auto-discovers `aw-taskwarrior` via naming convention
3. Start manually from aw-qt's UI, or add to `autostart_modules` in `~/.config/activitywatch/aw-qt/aw-qt.toml`:

```toml
[aw-qt]
autostart_modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window", "aw-taskwarrior"]
```

## Testing

All 14 unit tests pass:

```bash
cargo test
```

Tests cover:
- Config loading, defaults, TOML overlays
- TaskWarrior JSON parsing, task selection by recency
- Pulsetime selection (online vs. offline logic)

## Project Structure

- `src/main.rs` - Poll loop, signal handling, orchestration
- `src/config.rs` - Config struct, TOML loading, defaults
- `src/taskwarrior.rs` - Task CLI invocation, JSON parsing
- `src/activitywatch.rs` - AW client wrapper, bucket/heartbeat logic
- `src/log.rs` - Simple stdout/stderr logging

## Dependencies

- `aw-client-rust` (git, ActivityWatch HTTP client)
- `chrono` (datetime parsing/formatting)
- `serde`/`serde_json` (JSON serialization)
- `toml` (config file parsing)
- `dirs` (XDG config path resolution)
- `ctrlc` (signal handling)
- `gethostname` (bucket naming)

## Known Limitations

- No `--testing` flag support (future enhancement: could swap to `_testing` bucket suffix)
- No in-process heartbeat retry backoff (naturally retried on next poll cycle)
- Hostname resolved once at startup; if hostname changes, restart needed

## Design Decisions

- **No Unix socket, no per-task daemons** — Single continuous polling daemon instead of hook-spawned processes
- **Dynamic pulsetime** — Replaces afk-bucket-based gap backfilling; server-side heartbeat merge handles gaps automatically
- **Graceful degradation** — Transient task CLI failures reuse last known state; AW server unreachable retries indefinitely
- **Pure stdout/stderr logging** — No file logging by default, compatible with aw-qt's log retrieval

## Future Work

- [ ] Add `--testing` flag for aw-qt testing mode
- [ ] CLI arg parsing for host/port/api-key override
- [ ] Structured logging with configurable levels
- [ ] Performance metrics/health reporting
- [ ] Event categorization by task type or project
