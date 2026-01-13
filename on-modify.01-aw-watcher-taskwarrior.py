#!/usr/bin/env python3
# TaskWarrior Hook for ActivityWatch
# Author: Emir Herrera González
# TaskWarrior hook to send heartbeats to ActivityWatch

# License: GNU GPLv3

import json
import os
import sys
import subprocess
import time
from datetime import datetime, timezone
from socket import gethostname

from aw_client import ActivityWatchClient
from aw_core.models import Event

# --- Constants ---
# File to store the LIFO queue of active tasks
ACTIVE_TASK_FILE = "/tmp/active-task.json"
# File to store the PID of the daemon process
PID_FILE = "/tmp/aw-watcher-taskwarrior.pid"

# Daemon settings
HEARTBEAT_FREQUENCY = 3  # seconds
PULSETIME = HEARTBEAT_FREQUENCY + 2  # Must be > HEARTBEAT_FREQUENCY
COMMIT_INTERVAL = 15  # seconds
SERVER_RETRY_INTERVAL = 30  # seconds


def heartbeat_daemon():
    """
    This function runs as a background daemon, sending heartbeats for the active task.
    """
    # Write PID to file to prevent multiple instances
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    # Connect to ActivityWatch server
    client = ActivityWatchClient("aw-watcher-taskwarrior", testing=False)
    bucket_id = f"aw-watcher-taskwarrior_{gethostname()}"

    # Loop to ensure bucket exists, retry if aw-server is not available
    while True:
        try:
            client.create_bucket(bucket_id, event_type="task-activity")
            break
        except Exception:
            time.sleep(SERVER_RETRY_INTERVAL)

    with client:
        while os.path.exists(ACTIVE_TASK_FILE):
            try:
                with open(ACTIVE_TASK_FILE, "r") as f:
                    active_tasks = json.load(f)
            except (IOError, json.JSONDecodeError):
                time.sleep(1)
                continue

            # This handles a transient state where the task file is empty but
            # not yet deleted by the hook script. It prevents a crash.
            if not active_tasks:
                time.sleep(1)
                continue

            # Get the last task from the LIFO queue (most recently started)
            current_task = active_tasks[-1]

            heartbeat_data = {
                "title": current_task.get("description", ""),
                "project": current_task.get("project", "No project"),
                "tags": current_task.get("tags", []),
                "uuid": current_task.get("uuid"),
            }
            now = datetime.now(timezone.utc)
            heartbeat_event = Event(timestamp=now, data=heartbeat_data)

            client.heartbeat(
                bucket_id,
                heartbeat_event,
                pulsetime=PULSETIME,
                queued=True,
                commit_interval=COMMIT_INTERVAL,
            )

            time.sleep(HEARTBEAT_FREQUENCY)

    # Clean up PID file on exit
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def get_active_tasks():
    """Reads the list of active tasks from the JSON file."""
    if not os.path.exists(ACTIVE_TASK_FILE):
        return []
    try:
        with open(ACTIVE_TASK_FILE, "r") as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError):
        return []


def write_active_tasks(tasks):
    """Writes the list of active tasks to the JSON file."""
    with open(ACTIVE_TASK_FILE, "w") as f:
        json.dump(tasks, f, indent=2)


def is_daemon_running():
    """Checks if the heartbeat daemon is currently running."""
    if not os.path.exists(PID_FILE):
        return False
    with open(PID_FILE, "r") as f:
        try:
            pid = int(f.read())
        except ValueError:
            return False

    # Check if a process with this PID is running (Unix-like specific)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def start_daemon():
    """
    Starts the heartbeat daemon as a detached background process.
    """
    if is_daemon_running():
        return

    # Command to re-execute this script with the --daemon flag
    command = [sys.executable, __file__, "--daemon"]

    # Use Popen to launch a detached process.
    # preexec_fn=os.setsid ensures the new process is in its own session.
    # Redirecting stdin/out/err to DEVNULL is crucial for full detachment.
    subprocess.Popen(
        command,
        close_fds=True,
        preexec_fn=os.setsid,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    # If '--daemon' is passed, this script becomes the daemon.
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        heartbeat_daemon()
        sys.exit(0)

    # Otherwise, it runs as the TaskWarrior hook.
    old_task_str = sys.stdin.readline()
    new_task_str = sys.stdin.readline()

    old_task = json.loads(old_task_str) if old_task_str.strip() else {}
    new_task = json.loads(new_task_str) if new_task_str.strip() else {}

    # TaskWarrior hook protocol requires writing the modified task back to stdout
    if new_task:
        print(json.dumps(new_task))
    elif old_task:
        print(json.dumps(old_task))
    else:
        print("{}")

    task_uuid = new_task.get("uuid") or old_task.get("uuid")
    if not task_uuid:
        sys.exit(0)

    is_active_before = "start" in old_task
    is_active_after = "start" in new_task

    active_tasks = get_active_tasks()
    active_uuids = {t["uuid"] for t in active_tasks}

    # Case 1: Task is started
    if is_active_after and task_uuid not in active_uuids:
        active_tasks.append(new_task)
        write_active_tasks(active_tasks)
        start_daemon()

    # Case 2: Task is stopped or deleted while active
    elif not is_active_after and task_uuid in active_uuids:
        active_tasks = [t for t in active_tasks if t["uuid"] != task_uuid]
        if not active_tasks:
            if os.path.exists(ACTIVE_TASK_FILE):
                os.remove(ACTIVE_TASK_FILE)
        else:
            write_active_tasks(active_tasks)

    # Case 3: Task is modified while active
    elif is_active_after and task_uuid in active_uuids:
        # Find and update the task in the list to reflect changes
        for i, task in enumerate(active_tasks):
            if task["uuid"] == task_uuid:
                active_tasks[i] = new_task
                break
        write_active_tasks(active_tasks)

    sys.exit(0)
