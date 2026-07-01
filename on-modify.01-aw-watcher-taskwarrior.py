#!/usr/bin/env python3
# TaskWarrior Hook for ActivityWatch
# Author: Emir Herrera González
# TaskWarrior hook to send heartbeats to ActivityWatch

# License: GNU GPLv3
# FIX: Something is werid when checking the response, right now it claims that the event was successfully created but the event per se doesn't exists on the bucket, or is it a collision?
"""
[2026-06-30T00:57:25.870644+00:00] Heartbeat: Instalar contactos y apagadores | Tags: ['OFFLINE']
[2026-06-30T00:57:28.874934+00:00] Heartbeat: Instalar contactos y apagadores | Tags: ['OFFLINE']
[2026-06-30T00:57:31.879131+00:00] Heartbeat: Instalar contactos y apagadores | Tags: ['OFFLINE']
[2026-06-30T03:30:58.477864+00:00] Checking offline gaps: 9206.6s since last heartbeat
[2026-06-30T03:30:59.547095+00:00] Querying aw-watcher-afk_HerreraMonroy from 2026-06-30 00:57:31.878781+00:00 to 2026-06-30 03:30:58.476225+00:00
[2026-06-30T03:31:18.598536+00:00] No afk events found - system was offline for entire gap (9206.6s)
[2026-06-30T03:31:18.616015+00:00] Posting to http://localhost:5600/api/0/buckets/aw-watcher-taskwarrior_HerreraMonroy/events: {'timestamp': '2026-06-30 00:57:31.878782+00:00', 'data': {'title': 'Instalar contactos y apagadores', 'project': 'Ecosistema.Hábitat.Mantenimiento del hogar.Recámara', 'tags': ['OFFLINE'], 'uuid': 'e7e9d2b1-9f68-484c-ad44-29c9e5889027'}, 'duration': 9206}
[2026-06-30T03:31:19.061047+00:00] Response status: 200, text: null

[2026-06-30T03:31:19.061412+00:00] ✓ Created offline event for complete gap: 9206.6s
[2026-06-30T03:31:19.101486+00:00] Heartbeat: Instalar contactos y apagadores | Tags: ['OFFLINE']
[2026-06-30T03:31:22.105818+00:00] Heartbeat: Instalar contactos y apagadores | Tags: ['OFFLINE']
[2026-06-30T03:31:25.110123+00:00] Heartbeat: Instalar contactos y apagadores | Tags: ['OFFLINE']
"""

import json
import os
import sys
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from socket import gethostname
from requests import post

from aw_client import ActivityWatchClient
from aw_core.models import Event

# --- Constants ---
# Unix domain socket used for daemon control
SOCKET_FILE = "/tmp/aw-watcher-taskwarrior.sock"
# Debug log file
DEBUG_LOG_FILE = "/tmp/aw-watcher-taskwarrior-debug.log"

# Daemon settings
HEARTBEAT_FREQUENCY = 3  # seconds
PULSETIME = HEARTBEAT_FREQUENCY + 2  # Must be > HEARTBEAT_FREQUENCY
COMMIT_INTERVAL = 15  # seconds
SERVER_RETRY_INTERVAL = 30  # seconds
OFFLINE_DETECTION_THRESHOLD = HEARTBEAT_FREQUENCY * 2  # seconds

# Socket protocol
SOCKET_RECV_TIMEOUT = 2
SOCKET_BACKLOG = 1
DAEMON_STOP_WAIT_TIMEOUT = 2
ACTION_STOP = "stop"
ACTION_UPDATE = "update"
ACTION_STATUS = "status"


def debug_log(message):
    """Log debug messages to a file with timestamp."""
    try:
        with open(DEBUG_LOG_FILE, "a") as f:
            timestamp = datetime.now(timezone.utc).isoformat()
            f.write(f"[{timestamp}] {message}\n")
            f.flush()
    except Exception:
        pass


class TaskState:
    def __init__(self, task):
        self._lock = threading.Lock()
        self.uuid = task.get("uuid")
        self._description = task.get("description", "unknown")
        self._project = task.get("project", "No project")
        self._tags = task.get("tags", [])

    def update(self, new_task):
        with self._lock:
            if "description" in new_task:
                self._description = new_task["description"]
            if "project" in new_task:
                self._project = new_task["project"]
            if "tags" in new_task:
                self._tags = new_task["tags"]

    def snapshot(self):
        with self._lock:
            tags = list(self._tags)
            return {
                "title": self._description,
                "project": self._project,
                "tags": tags,
                "uuid": self.uuid,
            }, tags


