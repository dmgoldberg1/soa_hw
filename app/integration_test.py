import json
import time
from datetime import datetime
from uuid import uuid4

import requests

from clickhouse import query


def main():
    event_id = str(uuid4())
    event = {
        "event_id": event_id,
        "user_id": f"test-{event_id}",
        "movie_id": "test-movie",
        "event_type": "VIEW_FINISHED",
        "timestamp": "2000-01-01T12:00:00.123+00:00",
        "device_type": "TV",
        "session_id": f"test-{event_id}",
        "progress_seconds": 3600,
    }
    url = "http://producer:8000/events"
    invalid = requests.post(url, json={**event, "device_type": "INVALID"}, timeout=20)
    assert invalid.status_code == 400, invalid.text
    result = requests.post(url, json=event, timeout=20)
    assert result.status_code == 201, result.text
    assert result.json() == {"event_id": event_id}, result.text

    sql = """
        SELECT event_id, user_id, movie_id, event_type,
            formatDateTime(timestamp, '%FT%T.%f', 'UTC') || '+00:00' AS timestamp,
            device_type, session_id, progress_seconds
        FROM cinema.events FINAL WHERE event_id = {event_id:UUID}
        FORMAT JSONEachRow
    """
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        raw = query(sql, event_id=event_id)
        if raw.strip():
            actual, = [json.loads(line) for line in raw.splitlines()]
            actual["timestamp"] = datetime.fromisoformat(actual["timestamp"]).isoformat(timespec="milliseconds")
            assert actual == event, (actual, event)
            print(f"OK: Producer → Kafka → ClickHouse, event_id={event_id}")
            return
        time.sleep(1)
    raise AssertionError(f"Event {event_id} did not reach ClickHouse")


if __name__ == "__main__":
    main()
