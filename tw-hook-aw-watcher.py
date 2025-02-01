#!/usr/bin/env python3
# TaskWarrior Hook for ActivityWatch
# Author: Emir Herrera González 
# Inserts a TaskWarrior activity when a Task is stopped

# License: GNU GPLv3

from datetime import datetime, timezone
from requests import post
from socket import gethostname
# from time import sleep


import json
import sys

# from aw_core.models import Event
# from aw_client import ActivityWatchClient

old = json.loads(sys.stdin.readline())
new = json.loads(sys.stdin.readline())

if "start" in old and ("start" not in new or "stop" in new):
    start = datetime.strptime(old["start"], "%Y%m%dT%H%M%S%z")
    # We'll run with testing=True so we don't mess up any production instance.
    # Make sure you've started aw-server with the `--testing` flag as well.
    # client = ActivityWatchClient("aw-watcher-warrior", testing=False)

    bucket_id = "{}_{}".format("aw-watcher-warrior", gethostname())
    # client.create_bucket(bucket_id, event_type="tw.task.active")
    # url = f"http://localhost:5600/api/0/buckets/{bucket_id}"
    # res = post(url, json="{'event_type':'tw.task.active'}")
    # print(f"{bucket_id}", res.text)

    if 'project' not in new:
        active_task_data = {"title":new["description"], "status":new["status"]}
    else:
        active_task_data = {"title":new["description"], "project": new["project"], "status":new["status"]}
    now = datetime.now(timezone.utc)

    duration = now-start
    # print ("Duration: ", duration)
    # active_task_event = Event(timestamp=start, data=active_task_data, duration=int(duration.seconds))
    # inserted_event = client.insert_event(bucket_id, active_task_event)

    url = f"http://localhost:5600/api/0/buckets/{bucket_id}/events"

    res = post(url=url,
               json={'timestamp':str(start), 'data':active_task_data, 'duration':int(duration.seconds)}, timeout=2000)
    # print(res.text)


if (new):
    print(json.dumps(new))
else:
    print(json.dumps(old))
