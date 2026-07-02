# Fix Recommendations for Offline Event Persistence Issue

## Root Cause (Confirmed via Agent Investigation)

**The problem is NOT that offline events fail to post, but that stale queued heartbeats corrupt them after posting.**

### The Bug Scenario

1. **Before suspend:** Daemon sends heartbeats via `client.heartbeat(..., queued=True, commit_interval=15)`
   - Heartbeats get cached in `aw_client`'s SQLite persistent queue: `~/.local/share/aw-client/queued/`
   - Some heartbeats may not have been flushed yet (< 15s since last flush)
   - The queue persists to disk even during system suspend

2. **System suspends:** Process freezes; RequestQueue background thread pauses

3. **System resumes:** 
   - Daemon detects 6+ hour offline gap
   - Successfully POSTs offline event directly to `/events` endpoint (200 OK)
   - But RequestQueue thread wakes up and resumes processing the SQLite queue

4. **The race condition:**
   - Stale heartbeats from BEFORE suspend are flushed to `/heartbeat` endpoint
   - These have timestamps EARLIER than the offline event just inserted
   - The server-side merge logic processes the stale heartbeat and may:
     - Incorrectly merge it with the offline event (wrong duration calculation)
     - Corrupt the offline event's representation in the database
     - Cause the offline event to not appear in queries

### Key Code References

| Issue | Location | Problem |
|-------|----------|---------|
| Persistent queue creation | `aw_client/client.py:434-436` | SQLite queue survives suspend; stale heartbeats replay post-resume |
| Queue flushing | `aw_client/client.py:520-534` | Background thread resumes flushing without checking for skipped time |
| Offline event insertion | `on-modify.01-aw-watcher-taskwarrior.py:422-437` | Direct HTTP POST to `/events` bypasses queue; can race with stale queue replay |
| Heartbeat merge logic | `/heartbeat` server endpoint | May incorrectly handle out-of-order/stale heartbeats |

## Recommended Fixes

### OPTION A: Clear Queue on Resume (Safest ⭐ RECOMMENDED)
**Rationale:** Discard all queued heartbeats when system resumes, since they're likely stale and won't accurately represent the offline period anyway.

**Implementation in `on-modify.01-aw-watcher-taskwarrior.py`:**

```python
# Add this import at top
import shutil
from pathlib import Path

# In heartbeat_daemon(), after creating client but before main loop:
def clear_stale_heartbeat_queue():
    """Clear persistent queue to prevent stale heartbeats from corrupting offline events."""
    try:
        # Queue location: ~/.local/share/aw-client/queued/
        queue_dir = Path.home() / ".local/share/aw-client/queued"
        if queue_dir.exists():
            # Remove the persistent queue files for this client
            for queue_file in queue_dir.glob("aw-watcher-taskwarrior*"):
                try:
                    queue_file.unlink()
                    debug_log(f"Cleared stale queue file: {queue_file}")
                except Exception as e:
                    debug_log(f"Could not clear queue file {queue_file}: {e}")
    except Exception as e:
        debug_log(f"Error clearing queue: {e}")

# Then in heartbeat_daemon(), after creating client:
client = ActivityWatchClient("aw-watcher-taskwarrior", testing=False)
clear_stale_heartbeat_queue()  # <-- Add this line
```

**Pros:**
- Simple, reliable fix
- Prevents race condition entirely
- No risk of merging errors
- Queue will rebuild fresh from next heartbeat

**Cons:**
- Discards any unsent heartbeats (acceptable, they're already offline-covered)

### OPTION B: Use `/heartbeat` Endpoint for Offline Events (More Correct)
**Rationale:** Use the same `/heartbeat` endpoint for offline events as regular heartbeats, letting the server handle merge logic consistently.

**Implementation:**

```python
# In check_and_create_offline_events(), replace direct POST (lines 422-437):

# OLD CODE:
try:
    url = f"http://localhost:5600/api/0/buckets/{bucket_id}/events"
    payload = {
        "timestamp": str(offline_event_start),
        "data": payload_task_data,
        "duration": int(gap_duration),
    }
    debug_log(f"Posting to {url}: {payload}")
    response = post(url, json=payload, timeout=5)
    ...

# NEW CODE:
try:
    # Use client.heartbeat() for offline events too
    offline_event = Event(
        timestamp=offline_event_start,
        data=payload_task_data,
    )
    # Set a long duration to cover the offline period
    offline_event.duration = timedelta(seconds=int(gap_duration))
    
    client.heartbeat(
        bucket_id,
        offline_event,
        pulsetime=5,
        queued=True,
        commit_interval=COMMIT_INTERVAL,
    )
    debug_log(f"✓ Created offline event via heartbeat: {gap_duration:.1f}s")
    return True
```

**Pros:**
- Uses consistent insertion path
- Server merge logic handles all events uniformly
- No race condition with direct POST

**Cons:**
- Changes offline event insertion method
- Offline events now go through same queue as heartbeats
- Requires import of `timedelta`

### OPTION C: Timestamp Format Fix (Defensive)
**Rationale:** Use ISO 8601 format consistently instead of str(datetime) for better compatibility.

**Implementation in `check_and_create_offline_events()` line 424:**

```python
# OLD:
"timestamp": str(offline_event_start),

# NEW:
"timestamp": offline_event_start.isoformat(),
```

**Pros:**
- Standards-compliant
- Matches Event object's format
- Future-proof

**Cons:**
- Cosmetic; not fixing the core issue alone

## Recommended Fix Strategy

**Use OPTION A + OPTION C:**

1. **Clear stale queue on daemon resume** (fixes the race condition)
2. **Use ISO format for offline event timestamp** (defensive fix)
3. **Monitor debug logs** for "Cleared stale queue file" messages

This is the safest, most maintainable approach.

## Testing the Fix

After applying the fix:

```bash
# 1. Start a task
task start <task>

# 2. Suspend system or stop aw-server momentarily
systemctl suspend
# ... or ...
killall aw-server
# wait 30 seconds
aw-server &

# 3. Resume and check debug log
tail -f /tmp/aw-watcher-taskwarrior-debug.log

# 4. Verify offline event appears
curl http://localhost:5600/api/0/buckets/aw-watcher-taskwarrior_$(hostname)/events | jq '.[] | select(.data.tags | contains(["OFFLINE"]))'
```

Expected output: Offline event with correct duration and no corruption.

## Files to Modify

1. `/home/emirhg/Desktop/ianua/tw-hook-aw-watcher/on-modify.01-aw-watcher-taskwarrior.py`
   - Add `clear_stale_heartbeat_queue()` function (~15 lines)
   - Call it after client creation (~1 line)
   - Change line 424 to use `.isoformat()` (~1 line)

**Total changes: ~20 lines of code**

## Why This Wasn't Obvious

1. Integration tests showed direct POST works in isolation ✅
2. The 200 OK response is misleading (succeeds at HTTP level, but later corrupted by queue replay)
3. The race condition only manifests during system suspend/resume
4. The corruption happens server-side via merge logic, not in the HTTP response

This explains why the issue was hard to debug—standard unit tests pass, but real-world suspend scenario fails.
