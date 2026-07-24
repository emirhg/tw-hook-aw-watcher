# tw-hook-aw-watcher: Project Summary

## What is this project?

**tw-hook-aw-watcher** is a TaskWarrior hook that integrates with [ActivityWatch](https://activitywatch.net/) to provide detailed timeline tracking of your work. When you use TaskWarrior to manage tasks, this hook automatically records those activities in ActivityWatch, creating a comprehensive time-tracking record.

### Key value proposition
- **Continuous heartbeats**: Instead of just recording task start/stop events, the hook sends regular heartbeats to maintain granular time tracking
- **Offline detection**: When your system goes offline, the hook intelligently detects and fills gaps in the timeline, logging time spent offline
- **Daemon-based architecture**: Uses a background daemon to ensure uninterrupted tracking even across multiple task changes

## Architecture Overview

### Entry Points

1. **`tw-hook-aw-watcher.py`** (525 lines)
   - Original simple hook that posts task completion events to ActivityWatch
   - Triggered by TaskWarrior on task modifications
   - Reads old/new task state from stdin (TaskWarrior hook protocol)
   - Sends single event when task stops or changes

2. **`on-modify.01-aw-watcher-taskwarrior.py`** (main component, 750+ lines)
   - Modern daemon-based implementation with recovery mechanism
   - Runs as background process for active tasks
   - Sends heartbeats every `HEARTBEAT_FREQUENCY` seconds (default: 3s)
   - Handles offline gap detection and event creation
   - Uses Unix domain socket (`/tmp/aw-watcher-taskwarrior.sock`) for control
   - Manages task metadata updates and stop signals via socket protocol
   - **Recovery mode** (`--recover` flag): Detects active task and reconstructs timeline from ActivityWatch queries

### Core Components

#### TaskState Class
- Thread-safe wrapper around task metadata (description, project, tags, uuid)
- Allows updates while heartbeat thread is running
- Snapshot method returns consistent task state

#### Daemon Loop
1. Read task from stdin (TaskWarrior provides this)
2. Create ActivityWatch bucket for task tracking
3. Main loop:
   - Send heartbeats at `HEARTBEAT_FREQUENCY` interval
   - Check for offline gaps (when system was unreachable)
   - Create offline events if gaps detected
   - Listen for socket commands (stop, update, status)
   - Exit when stop signal received

#### Socket Protocol
Commands are JSON objects sent to `/tmp/aw-watcher-taskwarrior.sock`:

```json
// Stop tracking current task
{"action": "stop", "uuid": "task-uuid"}

// Update task metadata (description, project, tags)
{"action": "update", "uuid": "task-uuid", "task": {...}}

// Query current tracked task
{"action": "status"}
```

#### Offline Event Detection
- Monitors time since last heartbeat
- Queries ActivityWatch's afk-watcher to determine if system was idle
- If offline detected, creates a single event spanning the gap
- Prevents heartbeat overlap by skipping heartbeat if offline event created

#### Recovery Mechanism (`recover_daemon()`)
Restores tracking state when daemon crashes but task remains active:
1. **Query TaskWarrior** for active task and its start time
2. **Query ActivityWatch** for last tracked event to determine timeline
3. **Compute gaps**: from task start → first event, and last event → now
4. **Intelligent backfilling** (before daemon starts):
   - **OFFLINE tasks** (with `OFFLINE` tag): Always backfill gaps (trust the tag)
   - **ONLINE tasks** (no tag): Only backfill if afk activity confirmed (user was at keyboard)
5. **Start daemon** after backfill completes to avoid heartbeat race conditions

Helper functions:
- `_create_offline_event()`: Directly POST offline event to ActivityWatch
- `_has_afk_activity()`: Check afk bucket for user activity in time window
- `get_last_tracked_event_end()`: Query ActivityWatch for last event timestamp
- `parse_tw_timestamp()`: Parse TaskWarrior's UTC format (`20260723T200958Z`)

### Key Constants & Settings

```
HEARTBEAT_FREQUENCY = 3 seconds
PULSETIME = 5 seconds (must be > HEARTBEAT_FREQUENCY)
COMMIT_INTERVAL = 15 seconds
SERVER_RETRY_INTERVAL = 30 seconds
OFFLINE_DETECTION_THRESHOLD = 6 seconds
SOCKET_FILE = /tmp/aw-watcher-taskwarrior.sock
DEBUG_LOG_FILE = /tmp/aw-watcher-taskwarrior-debug.log
```

## Recent Changes

### Latest: Intelligent Gap-Filling Recovery Mechanism (ccc0390)
- Added `recover_daemon()` function: Detects active task and reconstructs timeline from ActivityWatch
- **Online/offline distinction**:
  - OFFLINE tasks: Always backfill gaps (trust the tag, no afk verification)
  - ONLINE tasks: Only backfill if afk activity confirmed (user was working)
- **Query-based gap detection**: Uses TaskWarrior start time and ActivityWatch last event (not debug log parsing)
- **Daemon startup timing**: Backfill completes before daemon starts to prevent heartbeat race conditions
- Added helper functions: `_create_offline_event()`, `_has_afk_activity()`, `get_last_tracked_event_end()`, `parse_tw_timestamp()`
- Invoked via `python3 on-modify.01-aw-watcher-taskwarrior.py --recover`

### Previous: Disable Heartbeat Queue Persistence (d0a6bee)
- Changed from `queued=True, commit_interval=15` to `queued=False`
- Reason: Persistent queue caused stale heartbeat replay after system suspend/resume
- Race condition: Queue would replay old heartbeats, corrupting offline events via server-side merge
- Solution: Send heartbeats immediately (no buffering needed - they're sent every 3s anyway)
- Also: Use ISO 8601 format for offline event timestamps

### Previous: Single-Task Unix Socket Control (4b69ab4)
- Replaced LIFO queue + file-polling with single-task daemon
- Each active task spawns one daemon controlled via Unix socket
- Fixed false-reanimation bugs in offline detection
- Task metadata updates now done via socket instead of file I/O

### Offline Event Handling (bf95839)
- Ensure offline events and heartbeats don't overlap
- Timestamp consistency across offline detection
- Return boolean indicating if offline event was created
- Skip heartbeat in same iteration as offline event

### Previous Work
- Payload data naming standardization
- Daemon implementation with continuous heartbeats
- ActivityWatch HTTP client integration
- Initial hook proof-of-concept

## Known Issues & Fixes

### ✅ FIXED: Daemon Crash Loses Tracking Context
**Issue**: When daemon crashes or is killed, tracking state is lost. Restarting TaskWarrior hook starts new daemon from current time, leaving gap with no recorded activity.

**Root Cause**: No recovery mechanism existed to detect and backfill gaps when daemon unexpectedly terminated.

**Fix**: Implemented `recover_daemon()` with intelligent gap-filling:
- Queries TaskWarrior for active task and start time
- Queries ActivityWatch for last tracked event
- Computes gaps from task start → first event and last event → now
- For OFFLINE tasks: Creates offline event for all significant gaps (trust the tag)
- For ONLINE tasks: Only creates recovery event if afk activity confirmed (user was at keyboard)
- Starts daemon after backfill completes to prevent race conditions

**Usage**: `python3 on-modify.01-aw-watcher-taskwarrior.py --recover`

### ✅ FIXED: Offline Events Disappearing After System Suspend
**Issue**: After system suspend/resume, offline events would disappear from bucket despite 200 OK response.

**Root Cause**: `aw_client` was configured with `queued=True, commit_interval=15`, which stores heartbeats in a persistent SQLite queue (`~/.local/share/aw-client/queued/`). When the system resumed:
1. Offline event would be POSTed directly (successful, 200 OK)
2. But the RequestQueue thread would replay stale, unflushed heartbeats from before suspend
3. These stale heartbeats (with earlier timestamps) would corrupt the offline event via server-side merge logic

**Fix**: Changed to `queued=False` - heartbeats now sent immediately, no persistence. This eliminates the race condition entirely. Persistent queuing adds no value here since heartbeats are sent every 3 seconds anyway.

## Development Notes

### How to Test
1. Ensure ActivityWatch is running: `aw-server`
2. Have TaskWarrior installed and configured
3. Install the hook: Place `on-modify.01-aw-watcher-taskwarrior.py` in `~/.task/hooks/`
4. Monitor debug output: `tail -f /tmp/aw-watcher-taskwarrior-debug.log`
5. Start a task: `task start`
6. Stop a task: `task stop`

### Recovery Testing
1. Start a task: `task start`
2. Kill the daemon: `pkill -f "python.*--daemon"`
3. Run recovery: `python3 on-modify.01-aw-watcher-taskwarrior.py --recover`
4. Check debug log for gap detection and backfill messages:
   - For OFFLINE tasks: Should see "backfilling all significant gaps"
   - For ONLINE tasks: Should see "checking for afk activity to verify work"
5. Verify recovery events in ActivityWatch (check bucket with correct timestamp range)

### Known Issues
- Timezone handling: Recovery uses UTC timestamps; adjust for local timezone if needed
- Socket cleanup may fail if daemon exits uncleanly (manual `rm /tmp/aw-watcher-taskwarrior.sock` sometimes needed)
- Offline event collision edge cases when multiple gaps detected simultaneously

### Dependencies
- `requests` - HTTP client for ActivityWatch API
- `aw_client` - ActivityWatch Python client library
- `aw_core` - ActivityWatch event models
- Standard library: `json`, `sys`, `socket`, `subprocess`, `threading`, `time`, `datetime`

## Git Workflow

- Main branch: `main`
- Commits use conventional commit format (feat:, fix:, refactor:, doc:, chg:)
- Recent work focused on daemon stability and offline detection accuracy

## Next Steps for Contributors

1. **Timezone Support**: Implement proper timezone handling for recovery (currently uses UTC, should respect local timezone)
2. **Testing**: Comprehensive offline detection and recovery edge case testing
3. **Refactoring**: Consider extracting socket protocol and recovery logic to separate modules
4. **Documentation**: Add installation guide and troubleshooting FAQ to repo
5. **Feature Ideas**: 
   - Event categorization by task type or project
   - Automatic recovery trigger on daemon start failure
   - Performance metrics dashboard
   - Web UI for recovery control and event visualization