def read_task_from_stdin():
    """Reads and parses the single task JSON piped to the daemon's stdin at startup."""
    raw = sys.stdin.read()
    if not raw.strip():
        debug_log("Daemon received no task data on stdin, aborting")
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        debug_log(f"Daemon failed to parse task JSON from stdin: {e}")
        return None


def setup_socket():
    """Binds and listens on SOCKET_FILE. Removes a stale file first if present."""
    if os.path.exists(SOCKET_FILE):
        os.remove(SOCKET_FILE)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(SOCKET_FILE)
    except OSError as e:
        debug_log(f"Failed to bind {SOCKET_FILE}: {e}")
        sock.close()
        raise
    sock.listen(SOCKET_BACKLOG)
    return sock


def cleanup_socket(sock):
    try:
        sock.close()
    except OSError:
        pass
    try:
        if os.path.exists(SOCKET_FILE):
            os.remove(SOCKET_FILE)
    except OSError:
        pass


def recv_line(conn, timeout=SOCKET_RECV_TIMEOUT, bufsize=4096):
    conn.settimeout(timeout)
    buf = b""
    try:
        while not buf.endswith(b"\n"):
            chunk = conn.recv(bufsize)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        pass
    return buf.decode("utf-8", errors="replace").strip()


def socket_listener(sock, task_state, stop_event):
    while True:
        try:
            conn, _ = sock.accept()
        except OSError:
            debug_log("Socket listener exiting (socket closed)")
            return
        with conn:
            line = recv_line(conn)
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                debug_log(f"Ignoring malformed socket message: {line!r}")
                continue

            action = msg.get("action")
            uuid = msg.get("uuid")

            if action == ACTION_STOP:
                if uuid == task_state.uuid:
                    debug_log(f"Received stop for tracked task {uuid}")
                    stop_event.set()
                else:
                    debug_log(f"Ignoring stop for {uuid}, tracking {task_state.uuid}")
            elif action == ACTION_UPDATE:
                if uuid == task_state.uuid:
                    task_state.update(msg.get("task", {}))
                    debug_log(f"Updated tracked task metadata for {uuid}")
                else:
                    debug_log(f"Ignoring update for {uuid}, tracking {task_state.uuid}")
            elif action == ACTION_STATUS:
                try:
                    conn.sendall((json.dumps({"uuid": task_state.uuid}) + "\n").encode())
                except OSError:
                    pass
            else:
                debug_log(f"Unknown socket action: {action!r}")


def heartbeat_daemon():
    """
    This function runs as a background daemon, sending heartbeats for the active task.
    """
    debug_log("=== Daemon started ===")

    task = read_task_from_stdin()
    if task is None or not task.get("uuid"):
        debug_log("No valid task on stdin, daemon exiting")
        sys.exit(1)

    task_state = TaskState(task)
    stop_event = threading.Event()

    try:
        sock = setup_socket()
    except OSError:
        sys.exit(1)

    listener = threading.Thread(
        target=socket_listener, args=(sock, task_state, stop_event), daemon=True
    )
    listener.start()

    client = ActivityWatchClient("aw-watcher-taskwarrior", testing=False)
    bucket_id = f"aw-watcher-taskwarrior_{gethostname()}"
    debug_log(f"Connecting to bucket: {bucket_id}")

    try:
        while not stop_event.is_set():
            try:
                client.create_bucket(bucket_id, event_type="task-activity")
                debug_log("Bucket created/connected successfully")
                break
            except Exception as e:
                debug_log(
                    f"Failed to create bucket, retrying in {SERVER_RETRY_INTERVAL}s: {e}"
                )
                stop_event.wait(SERVER_RETRY_INTERVAL)

        if stop_event.is_set():
            debug_log("Stop received during bucket setup, exiting before first heartbeat")
            return

        last_heartbeat_time = datetime.now(timezone.utc)

        with client:
            while not stop_event.is_set():
                payload_task_data, task_tags = task_state.snapshot()
                task_desc = payload_task_data["title"]
                now = datetime.now(timezone.utc)

                if "OFFLINE" in task_tags:
                    offline_event_created = check_and_create_offline_events(
                        client, bucket_id, last_heartbeat_time, now, payload_task_data
                    )
                    if offline_event_created:
                        last_heartbeat_time = now
                        stop_event.wait(HEARTBEAT_FREQUENCY)
                        continue

                last_heartbeat_time = now
                heartbeat_event = Event(timestamp=now, data=payload_task_data)
                client.heartbeat(
                    bucket_id,
                    heartbeat_event,
                    pulsetime=PULSETIME,
                    queued=True,
                    commit_interval=COMMIT_INTERVAL,
                )
                debug_log(f"Heartbeat: {task_desc} | Tags: {task_tags}")

                stop_event.wait(HEARTBEAT_FREQUENCY)

        debug_log("=== Daemon stopping (stop signal received) ===")
    finally:
        cleanup_socket(sock)


