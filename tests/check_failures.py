import json
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def compose(*args):
    return subprocess.run(["docker", "compose", *args], check=True,
                          text=True, capture_output=True, timeout=40).stdout.strip()


def request(path, body=None):
    req = Request("http://localhost:8000" + path,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as response:
        return response.code, json.load(response)


def wait_for(check, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.25)
    raise AssertionError("Timed out waiting for recovery")


def master_address():
    return compose("exec", "-T", "sentinel-1", "redis-cli", "-p", "26379", "--raw",
                   "SENTINEL", "get-master-addr-by-name", "flights")


def role(service):
    return compose("exec", "-T", service, "redis-cli", "--raw", "ROLE").splitlines()[0]


if __name__ == "__main__":
    flight_path = "/flights/11111111-1111-1111-1111-111111111111"
    stopped = set()
    booking_id = None
    try:
        assert request(flight_path)[0] == 200
        compose("stop", "-t", "1", "flight")
        stopped.add("flight")
        for _ in range(5):
            status, body = request(flight_path)
            assert status == 503, (status, body)
        start = time.monotonic()
        status, body = request(flight_path)
        assert status == 503 and "circuit breaker" in body["detail"], body
        assert time.monotonic() - start < 0.5, "OPEN should reject immediately"
        print("PASS: Flight outage -> retry -> OPEN -> immediate HTTP 503", flush=True)
        compose("start", "--wait", "flight")
        stopped.remove("flight")
        wait_for(lambda: request(flight_path)[0] == 200)
        logs = compose("logs", "--tail", "200", "booking")
        for transition in ("CLOSED -> OPEN", "OPEN -> HALF_OPEN", "HALF_OPEN -> CLOSED"):
            assert transition in logs, transition
        print("PASS: Flight recovery -> HALF_OPEN -> CLOSED -> HTTP 200", flush=True)

        old_address = master_address()
        old_master = next(node for node in ("redis-master", "redis-replica") if role(node) == "master")
        before = request(flight_path)[1]["available_seats"]
        compose("stop", "-t", "1", old_master)
        stopped.add(old_master)
        wait_for(lambda: master_address() != old_address)
        new_master = "redis-replica" if old_master == "redis-master" else "redis-master"
        wait_for(lambda: role(new_master) == "master")
        assert request(flight_path)[0] == 200
        status, body = request("/bookings", {"user_id": "failure-check",
            "flight_id": "11111111-1111-1111-1111-111111111111",
            "passenger_name": "Тест", "passenger_email": "test@example.com", "seat_count": 1})
        assert status == 201, (status, body)
        booking_id = body["id"]
        assert request(flight_path)[1]["available_seats"] == before - 1
        assert request("/bookings/" + booking_id + "/cancel", {})[0] == 200
        booking_id = None
        assert request(flight_path)[1]["available_seats"] == before
        assert request(flight_path)[0] == 200
        assert "cache hit" in compose("logs", "--tail", "40", "flight")
        print(f"PASS: Sentinel failover {old_master} -> {new_master}; cache and bookings work", flush=True)
    finally:
        for service in stopped:
            compose("start", "--wait", service)
        if booking_id:
            request("/bookings/" + booking_id + "/cancel", {})
