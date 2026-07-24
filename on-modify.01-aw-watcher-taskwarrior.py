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

    # Try to create ActivityWatch client with unique app name to avoid singleton conflicts
    # Use PID-based name first to guarantee uniqueness and avoid conflicts
    client = None
    client_names = [
        f"aw-watcher-taskwarrior-{os.getpid()}",
        "aw-watcher-taskwarrior",
        "aw-watcher-taskwarrior-recovery",
    ]

    for client_name in client_names:
        try:
            debug_log(f"Trying to connect to ActivityWatch as '{client_name}'")
            client = ActivityWatchClient(client_name, testing=False)
            debug_log(f"✓ Connected as '{client_name}'")
            break
        except Exception as e:
            debug_log(f"Failed with '{client_name}': {type(e).__name__}: {e}")
            continue

    if client is None:
        debug_log("Failed to create ActivityWatch client with any app name")
        cleanup_socket(sock)
        sys.exit(1)

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

        try:
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
                    if send_heartbeat_with_retry(client, bucket_id, heartbeat_event):
                        debug_log(f"Heartbeat: {task_desc} | Tags: {task_tags}")

                    stop_event.wait(HEARTBEAT_FREQUENCY)

            debug_log("=== Daemon stopping (stop signal received) ===")
        except Exception as e:
            debug_log(f"✗ Error in daemon heartbeat loop: {type(e).__name__}: {e}")
            import traceback
            debug_log(f"Traceback: {traceback.format_exc()}")
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