def is_daemon_running():
    """True if a daemon is actively listening on SOCKET_FILE.
    A socket file left behind by a crashed daemon (stale) is cleaned up here."""
    if not os.path.exists(SOCKET_FILE):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(SOCKET_RECV_TIMEOUT)
            s.connect(SOCKET_FILE)
        return True
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError) as e:
        debug_log(f"Stale socket file detected ({e}), removing")
        try:
            os.remove(SOCKET_FILE)
        except OSError:
            pass
        return False


def query_daemon_uuid():
    """Returns the uuid the running daemon is tracking, or None if no daemon
    is running / it didn't answer in time. Never raises."""
    if not os.path.exists(SOCKET_FILE):
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(SOCKET_RECV_TIMEOUT)
            s.connect(SOCKET_FILE)
            s.sendall((json.dumps({"action": ACTION_STATUS}) + "\n").encode())
            line = recv_line(s)
        if not line:
            return None
        return json.loads(line).get("uuid")
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout,
            OSError, json.JSONDecodeError) as e:
        debug_log(f"query_daemon_uuid failed: {e}")
        return None


def _connect_with_retry(attempts=3, delay=0.2):
    """Small bounded retry to absorb the start->stop race (daemon subprocess
    may not have finished binding its socket yet). Total worst case ~0.6s."""
    last_err = None
    for _ in range(attempts):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(SOCKET_RECV_TIMEOUT)
            s.connect(SOCKET_FILE)
            return s
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            last_err = e
            time.sleep(delay)
    debug_log(f"Could not connect to daemon socket after retries: {last_err}")
    return None


def send_stop_signal(task_uuid):
    """Best-effort; must never raise (a hook exception can block TaskWarrior)."""
    s = _connect_with_retry()
    if s is None:
        return
    try:
        with s:
            s.sendall((json.dumps({"action": ACTION_STOP, "uuid": task_uuid}) + "\n").encode())
    except OSError as e:
        debug_log(f"Error sending stop signal: {e}")


def send_update_signal(task_uuid, new_task):
    s = _connect_with_retry(attempts=1)
    if s is None:
        return
    try:
        with s:
            msg = {"action": ACTION_UPDATE, "uuid": task_uuid, "task": new_task}
            s.sendall((json.dumps(msg) + "\n").encode())
    except OSError as e:
        debug_log(f"Error sending update signal: {e}")


