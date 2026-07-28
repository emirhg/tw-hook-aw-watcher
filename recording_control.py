#!/usr/bin/env python3
# Recording control module for tw-hook-aw-watcher.
# Launches/stops an external screen-recording command when a task tagged
# VIDEO/RECORD starts/stops. Independent of TaskWarrior/ActivityWatch
# specifics; callers pass in only what's needed (task dict / uuid).

import os
import subprocess
from datetime import datetime, timezone

# --- Constants ---
DEBUG_LOG_FILE = "/tmp/aw-watcher-taskwarrior-debug.log"

# Argument-list form (no shell=True), e.g.:
#   RECORD_START_COMMAND = ["/path/to/start-recording.sh"]
#   RECORD_STOP_COMMAND = ["/path/to/stop-recording.sh"]
RECORD_START_COMMAND = [
    "/home/emirhg/workspace/observer/record-desktop-activity.sh",
    "--daemon",
]
RECORD_STOP_COMMAND = [
    "/home/emirhg/workspace/observer/record-desktop-activity.sh",
    "--stop",
]

RECORD_TAGS = ("VIDEO", "RECORD")


def debug_log(message):
    """Log debug messages to a file with timestamp."""
    try:
        with open(DEBUG_LOG_FILE, "a") as f:
            timestamp = datetime.now(timezone.utc).isoformat()
            f.write(f"[{timestamp}] {message}\n")
            f.flush()
    except Exception:
        pass


def has_record_tag(tags):
    """True if the given tag list contains VIDEO and/or RECORD."""
    return any(tag in tags for tag in RECORD_TAGS)


def start_recording(task):
    """Launch RECORD_START_COMMAND as a detached background process.
    Never raises; logs a warning and no-ops if unconfigured."""
    if not RECORD_START_COMMAND:
        debug_log("start_recording: RECORD_START_COMMAND not configured, skipping")
        return
    try:
        uuid = task.get("uuid")
        command = list(RECORD_START_COMMAND) + ["--task", uuid]
        subprocess.Popen(
            command,
            close_fds=True,
            preexec_fn=os.setsid,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        debug_log(f"Started recording for task {uuid}: {command}")
    except Exception as e:
        debug_log(f"Error starting recording: {type(e).__name__}: {e}")


def stop_recording(task_uuid):
    """Launch RECORD_STOP_COMMAND (fire-and-forget). Never raises; logs a
    warning and no-ops if unconfigured."""
    if not RECORD_STOP_COMMAND:
        debug_log("stop_recording: RECORD_STOP_COMMAND not configured, skipping")
        return
    try:
        command = list(RECORD_STOP_COMMAND) + ["--task", task_uuid]
        subprocess.Popen(
            command,
            close_fds=True,
            preexec_fn=os.setsid,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        debug_log(f"Stopped recording for task {task_uuid}: {command}")
    except Exception as e:
        debug_log(f"Error stopping recording: {type(e).__name__}: {e}")
