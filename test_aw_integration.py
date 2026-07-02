#!/usr/bin/env python3
"""
Integration tests for ActivityWatch event posting.
Tests different timestamp formats and event structures to identify
why offline events appear to succeed (200 status) but don't show up in bucket.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from socket import gethostname
from requests import post, get

from aw_client import ActivityWatchClient
from aw_core.models import Event

# Test bucket configuration
TEST_BUCKET_ID = f"aw-watcher-taskwarrior-test_{gethostname()}"
AW_SERVER_URL = "http://localhost:5600/api/0"

# Sample task data
SAMPLE_TASK = {
    "title": "Test Task Integration",
    "project": "Testing.Integration",
    "tags": ["TEST"],
    "uuid": "test-uuid-12345",
}

def print_header(title):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")

def print_test(num, title):
    print(f"\n[TEST {num}] {title}")
    print("-" * 70)

def setup_bucket():
    """Create or connect to test bucket."""
    print("Setting up test bucket...")
    client = ActivityWatchClient("aw-watcher-taskwarrior-test", testing=False)
    try:
        client.create_bucket(TEST_BUCKET_ID, event_type="task-activity")
        print(f"✓ Bucket created/connected: {TEST_BUCKET_ID}")
        return client
    except Exception as e:
        print(f"✗ Failed to create bucket: {e}")
        sys.exit(1)

def cleanup_bucket(client):
    """Delete test bucket after tests."""
    try:
        delete_url = f"{AW_SERVER_URL}/buckets/{TEST_BUCKET_ID}"
        response = get(delete_url)
        if response.status_code == 200:
            print(f"✓ Bucket deleted after tests")
    except Exception as e:
        print(f"  (Note: Could not delete bucket: {e})")

def get_bucket_events():
    """Fetch all events from test bucket."""
    try:
        url = f"{AW_SERVER_URL}/buckets/{TEST_BUCKET_ID}/events"
        response = get(url, timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"  ✗ Failed to fetch events: HTTP {response.status_code}")
            return []
    except Exception as e:
        print(f"  ✗ Exception fetching events: {e}")
        return []

def test_1_heartbeat_via_client():
    """Test 1: Using client.heartbeat() (works in production)."""
    print_test(1, "Heartbeat via ActivityWatch client library")

    client = ActivityWatchClient("aw-watcher-taskwarrior-test", testing=False)
    now = datetime.now(timezone.utc)

    event = Event(timestamp=now, data=SAMPLE_TASK)
    print(f"Event timestamp: {event.timestamp}")
    print(f"Event timestamp ISO: {event.timestamp.isoformat()}")

    try:
        client.heartbeat(
            TEST_BUCKET_ID,
            event,
            pulsetime=5,
            queued=True,
            commit_interval=15,
        )
        print("✓ Heartbeat sent via client.heartbeat()")
    except Exception as e:
        print(f"✗ Failed: {e}")

def test_2_direct_post_str_timestamp():
    """Test 2: Direct HTTP POST with str(datetime) format (current code)."""
    print_test(2, "Direct HTTP POST with str(datetime) timestamp")

    now = datetime.now(timezone.utc)
    timestamp_str = str(now)

    payload = {
        "timestamp": timestamp_str,
        "data": SAMPLE_TASK,
        "duration": 60,
    }
    print(f"Timestamp format: {timestamp_str}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        url = f"{AW_SERVER_URL}/buckets/{TEST_BUCKET_ID}/events"
        response = post(url, json=payload, timeout=5)
        print(f"Response status: {response.status_code}")
        print(f"Response text: {response.text}")

        if response.status_code == 200:
            print("✓ HTTP 200 received (event might or might not exist)")
    except Exception as e:
        print(f"✗ Exception: {e}")

def test_3_direct_post_iso_timestamp():
    """Test 3: Direct HTTP POST with ISO 8601 timestamp."""
    print_test(3, "Direct HTTP POST with ISO 8601 timestamp")

    now = datetime.now(timezone.utc)
    timestamp_iso = now.isoformat()

    payload = {
        "timestamp": timestamp_iso,
        "data": SAMPLE_TASK,
        "duration": 60,
    }
    print(f"Timestamp format: {timestamp_iso}")
    print(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        url = f"{AW_SERVER_URL}/buckets/{TEST_BUCKET_ID}/events"
        response = post(url, json=payload, timeout=5)
        print(f"Response status: {response.status_code}")
        print(f"Response text: {response.text}")

        if response.status_code == 200:
            print("✓ HTTP 200 received")
    except Exception as e:
        print(f"✗ Exception: {e}")

def test_4_direct_post_with_duration_ms():
    """Test 4: Direct HTTP POST with duration in milliseconds."""
    print_test(4, "Direct HTTP POST with duration in milliseconds")

    now = datetime.now(timezone.utc)

    payload = {
        "timestamp": now.isoformat(),
        "data": SAMPLE_TASK,
        "duration": 60000,  # milliseconds instead of seconds
    }
    print(f"Duration: 60000 (milliseconds)")
    print(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        url = f"{AW_SERVER_URL}/buckets/{TEST_BUCKET_ID}/events"
        response = post(url, json=payload, timeout=5)
        print(f"Response status: {response.status_code}")
        print(f"Response text: {response.text}")
    except Exception as e:
        print(f"✗ Exception: {e}")

def test_5_using_event_object_with_insert():
    """Test 5: Using Event object and insert_event (client method)."""
    print_test(5, "Using Event object via client.insert_event()")

    client = ActivityWatchClient("aw-watcher-taskwarrior-test", testing=False)
    now = datetime.now(timezone.utc)

    event = Event(timestamp=now, data=SAMPLE_TASK, duration=60)
    print(f"Event object created with timestamp: {event.timestamp}")

    try:
        inserted = client.insert_event(TEST_BUCKET_ID, event)
        print(f"✓ Event inserted, returned: {inserted}")
    except Exception as e:
        print(f"✗ Failed: {e}")

def test_6_verify_events_in_bucket():
    """Test 6: Verify which events actually exist in the bucket."""
    print_test(6, "Verifying events in bucket")

    events = get_bucket_events()
    print(f"Total events in bucket: {len(events)}")

    for i, event in enumerate(events[-5:]):  # Show last 5
        print(f"\nEvent {i+1}:")
        print(f"  timestamp: {event.get('timestamp')}")
        print(f"  duration: {event.get('duration')}")
        print(f"  data: {event.get('data')}")

def main():
    print_header("ActivityWatch Integration Tests")
    print(f"Server: {AW_SERVER_URL}")
    print(f"Bucket: {TEST_BUCKET_ID}")

    client = setup_bucket()

    try:
        test_1_heartbeat_via_client()
        test_2_direct_post_str_timestamp()
        test_3_direct_post_iso_timestamp()
        test_4_direct_post_with_duration_ms()
        test_5_using_event_object_with_insert()
        test_6_verify_events_in_bucket()

        print_header("Test Summary")
        print("\nFinal bucket state:")
        final_events = get_bucket_events()
        print(f"  Total events: {len(final_events)}")

        for event in final_events:
            print(f"  - {event.get('timestamp')}: {event.get('data', {}).get('title', 'unknown')}")

    finally:
        cleanup_bucket(client)

if __name__ == "__main__":
    main()
