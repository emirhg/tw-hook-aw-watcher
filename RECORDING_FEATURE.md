# Recording Trigger Feature

## Overview

The TaskWarrior hook now supports automatic screen recording for tasks tagged with `VIDEO` or `RECORD`. When a task with either tag starts, the hook triggers a configurable "start recording" command. When the task stops, it triggers the "stop recording" command.

## Architecture

### New Module: `recording_control.py`

A lightweight, independent module that handles all recording-related logic. It has:
- **Command constants** at the top: `RECORD_START_COMMAND` and `RECORD_STOP_COMMAND` (defaults to `None` for unconfigured)
- **`has_record_tag(tags)`** — checks if task tags contain `VIDEO` or `RECORD`
- **`start_recording(task)`** — launches the start command as a detached background process
- **`stop_recording(task_uuid)`** — launches the stop command as a detached background process
- **Built-in logging** — all actions logged to `/tmp/aw-watcher-taskwarrior-debug.log` with timestamps

Key design:
- **Zero dependencies on the hook file** — the module can be imported and tested independently
- **Never raises exceptions** — all subprocess errors caught and logged; never blocks TaskWarrior
- **Argument-list commands** — `RECORD_START_COMMAND = ["/path/to/script.sh"]`, not shell strings (safer, no shell injection)
- **Idempotent** — calling stop on a task that was never recording is harmless (underlying commands should be idempotent)

### Hook Integration: `on-modify.01-aw-watcher-taskwarrior.py`

Three call sites added, all inside the existing `try/except` block that guarantees hook exceptions never propagate:

1. **Case 1 (Task Started)** — after daemon setup, check tags and start recording:
   ```python
   if recording_control.has_record_tag(new_task.get("tags", [])):
       recording_control.start_recording(new_task)
   ```
   Called for all daemon-start paths (new daemon or already tracked).

2. **Daemon-Switch sub-case** — when switching from task A to task B, stop task A's recording:
   ```python
   recording_control.stop_recording(current_uuid)
   ```
   Called unconditionally (safe because the module no-ops if the command is unconfigured).

3. **Case 2 (Task Stopped)** — after stopping the daemon, stop recording:
   ```python
   if recording_control.has_record_tag(old_task.get("tags", [])):
       recording_control.stop_recording(task_uuid)
   ```
   Uses `old_task` because tags don't change between old/new on a plain stop.

**Case 3 (Task Modified While Active)** — left untouched, out of scope. If a user adds/removes the tag on an already-running task, recording won't retroactively start/stop.

## Configuration

Edit `recording_control.py` and set the command constants at the top. Example for FFmpeg-based recording:

```python
RECORD_START_COMMAND = ["/path/to/start-recording.sh"]
RECORD_STOP_COMMAND = ["/path/to/stop-recording.sh"]
```

Commands are **argument lists** (no shell=True), so they must be directly executable files or shell scripts with a shebang (`#!/bin/bash` or similar).

Example start-recording script:
```bash
#!/bin/bash
# start-recording.sh
ffmpeg -f x11grab -i :0 -f pulse -i default /tmp/screen-$(date +%s).mkv 2>/dev/null &
echo $! > /tmp/ffmpeg.pid
```

Example stop-recording script:
```bash
#!/bin/bash
# stop-recording.sh
kill $(cat /tmp/ffmpeg.pid 2>/dev/null) 2>/dev/null || true
```

Leave as `None` to disable recording (will log "not configured" warnings, but never fails).

## Usage

### Start a task for recording
```bash
task add +VIDEO "My task"
task start <id>
```

### Stop a task
```bash
task stop <id>
```

### Both tags are equivalent
`+VIDEO` and `+RECORD` both trigger the same command pair — use whichever name fits your workflow.

## Deployment

After editing the command constants:

1. **Repo file**: `/home/emirhg/Desktop/ianua/tw-hook-aw-watcher/recording_control.py`
2. **Repo file**: `/home/emirhg/Desktop/ianua/tw-hook-aw-watcher/on-modify.01-aw-watcher-taskwarrior.py` (import added, three call sites)
3. **Live hook**: Copy both files to `~/.task/hooks/`:
   ```bash
   cp recording_control.py ~/.task/hooks/
   cp on-modify.01-aw-watcher-taskwarrior.py ~/.task/hooks/
   ```

(Note: The hook is installed as a plain file copy, not a symlink, so manual deployment is required.)

## Debugging

Check `/tmp/aw-watcher-taskwarrior-debug.log` for all recording-related messages:
- `start_recording: RECORD_START_COMMAND not configured, skipping` — no command configured
- `Started recording for task <uuid>: ...` — command launched successfully
- `Stopped recording for task <uuid>: ...` — stop command launched successfully
- `Error starting/stopping recording: <error>` — subprocess error (logged, not raised)

Example:
```bash
tail -f /tmp/aw-watcher-taskwarrior-debug.log | grep recording
```

## Known Limitations

1. **Task modification while active** (Case 3) — adding/removing `+VIDEO`/`+RECORD` on an already-running task won't retroactively start/stop recording. Only plain task start/stop (Cases 1 and 2) are tracked.
2. **Daemon restart** — if the heartbeat daemon crashes and is auto-recovered, recording state won't be restored. Only fresh task start/stop trigger recording.
3. **No feedback on command failure** — if the underlying recording command fails (file permissions, missing tool, etc.), the error is logged to the debug log but won't block the task. Users must check the log to diagnose.

## Testing

1. Set dummy commands for testing:
   ```python
   RECORD_START_COMMAND = ["/bin/sh", "-c", "echo START-REC $(date) >> /tmp/rec-test.log"]
   RECORD_STOP_COMMAND = ["/bin/sh", "-c", "echo STOP-REC $(date) >> /tmp/rec-test.log"]
   ```

2. Create and start a recording task:
   ```bash
   task add +VIDEO "test"
   task start <id>
   cat /tmp/rec-test.log  # should show START-REC entry
   ```

3. Stop the task:
   ```bash
   task stop <id>
   cat /tmp/rec-test.log  # should show STOP-REC entry
   ```

4. Verify debug log:
   ```bash
   tail -20 /tmp/aw-watcher-taskwarrior-debug.log | grep recording
   ```

5. Test unconfigured state (reset to `None`):
   - No recording entries should appear in `/tmp/rec-test.log`
   - Debug log should show "not configured, skipping" messages
   - `task start` / `task stop` must still work normally (hook must not fail)

## Implementation Notes

- **Module independence**: `recording_control.py` has its own `debug_log()` function (a copy of the hook's) so it doesn't import from the hook file. This keeps the module independently testable and avoids import-time side effects.
- **Same debug log file**: Both the hook and the module write to `/tmp/aw-watcher-taskwarrior-debug.log` so all diagnostics are chronologically interleaved in one stream.
- **Subprocess pattern**: Both functions use `subprocess.Popen` with `os.setsid`, `close_fds=True`, and redirected I/O, matching the existing `start_daemon()` pattern — ensures the recording process outlives the hook and doesn't block task operations.
- **Error handling**: All subprocess calls wrapped in `try/except Exception` and logged; never raised, maintaining the hook's guarantee that it never fails.
