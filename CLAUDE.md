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

2. **`on-modify.01-aw-watcher-taskwarrior.py`** (main component, 525 lines)
   - Modern daemon-based implementation
   - Runs as background process for active tasks
   - Sends heartbeats every `HEARTBEAT_FREQUENCY` seconds (default: 3s)
   - Handles offline gap detection and event creation
   - Uses Unix domain socket (`/tmp/aw-watcher-taskwarrior.sock`) for control
   - Manages task metadata updates and stop signals via socket protocol

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

### Latest: Disable Heartbeat Queue Persistence (d0a6bee)
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

### Known Issues
- Debug log at line 7 documents potential response validation issues when events appear not to exist despite 200 status
- Offline event collision edge cases when multiple gaps detected
- Socket cleanup may fail if daemon exits uncleanly (manual `rm /tmp/aw-watcher-taskwarrior.sock` sometimes needed)

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

1. **Code Review**: The FIX comment at line 7 suggests investigating response validation
2. **Testing**: Comprehensive offline detection edge case testing
3. **Refactoring**: Consider extracting socket protocol handling to separate module
4. **Documentation**: Add installation guide and troubleshooting to repo
5. **Feature Ideas**: Event categorization, task filtering, performance metrics
