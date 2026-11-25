import io
import json
import logging
import os
import random
import struct
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock, Thread
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import requests
from confluent_kafka import KafkaError, KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from fastavro import parse_schema, schemaless_writer
from fastavro.validation import ValidationError, validate
from flask import Flask, request

TOPIC = "movie-events"
SCHEMA = Path(__file__).with_name("movie-event.avsc").read_text()
AVRO = parse_schema(json.loads(SCHEMA))
DEVICES = AVRO["fields"][5]["type"]["symbols"]
app = Flask(__name__)
publish_lock = Lock()


def initialize():
    brokers = {"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"]}
    admin = AdminClient(brokers)
    topic = NewTopic(TOPIC, 3, 3, config={"min.insync.replicas": "2"})
    try:
        admin.create_topics([topic], request_timeout=20)[TOPIC].result()
    except KafkaException as error:
        if error.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise

    subject = f"{os.environ['SCHEMA_REGISTRY_URL']}/subjects/{TOPIC}-value"
    schema = {"schemaType": "AVRO", "schema": SCHEMA}
    registration = requests.post(f"{subject}/versions", json=schema, timeout=10)
    registration.raise_for_status()
    version = requests.post(subject, json=schema, timeout=10)
    version.raise_for_status()
    metadata = version.json()
    logging.info("Registered %s-value version=%s schema_id=%s", TOPIC, metadata["version"], metadata["id"])
    producer = Producer({
        **brokers,
        "acks": "all",
        "enable.idempotence": True,
        "retry.backoff.ms": 100,
        "retry.backoff.max.ms": 2000,
        "message.timeout.ms": 15000,
        "linger.ms": 1,
    })
    # The Confluent wire format prefixes Avro with a magic byte and the schema ID.
    return producer, struct.pack(">BI", 0, metadata["id"])


def encode(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    event = payload.copy()
    event["event_id"] = str(UUID(event["event_id"]))
    timestamp = datetime.fromisoformat(event["timestamp"])
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    event["timestamp"] = timestamp.astimezone(UTC)
    if not datetime(1970, 1, 1, tzinfo=UTC) <= event["timestamp"] < datetime(2149, 6, 7, tzinfo=UTC):
        raise ValueError("timestamp is outside ClickHouse Date range")

    progress = event.setdefault("progress_seconds", None)
    if event["event_type"] in ("VIEW_STARTED", "VIEW_FINISHED", "VIEW_PAUSED", "VIEW_RESUMED") and progress is None:
        raise ValueError("progress_seconds is required for view events")
    if progress is not None and (type(progress) is not int or not 0 <= progress < 2**31):
        raise ValueError("progress_seconds must be a non-negative Avro int")

    buffer = io.BytesIO()
    buffer.write(wire_header)
    validate(event, AVRO, strict=True)
    schemaless_writer(buffer, AVRO, event, strict=True)
    return event, buffer.getvalue()


def publish(event, value):
    errors = []
    with publish_lock:
        kafka.produce(TOPIC, key=event["user_id"].encode(), value=value,
                      on_delivery=lambda error, _: errors.append(error))
        kafka.flush()
    if errors[0] is not None:
        raise KafkaException(errors[0])
    logging.info("Published event_id=%s event_type=%s timestamp=%s",
                 event["event_id"], event["event_type"], event["timestamp"].isoformat())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/events")
def post_event():
    try:
        event, value = encode(request.get_json(silent=True))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ValidationError) as error:
        return {"error": str(error)}, 400
    try:
        publish(event, value)
    except (KafkaException, BufferError):
        logging.exception("Kafka publication failed for %s", event["event_id"])
        return {"error": "Kafka publication failed"}, 503
    return {"event_id": event["event_id"]}, 201


def session(user, session_id, started_at, rng):
    movie = f"movie-{rng.choices(range(1, 7), weights=[6, 4, 3, 2, 1, 1])[0]}"
    device = rng.choice(DEVICES)
    duration = rng.randint(1800, 5400)
    pause = duration // 3
    stages = [("VIEW_STARTED", 0, 0), ("VIEW_PAUSED", pause, pause)]
    if rng.random() < 0.8:
        stages += [("VIEW_RESUMED", pause + 6, pause + 1), ("VIEW_FINISHED", duration + 5, duration)]
        if rng.random() < 0.4:
            stages.append(("LIKED", duration + 10, None))
    if rng.random() < 0.5:
        stages.insert(0, ("SEARCHED", -10, None))

    for kind, seconds, progress in stages:
        publish(*encode({
            "event_id": str(uuid5(NAMESPACE_URL, f"{session_id}:{kind}")),
            "user_id": user,
            "movie_id": movie,
            "event_type": kind,
            "timestamp": (started_at + timedelta(seconds=seconds)).isoformat(timespec="milliseconds"),
            "device_type": device,
            "session_id": session_id,
            "progress_seconds": progress,
        }))


def seed_demo():
    today = datetime.now(UTC).date()
    for days_ago in range(13, 0, -1):
        day = today - timedelta(days=days_ago)
        for age in range(min(7, 13 - days_ago) + 1):
            cohort = day - timedelta(days=age)
            for index in range(6):
                user = f"demo-{cohort}-{index}"
                session_id = f"{user}-{day}"
                rng = random.Random(session_id)
                if age == 0 or rng.random() < 0.8 / (age**0.6 + 0.4):
                    start = datetime.fromisoformat(f"{day}T12:00:00+00:00")
                    session(user, session_id, start, rng)


def generate(interval):
    try:
        seed_demo()
    except (KafkaException, BufferError):
        logging.exception("Demo data generation failed")
    while True:
        try:
            session(f"live-{random.randint(1, 30)}", str(uuid4()),
                    datetime.now(UTC) - timedelta(seconds=5500), random)
        except (KafkaException, BufferError):
            logging.exception("Live session publication failed")
        time.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    kafka, wire_header = initialize()
    interval = float(os.environ["GENERATOR_INTERVAL_SECONDS"])
    if interval > 0:
        Thread(target=generate, args=(interval,), daemon=True).start()
    app.run(host="0.0.0.0", port=8000)
