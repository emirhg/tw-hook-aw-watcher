#!/usr/bin/env python3
# TaskWarrior Hook for ActivityWatch
# Author: Emir Herrera González 
# Inserts a TaskWarrior activity when a Task is stopped

# License: GNU GPLv3

from datetime import datetime, timezone
from time import sleep

import json
import sys

from aw_core.models import Event
from aw_client import ActivityWatchClient

old = json.loads(sys.stdin.readline())
new = json.loads(sys.stdin.readline())

if "start" in old and ("start" not in new or "stop" in new):
    start = datetime.strptime(old["start"], "%Y%m%dT%H%M%S%z")
    # We'll run with testing=True so we don't mess up any production instance.
    # Make sure you've started aw-server with the `--testing` flag as well.
    client = ActivityWatchClient("aw-watcher-warrior", testing=False)

    bucket_id = "{}_{}".format("aw-watcher-warrior", client.client_hostname)
    client.create_bucket(bucket_id, event_type="tw.task.active")

    active_task_data = {"title":new["description"], "project": new["project"], "status":new["status"]}
    now = datetime.now(timezone.utc)

    duration = now-start
    print ("Duration: ", duration)
    active_task_event = Event(timestamp=start, data=active_task_data, duration=int(duration.seconds))
    inserted_event = client.insert_event(bucket_id, active_task_event)


if (new):
    print(json.dumps(new))
else:
    print(json.dumps(old))
