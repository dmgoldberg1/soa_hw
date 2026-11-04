import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import grpc
import psycopg
from redis.sentinel import Sentinel

import flight_pb2 as pb
import flight_pb2_grpc as rpc
from common import connect

FLIGHT_DB = "postgresql://app:app@flight-db:5432/app"
BOOKING_DB = "postgresql://app:app@booking-db:5432/app"
API = "http://booking:8000"


def http(path, body=None):
    request = Request(API + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            return response.status, json.load(response)
    except HTTPError as response:
        return response.code, json.load(response)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.channel = grpc.insecure_channel("flight:50051")
        grpc.channel_ready_future(cls.channel).result(timeout=20)
        cls.stub = rpc.FlightServiceStub(cls.channel)
        cls.auth = (("x-api-key", os.environ["GRPC_API_KEY"]),)
        for _ in range(50):
            try:
                if http("/bookings?user_id=readiness")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.2)
        raise RuntimeError("Booking API did not start")

    @classmethod
    def tearDownClass(cls):
        cls.channel.close()

    def setUp(self):
        self.flight_id, self.user = str(uuid4()), "test-" + str(uuid4())
        with connect(FLIGHT_DB) as db:
            db.execute("""INSERT INTO flights VALUES
                (%s, 'SU100', 'VKO', 'LED', '2099-01-01 10:00Z', '2099-01-01 12:00Z',
                 2, 2, 12345, 'SCHEDULED')""", (self.flight_id,))
            db.execute("UPDATE cache_revision SET revision = revision + 1")

    def tearDown(self):
        with connect(BOOKING_DB) as db:
            db.execute("DELETE FROM bookings WHERE user_id = %s", (self.user,))
        with connect(FLIGHT_DB) as db:
            db.execute("DELETE FROM seat_reservations WHERE flight_id = %s", (self.flight_id,))
            db.execute("DELETE FROM flights WHERE id = %s", (self.flight_id,))
            db.execute("UPDATE cache_revision SET revision = revision + 1")

    def grpc(self, method, request):
        return getattr(self.stub, method)(request, metadata=self.auth, timeout=5)

    def create(self, seats=1):
        return http("/bookings", {"user_id": self.user, "flight_id": self.flight_id,
            "passenger_name": "Иван Иванов", "passenger_email": "ivan@example.com", "seat_count": seats})

    def flight(self):
        return self.grpc("GetFlight", pb.GetFlightRequest(flight_id=self.flight_id))

    def reserve(self, booking_id, seats=1):
        return self.grpc("ReserveSeats", pb.ReserveSeatsRequest(
            flight_id=self.flight_id, booking_id=booking_id, seat_count=seats))

    def release(self, booking_id):
        return self.grpc("ReleaseReservation", pb.ReleaseReservationRequest(booking_id=booking_id))

    def test_rest_flow_price_snapshot_search_and_cancellation(self):
        status, flights = http("/flights?origin=VKO&destination=LED&date=2099-01-01")
        self.assertEqual(status, 200)
        self.assertIn(self.flight_id, [f["id"] for f in flights])
        self.assertEqual(http("/flights?origin=VKO&destination=LED&date=2099-01-02"), (200, []))
        self.assertEqual(http("/flights/" + self.flight_id)[0], 200)
        status, booking = self.create(2)
        self.assertEqual(status, 201, booking)
        self.assertEqual(booking["total_price"], 24690)
        self.assertEqual(self.flight().available_seats, 0)
        self.grpc("UpdateFlight", pb.UpdateFlightRequest(flight_id=self.flight_id,
                  price=99999, status=pb.SCHEDULED))
        self.assertEqual(http("/bookings/" + booking["id"])[1]["total_price"], 24690)
        self.assertEqual(len(http("/bookings?user_id=" + self.user)[1]), 1)
        self.assertEqual(http("/bookings/" + booking["id"] + "/cancel", {})[1]["status"], "CANCELLED")
        self.assertEqual(self.flight().available_seats, 2)
        self.assertEqual(http("/bookings/" + booking["id"] + "/cancel", {})[0], 409)

    def test_no_booking_when_not_enough_seats_and_input_errors(self):
        self.assertEqual(self.create(3)[0], 409)
        self.assertEqual(self.create(0)[0], 422)
        self.assertEqual(http("/bookings?user_id=" + self.user)[1], [])
        self.assertEqual(self.flight().available_seats, 2)
        self.assertEqual(http("/flights/" + str(uuid4()))[0], 404)
        self.assertEqual(http("/bookings/" + str(uuid4()))[0], 404)
        self.assertEqual(http("/flights?origin=VKO")[0], 422)
        self.assertEqual(http("/flights?origin=VKO&destination=LED&date=bad")[0], 422)

    def test_last_seat_race(self):
        status, first = self.create()
        self.assertEqual(status, 201)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.create(), range(8)))
        self.assertEqual(sum(status == 201 for status, _ in results), 1, results)
        self.assertEqual(sum(status == 409 for status, _ in results), 7, results)
        self.assertEqual(self.flight().available_seats, 0)
        self.assertEqual(len(http("/bookings?user_id=" + self.user)[1]), 2)

    def test_concurrent_idempotent_reserve_and_release(self):
        booking_id = str(uuid4())
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.reserve(booking_id), range(8)))
        self.assertTrue(all(result.status == pb.ACTIVE for result in results))
        self.assertEqual(self.flight().available_seats, 1)
        with self.assertRaises(grpc.RpcError) as error:
            self.reserve(booking_id, 2)
        self.assertEqual(error.exception.code(), grpc.StatusCode.ALREADY_EXISTS)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.release(booking_id), range(8)))
        self.assertEqual(self.flight().available_seats, 2)
        with self.assertRaises(grpc.RpcError) as error:
            self.reserve(booking_id)
        self.assertEqual(error.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)

    def test_authentication_on_every_method(self):
        requests = {
            "SearchFlights": pb.SearchFlightsRequest(origin="VKO", destination="LED"),
            "GetFlight": pb.GetFlightRequest(flight_id=self.flight_id),
            "ReserveSeats": pb.ReserveSeatsRequest(flight_id=self.flight_id, booking_id=str(uuid4()), seat_count=1),
            "ReleaseReservation": pb.ReleaseReservationRequest(booking_id=str(uuid4())),
            "UpdateFlight": pb.UpdateFlightRequest(flight_id=self.flight_id, price=1, status=pb.SCHEDULED),
        }
        for method, request in requests.items():
            for metadata in ((), (("x-api-key", "wrong"),)):
                with self.subTest(method=method, metadata=metadata):
                    with self.assertRaises(grpc.RpcError) as error:
                        getattr(self.stub, method)(request, metadata=metadata, timeout=5)
                    self.assertEqual(error.exception.code(), grpc.StatusCode.UNAUTHENTICATED)

    def test_cache_ttl_and_invalidation_for_all_mutations(self):
        redis = Sentinel([(f"sentinel-{i}", 26379) for i in range(1, 4)],
                         socket_timeout=1).master_for("flights", socket_timeout=1)

        def cached():
            flight = self.flight()
            search = self.grpc("SearchFlights", pb.SearchFlightsRequest(origin="VKO", destination="LED"))
            return flight, next(f for f in search.flights if f.id == self.flight_id)

        cached()
        with connect(FLIGHT_DB) as db:
            revision = db.execute("SELECT revision FROM cache_revision").fetchone()["revision"]
        for key in (f"flight:{self.flight_id}:{revision}", f"search:VKO:LED:all:{revision}"):
            self.assertGreater(redis.ttl(key), 0)
            self.assertLessEqual(redis.ttl(key), 300)
        booking_id = str(uuid4())
        self.reserve(booking_id)
        self.assertEqual([f.available_seats for f in cached()], [1, 1])
        self.release(booking_id)
        self.assertEqual([f.available_seats for f in cached()], [2, 2])
        self.grpc("UpdateFlight", pb.UpdateFlightRequest(flight_id=self.flight_id, price=54321, status=pb.SCHEDULED))
        self.assertEqual([f.price for f in cached()], [54321, 54321])
        self.grpc("UpdateFlight", pb.UpdateFlightRequest(flight_id=self.flight_id, price=54321, status=pb.DEPARTED))
        self.assertEqual(self.grpc("SearchFlights", pb.SearchFlightsRequest(origin="VKO", destination="LED")).flights, [])

    def test_flight_transaction_rolls_back_if_reservation_insert_fails(self):
        with connect(FLIGHT_DB) as db:
            db.execute("""CREATE FUNCTION test_reject_reservation() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'injected reservation failure'; END $$""")
            db.execute("""CREATE TRIGGER test_reject BEFORE INSERT ON seat_reservations
                FOR EACH ROW EXECUTE FUNCTION test_reject_reservation()""")
        try:
            with self.assertRaises(grpc.RpcError):
                self.reserve(str(uuid4()))
            self.assertEqual(self.flight().available_seats, 2)
        finally:
            with connect(FLIGHT_DB) as db:
                db.execute("DROP TRIGGER test_reject ON seat_reservations")
                db.execute("DROP FUNCTION test_reject_reservation()")

    def test_booking_insert_failure_compensates_reservation(self):
        with connect(BOOKING_DB) as db:
            db.execute("""CREATE FUNCTION test_reject_booking() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'injected booking failure'; END $$""")
            db.execute("CREATE TRIGGER test_reject BEFORE INSERT ON bookings FOR EACH ROW EXECUTE FUNCTION test_reject_booking()")
        try:
            self.assertEqual(self.create()[0], 503)
            self.assertEqual(self.flight().available_seats, 2)
            self.assertEqual(http("/bookings?user_id=" + self.user)[1], [])
        finally:
            with connect(BOOKING_DB) as db:
                db.execute("DROP TRIGGER test_reject ON bookings")
                db.execute("DROP FUNCTION test_reject_booking()")

    def test_database_constraints(self):
        for assignment in ("available_seats = -1", "available_seats = 3", "total_seats = 0", "price = 0",
                           "arrival_time = departure_time", "origin = 'bad'", "destination = origin"):
            with self.subTest(assignment=assignment), self.assertRaises(psycopg.errors.CheckViolation):
                with connect(FLIGHT_DB) as db:
                    db.execute("UPDATE flights SET " + assignment + " WHERE id = %s", (self.flight_id,))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with connect(FLIGHT_DB) as db:
                db.execute("""INSERT INTO flights SELECT %s, flight_number, origin, destination,
                    departure_time + interval '1 hour', arrival_time + interval '1 hour',
                    total_seats, available_seats, price, status FROM flights WHERE id = %s""",
                    (str(uuid4()), self.flight_id))


if __name__ == "__main__":
    unittest.main()