def wait_for_daemon_exit(timeout=DAEMON_STOP_WAIT_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_daemon_running():
            return True
        time.sleep(0.1)
    debug_log("Timed out waiting for previous daemon to exit")
    return False


def start_daemon(new_task):
    """Starts the heartbeat daemon as a detached background process, piping
    the task JSON to its stdin so it knows what to track at startup."""
    command = [sys.executable, __file__, "--daemon"]
    proc = subprocess.Popen(
        command,
        close_fds=True,
        preexec_fn=os.setsid,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if proc.stdin:
        try:
            proc.stdin.write(json.dumps(new_task).encode())
        finally:
            proc.stdin.close()


def check_and_create_offline_events(
    client, bucket_id, last_heartbeat_time: datetime, now: datetime, payload_task_data
) -> bool:
    """
    Checks if system was offline since last heartbeat by querying the afk bucket.
    Only checks if there's been a gap in heartbeats (> 2 heartbeat intervals).
    Creates offline events for gaps where there was no user activity.

    Returns True if an offline event was created, False otherwise.
    """
    try:
        time_since_heartbeat = (now - last_heartbeat_time).total_seconds()

        # Only check if heartbeats have actually stopped (gap > threshold)
        if time_since_heartbeat <= OFFLINE_DETECTION_THRESHOLD:
            return False

        debug_log(
            f"Checking offline gaps: {time_since_heartbeat:.1f}s since last heartbeat"
        )

        # Query afk bucket to check for user inactivity
        afk_bucket_id = f"aw-watcher-afk_{gethostname()}"
        debug_log(f"Querying {afk_bucket_id} from {last_heartbeat_time} to {now}")
        offline_event_start = last_heartbeat_time + timedelta(0, 0, 1)

        afk_events = client.get_events(
            afk_bucket_id,
            start=(timedelta(0, HEARTBEAT_FREQUENCY, 0) + last_heartbeat_time),
            end=now,
            limit=-1,
        )

        gap_duration = (now - offline_event_start).total_seconds()
        if not afk_events:
            # No afk events means system was completely off (no activity data at all)
            debug_log(
                f"No afk events found - system was offline for entire gap ({gap_duration:.1f}s)"
            )
            try:
                url = f"http://localhost:5600/api/0/buckets/{bucket_id}/events"
                payload = {
                    "timestamp": str(offline_event_start),
                    "data": payload_task_data,
                    "duration": int(gap_duration),
                }
                debug_log(f"Posting to {url}: {payload}")
                response = post(url, json=payload, timeout=5)
                debug_log(
                    f"Response status: {response.status_code}, text: {response.text}"
                )
                if response.status_code == 200:
                    debug_log(
                        f"✓ Created offline event for complete gap: {gap_duration:.1f}s"
                    )
                    return True
                else:
                    debug_log(
                        f"✗ Failed to create offline event: HTTP {response.status_code}"
                    )
            except Exception as insert_err:
                debug_log(
                    f"✗ Exception during insert: {type(insert_err).__name__}: {insert_err}"
                )
            return False

        debug_log(
            f"Found {len(afk_events)} afk events (covered by heartbeats, no offline events needed)"
        )
        return False

    except Exception as e:
        # Silently fail to avoid daemon crash
        debug_log(f"Error in offline detection: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        heartbeat_daemon()
        sys.exit(0)

    old_task_str = sys.stdin.readline()
    new_task_str = sys.stdin.readline()
    old_task = json.loads(old_task_str) if old_task_str.strip() else {}
    new_task = json.loads(new_task_str) if new_task_str.strip() else {}

    # TaskWarrior hook protocol: must echo the (possibly modified) task back.
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

    try:
        if is_active_after and not is_active_before:
            # Case 1: task started.
            current_uuid = query_daemon_uuid()

            if current_uuid == task_uuid:
                # Shouldn't normally happen (is_active_before was False), but
                # defend against duplicate/out-of-order hook invocations.
                debug_log(f"Task {task_uuid} already tracked, ignoring duplicate start")

            elif current_uuid is not None:
                # Single-daemon policy: starting a new task stops tracking of the old one.
                debug_log(f"Switching daemon from {current_uuid} to {task_uuid}")
                send_stop_signal(current_uuid)
                wait_for_daemon_exit()
                start_daemon(new_task)

            else:
                start_daemon(new_task)

        elif not is_active_after and is_active_before:
            # Case 2: task stopped, completed, or deleted while active.
            send_stop_signal(task_uuid)

        elif is_active_after and is_active_before:
            # Case 3: task modified while active (description/project/tags).
            current_uuid = query_daemon_uuid()
            if current_uuid == task_uuid:
                send_update_signal(task_uuid, new_task)
            else:
                debug_log(
                    f"Modified task {task_uuid} is not the daemon's tracked "
                    f"task ({current_uuid}); ignoring update"
                )
        # else: task inactive before and after -> nothing to do.

    except Exception as e:
        # A hook exception must never propagate: TaskWarrior blocks the
        # underlying task command (start/stop/modify) on hook failure.
        debug_log(f"Unhandled hook error: {type(e).__name__}: {e}")

    sys.exit(0)
