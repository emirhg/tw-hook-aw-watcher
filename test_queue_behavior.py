#!/usr/bin/env python3
"""
Test to check if queued heartbeats are interfering with offline events.

Scenario:
1. Send heartbeat with timestamp T1 via client.heartbeat(..., queued=True)
2. Wait a bit (simulating what would happen during system suspend)
3. Post offline event directly via HTTP with timestamp T1 (covering the gap)
4. Send another heartbeat via client.heartbeat(..., queued=True)
5. Flush the client context
6. Check if the offline event still exists or got corrupted/merged
"""

import time
from datetime import datetime, timedelta, timezone
from socket import gethostname
from requests import post, get

from aw_client import ActivityWatchClient
from aw_core.models import Event

AW_SERVER_URL = "http://localhost:5600/api/0"
TEST_BUCKET_ID = f"aw-watcher-taskwarrior-queue-test_{gethostname()}"

def get_events():
    try:
        response = get(f"{AW_SERVER_URL}/buckets/{TEST_BUCKET_ID}/events", timeout=5)
        return response.json() if response.status_code == 200 else []
    except:
        return []

def print_events(label):
    events = get_events()
    print(f"\n{label}: {len(events)} events")
    for i, e in enumerate(events):
        ts = e.get('timestamp', '?')
        dur = e.get('duration', '?')
        title = e.get('data', {}).get('title', '?')
        print(f"  [{i}] {ts} | dur={dur} | {title}")

def main():
    print("=" * 70)
    print("Testing queue vs offline event interference")
    print("=" * 70)

    # Setup bucket
    print("\nSetting up bucket...")
    client = ActivityWatchClient("aw-watcher-test-queue", testing=False)
    client.create_bucket(TEST_BUCKET_ID, event_type="task-activity")
    print(f"✓ Bucket created: {TEST_BUCKET_ID}")

    # T0: baseline
    baseline_time = datetime.now(timezone.utc)

    print("\n[STEP 1] Send heartbeat at T0 via client.heartbeat (queued)")
    event1 = Event(
        timestamp=baseline_time,
        data={"title": "Heartbeat 1", "project": "Test"}
    )
    client.heartbeat(TEST_BUCKET_ID, event1, pulsetime=5, queued=True)
    print_events("After step 1 (queued, not yet flushed)")

    print("\n[STEP 2] Wait 2 seconds (simulating time passing during suspend)")
    time.sleep(2)

    print("\n[STEP 3] Post offline event directly for the gap (T0 to T0+2s)")
    offline_start = baseline_time
    offline_duration = 2.0
    offline_payload = {
        "timestamp": offline_start.isoformat(),
        "data": {"title": "OFFLINE EVENT", "project": "Test", "tags": ["OFFLINE"]},
        "duration": offline_duration,
    }
    response = post(
        f"{AW_SERVER_URL}/buckets/{TEST_BUCKET_ID}/events",
        json=offline_payload,
        timeout=5
    )
    print(f"Offline event POST: status={response.status_code}")
    print_events("After step 3 (offline event posted)")

    print("\n[STEP 4] Send another heartbeat at T0+2s via client.heartbeat (queued)")
    event2 = Event(
        timestamp=baseline_time + timedelta(seconds=2),
        data={"title": "Heartbeat 2", "project": "Test"}
    )
    client.heartbeat(TEST_BUCKET_ID, event2, pulsetime=5, queued=True)
    print_events("After step 4 (heartbeat 2 queued)")

    print("\n[STEP 5] Closing client context to flush all queued heartbeats")
    try:
        # Properly exit the client context to flush
        if hasattr(client, 'close'):
            client.close()
        time.sleep(1)  # Give server time to process
    except Exception as e:
        print(f"  Note: {e}")

    print_events("After step 5 (after flush)")

    print("\n[ANALYSIS]")
    events = get_events()
    print(f"Total events in bucket: {len(events)}")

    # Check if offline event is still there and intact
    offline_events = [e for e in events if "OFFLINE" in e.get('data', {}).get('tags', [])]
    if offline_events:
        print(f"✓ Offline event found: {len(offline_events)} instance(s)")
        for oe in offline_events:
            print(f"  - ts={oe.get('timestamp')}, dur={oe.get('duration')}")
    else:
        print(f"✗ Offline event MISSING!")
        print("  This suggests queued heartbeats may have overwritten/merged it")

    # Check heartbeat events
    hb_events = [e for e in events if "Heartbeat" in e.get('data', {}).get('title', '')]
    if hb_events:
        print(f"✓ Heartbeat events found: {len(hb_events)} instance(s)")

    print("\n✓ Test complete")

if __name__ == "__main__":
    main()
