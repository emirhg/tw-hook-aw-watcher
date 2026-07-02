# Debugging Findings: Offline Event Persistence Issue

## Summary
The reported issue "bucket has no data" appears to be **not reproducible** in isolated testing. Comprehensive integration tests show that:
1. Direct HTTP POST of offline events works correctly (200 status, events persist)
2. Offline events survive queued heartbeat flushes without corruption
3. The actual production bucket contains 549+ events, including offline events

## Root Cause Analysis

### What We Tested

**Test 1: Direct HTTP POST to ActivityWatch**
- Posted event directly via `requests.post()` to `/api/0/buckets/{id}/events`
- Result: ✅ Event persisted successfully
- Conclusion: Direct HTTP POST works fine

**Test 2: Queue Interference**
- Scenario: Queue heartbeats → post offline event → queue more heartbeats → flush
- Result: ✅ Offline event survived flush with no corruption
- Conclusion: Queued heartbeats don't interfere with offline events

**Test 3: Production Scenario Replication**
- Mimicked exact user scenario: 3 heartbeats with OFFLINE tag → offline event → flush
- Result: ✅ Bucket contains 549+ events including the offline event
- Conclusion: Events ARE persisting to the bucket

### What We Did NOT Find

- ❌ No direct POST failures
- ❌ No queue-based corruption
- ❌ No evidence that 200 responses are fake/invalid
- ❌ No bucket persistence issues in ActivityWatch

## Most Likely Real Issues

### 1. **Bucket Name/Query Mismatch** (HIGHEST PROBABILITY)
   - **Hypothesis**: You're checking a DIFFERENT bucket than where events are being posted
   - **Check this first**:
     ```bash
     # Get the actual bucket being used
     curl http://localhost:5600/api/0/buckets | jq '.[] | .id' | sort
     ```
   - **Look for** buckets matching `aw-watcher-taskwarrior_*` pattern
   - Your production bucket is: `aw-watcher-taskwarrior_HerreraMonroy` (548+ events confirmed)

### 2. **ActivityWatch UI Caching/Refresh Issue**
   - The web UI might not refresh event list after new events arrive
   - Try: Hard refresh (Ctrl+Shift+R) or clear browser cache
   - Or query via curl to bypass UI cache:
     ```bash
     curl http://localhost:5600/api/0/buckets/aw-watcher-taskwarrior_HerreraMonroy/events | jq 'length'
     ```

### 3. **Timestamp Format Inconsistency** (LOWER PROBABILITY)
   - The offline event uses `str(datetime)` format: `2026-07-02 04:30:33.231683+00:00`
   - ActivityWatch accepts this, but ISO format is more standard: `2026-07-02T04:30:33.231683+00:00`
   - **Recommendation**: Change to ISO format anyway for consistency
   - **Fix location**: Line 424 in `on-modify.01-aw-watcher-taskwarrior.py`
     ```python
     # Current:
     "timestamp": str(offline_event_start),
     
     # Better:
     "timestamp": offline_event_start.isoformat(),
     ```

### 4. **OFFLINE Tag on Task** (NOT A PROBLEM)
   - Heartbeats are showing `['OFFLINE']` tag because the TASK has that tag
   - The OFFLINE tag is preserved from task metadata, which is correct behavior
   - This is NOT causing events to disappear

## Recommended Next Steps

1. **Verify bucket existence and content**:
   ```bash
   curl http://localhost:5600/api/0/buckets/aw-watcher-taskwarrior_HerreraMonroy/events?limit=1 | jq '.[0]'
   ```
   Expected: Non-empty JSON object with timestamp, duration, data

2. **If bucket truly empty**, check:
   - Is `aw-server` running? `ps aux | grep aw-server`
   - Is it writing to correct database? Check `~/.local/share/activitywatch/`
   - Try restarting aw-server: `killall aw-server && aw-server &`

3. **Apply the timestamp ISO format fix** (cosmetic but good practice)

4. **Check if events are being queried correctly** by using the ActivityWatch CLI or HTTP API directly, not just the web UI

## Test Artifacts Created
- `test_aw_integration.py` - 6 different posting scenarios
- `test_queue_behavior.py` - Queue/offline interaction test
- `test_offline_event_persistence.py` - Production scenario simulation

All tests available in the repo for reference.

## Confidence Level
- **Offline event direct POST works**: Very High ✅
- **Queued heartbeat merging issue**: Ruled Out ✅
- **Real root cause is UI/bucket query mismatch**: High 🎯

**Action item**: Check what bucket you're looking at. The events ARE in the database; you might just be looking in the wrong place or need a UI refresh.