def parse_tw_timestamp(tw_ts):
    """Parse TaskWarrior UTC timestamp (format: 20260723T200958Z) into aware datetime."""
    try:
        return datetime.strptime(tw_ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except Exception as e:
        debug_log(f"Error parsing TaskWarrior timestamp {tw_ts}: {e}")
        return None


def get_last_tracked_event_end(client, bucket_id, task_uuid, since):
    """
    Query the taskwarrior bucket for events matching task_uuid between since and now.
    Returns the end time of the most recent matching event, or since if none found.
    """
    try:
        now = datetime.now(timezone.utc)
        events = client.get_events(bucket_id, start=since, end=now, limit=-1)

        if not events:
            debug_log(f"No events found for task {task_uuid} since {since}, using task start as baseline")
            return since

        # Filter to events from this task (defend against stale events from previous tasks)
        matching_events = [e for e in events if e.data.get("uuid") == task_uuid]
        if not matching_events:
            debug_log(f"No events found for task {task_uuid} in bucket {bucket_id}, using task start as baseline")
            return since

        # Find the latest event end (duration is already a timedelta)
        last_event = max(matching_events, key=lambda e: e.timestamp + e.duration)
        last_event_end = last_event.timestamp + last_event.duration
        debug_log(f"Last tracked event for {task_uuid} ended at {last_event_end}")
        return last_event_end
    except Exception as e:
        debug_log(f"Error querying last tracked event: {type(e).__name__}: {e}, using task start as baseline")
        return since


def get_active_task():
    """Query TaskWarrior for the active task. Returns task dict or None."""
    try:
        result = subprocess.run(
            ["task", "rc.hooks:off", "status:pending", "start.any:", "export"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            tasks = json.loads(result.stdout)
            if tasks:
                return tasks[0]
        return None
    except Exception as e:
        debug_log(f"Error querying active task: {e}")
        return None


def recover_daemon():
    """
    Recovery mode: Checks for active task and resumes tracking.
    Queries TaskWarrior for task start time and ActivityWatch for last tracked event.
    If task is OFFLINE and gap is significant, backfills using afk-verified offline detection.
    If task is ONLINE, just resumes tracking without backfilling.
    """
    debug_log("=== Recovery mode started ===")

    active_task = get_active_task()
    if not active_task:
        debug_log("No active task found, recovery aborted")
        return False

    task_uuid = active_task.get("uuid")
    task_desc = active_task.get("description", "unknown")
    is_offline = "OFFLINE" in active_task.get("tags", [])

    debug_log(
        f"Found active task: {task_desc} (OFFLINE: {is_offline})"
    )

    # Check if daemon is already running
    if is_daemon_running():
        debug_log(f"Daemon already running for task {task_uuid}, recovery not needed")
        return False

    # Parse task start time from TaskWarrior
    tw_start = active_task.get("start")
    task_start_time = parse_tw_timestamp(tw_start) if tw_start else None
    if not task_start_time:
        debug_log(f"Could not parse task start time ({tw_start}), skipping gap computation")
        last_event_end = None
    else:
        debug_log(f"Task started at {task_start_time}")

        # Query ActivityWatch for last tracked event for this task
        bucket_id = f"aw-watcher-taskwarrior_{gethostname()}"
        try:
            client = ActivityWatchClient("aw-watcher-taskwarrior", testing=False)
            # Best-effort bucket creation (matches daemon's approach)
            try:
                with client:
                    client.create_bucket(bucket_id, event_type="task-activity")
                    last_event_end = get_last_tracked_event_end(client, bucket_id, task_uuid, task_start_time)
            except Exception as e:
                debug_log(f"Could not query bucket: {type(e).__name__}: {e}, using task start as baseline")
                last_event_end = task_start_time
        except Exception as e:
            debug_log(f"Could not create ActivityWatch client: {type(e).__name__}: {e}, using task start as baseline")
            last_event_end = task_start_time

    # Start daemon
    debug_log(f"Starting daemon for task {task_uuid}")
    start_daemon(active_task)

    # Wait for daemon to bind socket (up to 5 seconds with 0.1s polling).
    # Use direct socket test without aggressive cleanup to avoid race conditions.
    for attempt in range(50):
        time.sleep(0.1)
        try:
            if os.path.exists(SOCKET_FILE):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(SOCKET_RECV_TIMEOUT)
                    s.connect(SOCKET_FILE)
                debug_log("Daemon started successfully")
                break
        except (ConnectionRefusedError, FileNotFoundError, socket.timeout, OSError):
            pass
    else:
        debug_log("Daemon socket binding timeout after 5s")
        # Final check: if daemon IS running now (maybe it started late), recovery succeeded
        if is_daemon_running():
            debug_log("Daemon is actually running despite timeout, recovery succeeded")
            return True
        debug_log("Failed to start daemon during recovery (socket binding timeout after 5s)")
        return False

    # Backfill gaps if task is marked OFFLINE (both initial and final gaps)
    now = datetime.now(timezone.utc)
    if task_start_time is not None and last_event_end is not None:
        # Check gap from task start to first event (if any)
        gap_from_start = (last_event_end - task_start_time).total_seconds()
        # Check gap from last event to now
        gap_to_now = (now - last_event_end).total_seconds()

        debug_log(f"Timeline: task started at {task_start_time}, last event ended at {last_event_end}, now {now}")
        debug_log(f"Gaps: {gap_from_start:.1f}s (start→first event) + {gap_to_now:.1f}s (last event→now)")

        if is_offline and (gap_from_start > OFFLINE_DETECTION_THRESHOLD or gap_to_now > OFFLINE_DETECTION_THRESHOLD):
            debug_log(f"Task is OFFLINE with significant gaps, backfilling")
            try:
                bucket_id = f"aw-watcher-taskwarrior_{gethostname()}"
                payload_task_data = {
                    "title": task_desc,
                    "project": active_task.get("project", "No project"),
                    "tags": active_task.get("tags", []),
                    "uuid": task_uuid,
                }

                client = ActivityWatchClient("aw-watcher-taskwarrior", testing=False)
                with client:
                    # Backfill initial gap (from task start to first event, if any)
                    if gap_from_start > OFFLINE_DETECTION_THRESHOLD:
                        debug_log(f"Backfilling initial gap: {gap_from_start:.1f}s from {task_start_time} to {last_event_end}")
                        event_created_initial = check_and_create_offline_events(
                            client, bucket_id, task_start_time, last_event_end, payload_task_data
                        )
                        if event_created_initial:
                            debug_log(f"✓ Created offline event for initial gap: {gap_from_start:.1f}s")
                        else:
                            debug_log(f"Initial gap did not require backfill (afk activity detected)")

                    # Backfill final gap (from last event to now)
                    if gap_to_now > OFFLINE_DETECTION_THRESHOLD:
                        debug_log(f"Backfilling final gap: {gap_to_now:.1f}s from {last_event_end} to {now}")
                        event_created_final = check_and_create_offline_events(
                            client, bucket_id, last_event_end, now, payload_task_data
                        )
                        if event_created_final:
                            debug_log(f"✓ Created offline event for final gap: {gap_to_now:.1f}s")
                        else:
                            debug_log(f"Final gap did not require backfill (afk activity detected)")
            except Exception as e:
                debug_log(f"✗ Exception during offline backfill: {type(e).__name__}: {e}")
        elif is_offline:
            debug_log(f"Task is OFFLINE but gaps ({gap_from_start:.1f}s + {gap_to_now:.1f}s) are within threshold, no backfill needed")
        else:
            debug_log(f"Task is ONLINE, resuming without backfill (gaps: {gap_from_start:.1f}s + {gap_to_now:.1f}s)")
    else:
        debug_log("Could not determine task start or event end, resuming without backfill")

    debug_log("=== Recovery completed ===")
    return True


def start_daemon(new_task):
    """Starts the heartbeat daemon as a detached background process, piping
    the task JSON to its stdin so it knows what to track at startup."""
    command = [sys.executable, __file__, "--daemon"]
    with open("/tmp/aw-watcher-daemon-stderr.log", "a") as stderr_file:
        proc = subprocess.Popen(
            command,
            close_fds=True,
            preexec_fn=os.setsid,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
        )
        if proc.stdin:
            try:
                proc.stdin.write(json.dumps(new_task).encode())
            finally:
                proc.stdin.close()


def send_heartbeat_with_retry(client, bucket_id, heartbeat_event, max_retries=3):
    """
    Send heartbeat with exponential backoff retry.
    Returns True if successful, False if all retries exhausted.
    Logs errors but doesn't crash the daemon.
    """
    for attempt in range(max_retries):
        try:
            client.heartbeat(
                bucket_id,
                heartbeat_event,
                pulsetime=PULSETIME,
                queued=False,
            )
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                backoff = min(HEARTBEAT_FREQUENCY * (2 ** attempt), 5)
                debug_log(
                    f"Heartbeat failed (attempt {attempt + 1}/{max_retries}): {type(e).__name__}: {e}. "
                    f"Retrying in {backoff:.1f}s"
                )
                time.sleep(backoff)
            else:
                debug_log(
                    f"Heartbeat failed after {max_retries} attempts: {type(e).__name__}: {e}. "
                    f"Skipping this heartbeat cycle."
                )
                return False
    return False


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
            url = f"http://localhost:5600/api/0/buckets/{bucket_id}/events"
            payload = {
                "timestamp": offline_event_start.isoformat(),
                "data": payload_task_data,
                "duration": int(gap_duration),
            }
            debug_log(f"Posting to {url}: {payload}")

            for attempt in range(3):
                try:
                    response = post(url, json=payload, timeout=5)
                    debug_log(
                        f"Response status: {response.status_code}, text: {response.text}"
                    )
                    if response.status_code == 200:
                        debug_log(
                            f"✓ Created offline event for complete gap: {gap_duration:.1f}s"
                        )
                        return True
                    elif attempt < 2:
                        backoff = min(HEARTBEAT_FREQUENCY * (2 ** attempt), 5)
                        debug_log(
                            f"✗ HTTP {response.status_code}, retrying in {backoff:.1f}s"
                        )
                        time.sleep(backoff)
                    else:
                        debug_log(
                            f"✗ Failed to create offline event after retries: HTTP {response.status_code}"
                        )
                except Exception as insert_err:
                    if attempt < 2:
                        backoff = min(HEARTBEAT_FREQUENCY * (2 ** attempt), 5)
                        debug_log(
                            f"✗ Exception during insert: {type(insert_err).__name__}: {insert_err}. "
                            f"Retrying in {backoff:.1f}s"
                        )
                        time.sleep(backoff)
                    else:
                        debug_log(
                            f"✗ Exception during insert (final attempt): {type(insert_err).__name__}: {insert_err}"
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

    if len(sys.argv) > 1 and sys.argv[1] == "--recover":
        success = recover_daemon()
        sys.exit(0 if success else 1)

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
