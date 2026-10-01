# aw-taskwarrior: Rust Standalone Polling Daemon

A standalone Rust daemon that tracks TaskWarrior activity into ActivityWatch, replacing the Python hook-based approach with a simple polling model.

## Quick Start

**Prerequisites:**
- Rust 1.70+ (install via [rustup.rs](https://rustup.rs/))
- TaskWarrior installed (`task --version`)
- ActivityWatch server running (`aw-server`) — start it before running the daemon
- Git (to clone this repo)

**Build and run:**
```bash
cd rust-client
cargo build --release
./target/release/aw-taskwarrior  # Start the daemon, press Ctrl-C to stop
```

The daemon will create an ActivityWatch bucket called `aw-watcher-taskwarrior_{your-hostname}` and send heartbeats tracking your active TaskWarrior tasks.

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
# Binary location: target/release/aw-taskwarrior (3.6 MB, fully optimized)
```

**Optional: Install to PATH**

For easier access, copy the binary to a directory on your `$PATH`:
```bash
# Option 1: Copy to ~/.local/bin (personal user binaries)
mkdir -p ~/.local/bin
cp target/release/aw-taskwarrior ~/.local/bin/

# Option 2: Copy to /usr/local/bin (system-wide, requires sudo)
sudo cp target/release/aw-taskwarrior /usr/local/bin/

# Option 3: Create a symlink (keeps the binary in the repo)
ln -s $(pwd)/target/release/aw-taskwarrior ~/.local/bin/aw-taskwarrior
```

Then you can run the daemon from anywhere:
```bash
aw-taskwarrior
```

## Configuration

The daemon works **out of the box with zero configuration**. All settings have sensible hardcoded defaults.

For custom settings, create an optional TOML file at `~/.config/aw-taskwarrior/config.toml`:

```toml
# How often to poll TaskWarrior for active tasks (seconds)
poll_interval_seconds = 5

# ActivityWatch server location
aw_server_host = "localhost"
aw_server_port = 5600

# TaskWarrior tag that marks a task as "offline-trackable"
# Tasks with this tag tolerate long gaps in tracking (e.g., system suspend)
offline_tag = "OFFLINE"

# Pulsetime (heartbeat merge window in ActivityWatch)
# Online tasks: gap > 120s creates a visible gap in activity
# Offline tasks: gap > 12h creates a visible gap (tolerates suspend/sleep)
online_pulsetime_seconds = 120
offline_pulsetime_seconds = 43200

# Optional: API key if ActivityWatch requires authentication
# api_key = "your-api-key-here"
```

**Default behavior when no task is active:**
- A heartbeat is sent with only the title "No task tracked"
- No project or tags are included (empty values omitted)
- This shows in ActivityWatch that tracking is running but no task is active

## Running

```bash
./target/release/aw-taskwarrior
```

The daemon will:
1. Load config from `~/.config/aw-taskwarrior/config.toml` (or use built-in defaults)
2. Connect to ActivityWatch server (default: `localhost:5600`)
3. Create/connect to bucket `aw-watcher-taskwarrior_{hostname}` (holds your task tracking data)
4. Enter poll loop:
   - Every 5 seconds: query TaskWarrior for active tasks via `task rc.hooks:off +ACTIVE export`
   - Find the most recently started task
   - Send a heartbeat to ActivityWatch with task details
   - If no task is active, send "No task tracked" indicator
5. Exit cleanly on Ctrl-C or when receiving SIGTERM

**Example output:**
```
[2026-10-01T19:04:57.236951738+00:00] INFO  Starting aw-taskwarrior, bucket=aw-watcher-taskwarrior_HerreraMonroy
[2026-10-01T19:04:57.271857185+00:00] INFO  Bucket aw-watcher-taskwarrior_HerreraMonroy ready
[2026-10-01T19:05:02.123456789+00:00] INFO  Received shutdown signal, exiting cleanly
```

**Running in the background:**
```bash
./target/release/aw-taskwarrior &  # Run in background
# or
nohup ./target/release/aw-taskwarrior > /tmp/aw-taskwarrior.log 2>&1 &
```

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

## Troubleshooting

**"Bucket aw-watcher-taskwarrior_... ready" but no heartbeats showing in ActivityWatch**
- Ensure ActivityWatch server is actually running: `curl http://localhost:5600/api/0/info`
- Check that the daemon is polling TaskWarrior: `task +ACTIVE export` should show your active tasks
- Verify the daemon isn't crashing silently by running it in foreground: `./target/release/aw-taskwarrior`

**"task query failed, reusing last known task" in logs**
- TaskWarrior binary not found or not in `$PATH`
- Verify: `which task` and `task --version`
- The daemon will keep using the last known task state, so it's resilient to transient failures

**Config file not loading (using defaults instead)**
- Verify config file location: `cat ~/.config/aw-taskwarrior/config.toml`
- Check for TOML syntax errors (run `cargo test` in the repo to validate TOML parsing)
- Permission issue: `ls -la ~/.config/aw-taskwarrior/`

**"Fatal: failed to create AW client"**
- ActivityWatch server is not running or not reachable at the configured address
- Start aw-server: `aw-server` in another terminal
- Check network: `ping localhost` and `curl -I http://localhost:5600/api/0/info`

**Daemon exits immediately without error**
- Check if already running: `pgrep -a aw-taskwarrior`
- Kill any existing daemon: `pkill aw-taskwarrior`
- Run in foreground to see error messages: `./target/release/aw-taskwarrior`

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
