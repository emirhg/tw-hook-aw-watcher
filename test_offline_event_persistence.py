#!/usr/bin/env python3
"""
Replicate the exact scenario from user logs: send heartbeats with OFFLINE tag,
then post an offline event, and verify it persists.

This mimics the real daemon behavior more closely.
"""

import time
from datetime import datetime, timedelta, timezone
from socket import gethostname
from requests import post, get

from aw_client import ActivityWatchClient
from aw_core.models import Event

AW_SERVER_URL = "http://localhost:5600/api/0"
BUCKET_ID = f"aw-watcher-taskwarrior_{gethostname()}"

def get_all_events():
    """Fetch all events from the bucket."""
    try:
        response = get(f"{AW_SERVER_URL}/buckets/{BUCKET_ID}/events?limit=-1", timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"✗ Failed to fetch events: HTTP {response.status_code}")
            return []
    except Exception as e:
        print(f"✗ Exception: {e}")
        return []

def main():
    print("=" * 70)
    print("Test: Offline event persistence (mimics real daemon)")
    print("=" * 70)
    print(f"Bucket: {BUCKET_ID}")
    print(f"Server: {AW_SERVER_URL}")

    # Setup
    client = ActivityWatchClient("aw-watcher-taskwarrior-persist-test", testing=False)
    client.create_bucket(BUCKET_ID, event_type="task-activity")
    print(f"\n✓ Bucket created/connected")

    # Simulate task with OFFLINE tag (like in the user's logs)
    task_data = {
        "title": "Lavar y compactar PET",
        "project": "Ecosistema.Tratamiento de residuos.Reciclaje",
        "tags": ["OFFLINE"],
        "uuid": "test-uuid-12345"
    }

    # Send 3 heartbeats like in the logs
    print("\n[STEP 1] Sending 3 heartbeats with OFFLINE tag (via client.heartbeat, queued)")
    base_time = datetime.now(timezone.utc) - timedelta(hours=6.8)  # 6.8 hours ago

    for i in range(3):
        hb_time = base_time + timedelta(seconds=i*3)
        event = Event(timestamp=hb_time, data=task_data)
        client.heartbeat(BUCKET_ID, event, pulsetime=5, queued=True)
        print(f"  Heartbeat {i+1} queued at {hb_time}")

    print(f"  Note: Events are queued, not yet flushed")
    time.sleep(0.5)

    # Simulate offline gap detection and event posting (like in the logs)
    print("\n[STEP 2] Detect offline gap, post offline event directly")

    gap_start = base_time + timedelta(seconds=3)  # 3s after last heartbeat
    gap_duration = int((datetime.now(timezone.utc) - gap_start).total_seconds())

    offline_payload = {
        "timestamp": gap_start.isoformat(),
        "data": {
            "title": "Lavar y compactar PET",
            "project": "Ecosistema.Tratamiento de residuos.Reciclaje",
            "tags": ["OFFLINE"],
            "uuid": "test-uuid-12345"
        },
        "duration": gap_duration,
    }

    print(f"  Offline gap: {gap_start.isoformat()} for {gap_duration}s")
    print(f"  Posting: {offline_payload}")

    response = post(
        f"{AW_SERVER_URL}/buckets/{BUCKET_ID}/events",
        json=offline_payload,
        timeout=5
    )
    print(f"  Response: {response.status_code} - {response.text}")

    if response.status_code == 200:
        print("  ✓ Offline event POST returned 200")

    # Query bucket immediately
    print("\n[STEP 3] Query bucket immediately after posting")
    events_before_flush = get_all_events()
    print(f"  Events in bucket: {len(events_before_flush)}")
    for e in events_before_flush:
        print(f"    - {e.get('timestamp')}: dur={e.get('duration')} | {e.get('data', {}).get('title')}")

    # Flush queued heartbeats
    print("\n[STEP 4] Flush queued heartbeats")
    try:
        if hasattr(client, 'close'):
            client.close()
    except:
        pass
    time.sleep(1)

    # Query again after flush
    print("\n[STEP 5] Query bucket after flush")
    events_after_flush = get_all_events()
    print(f"  Events in bucket: {len(events_after_flush)}")
    for e in events_after_flush:
        data = e.get('data', {})
        print(f"    - {e.get('timestamp')}: dur={e.get('duration')} | {data.get('title')} | tags={data.get('tags')}")

    # Analyze
    print("\n[ANALYSIS]")
    offline_count = len([e for e in events_after_flush if "OFFLINE" in e.get('data', {}).get('tags', [])])
    hb_count = len([e for e in events_after_flush if "Heartbeat" not in e.get('data', {}).get('title', '')])

    print(f"  Offline events: {offline_count}")
    print(f"  Regular events: {len(events_after_flush) - offline_count}")

    if offline_count == 0 and len(events_after_flush) == 0:
        print("\n✗ PROBLEM REPRODUCED: Bucket is empty despite 200 response!")
    elif offline_count > 0:
        print("\n✓ Offline event found in bucket")
    else:
        print("\n⚠ Events exist but no OFFLINE tag found")

    print("\n✓ Test complete")

if __name__ == "__main__":
    main()
