import functools
import hmac
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

import grpc
import psycopg
from redis import RedisError
from redis.sentinel import Sentinel

import flight_pb2 as pb
import flight_pb2_grpc as rpc
from common import connect, migrate

log = logging.getLogger(__name__)
FLIGHT_SQL = """SELECT f.*, a.name AS airline FROM flights f
    JOIN flight_numbers n USING (flight_number)
    JOIN airlines a ON a.id = n.airline_id"""


def uuid(value):
    return str(UUID(value))


def airport(value):
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError("Аэропорт должен быть IATA-кодом из трёх заглавных букв")
    return value


def flight_message(row):
    result = pb.Flight(**{k: row[k] for k in (
        "flight_number", "airline", "origin", "destination",
        "total_seats", "available_seats", "price")},
        id=str(row["id"]), status=pb.FlightStatus.Value(row["status"]))
    result.departure_time.FromDatetime(row["departure_time"])
    result.arrival_time.FromDatetime(row["arrival_time"])
    return result


def reservation_message(row):
    return pb.SeatReservation(booking_id=str(row["booking_id"]),
        flight_id=str(row["flight_id"]), seat_count=row["seat_count"],
        status=pb.ReservationStatus.Value(row["status"]))


def errors(method):
    @functools.wraps(method)
    def wrapped(self, request, context):
        try:
            return method(self, request, context)
        except ValueError as error:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
        except psycopg.Error:
            log.exception("Flight database error")
            context.abort(grpc.StatusCode.UNAVAILABLE, "База рейсов недоступна")
    return wrapped


class Authentication(grpc.ServerInterceptor):
    def __init__(self):
        self.key = os.environ["GRPC_API_KEY"]

    def intercept_service(self, continuation, details):
        handler = continuation(details)
        key = dict(details.invocation_metadata).get("x-api-key", "")
        if handler and not hmac.compare_digest(key, self.key):
            def deny(request, context):
                context.abort(grpc.StatusCode.UNAUTHENTICATED, "Неверный x-api-key")
            return grpc.unary_unary_rpc_method_handler(deny,
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer)
        return handler


class Cache:
    def __init__(self):
        nodes = [(host, int(port)) for host, port in
                 (node.split(":") for node in os.environ["REDIS_SENTINELS"].split(","))]
        sentinel = Sentinel(nodes, socket_timeout=0.3,
                            sentinel_kwargs={"socket_timeout": 0.3, "socket_connect_timeout": 0.3})
        self.redis = sentinel.master_for(os.environ.get("REDIS_MASTER", "flights"),
                                         socket_timeout=0.3, socket_connect_timeout=0.3)
        self.ttl = int(os.environ.get("CACHE_TTL", "300"))
        if not 300 <= self.ttl <= 600:
            raise ValueError("CACHE_TTL должен быть от 300 до 600 секунд")

    def aside(self, key, message_type, load):
        try:
            value = self.redis.get(key)
            if value is not None:
                log.info("cache hit %s", key)
                return message_type.FromString(value)
        except RedisError:
            log.warning("Redis unavailable; reading PostgreSQL")
        log.info("cache miss %s", key)
        result = load()
        try:
            self.redis.set(key, result.SerializeToString(), ex=self.ttl)
        except RedisError:
            log.warning("Redis unavailable; cache write skipped")
        return result


class FlightService(rpc.FlightServiceServicer):
    def __init__(self):
        self.cache = Cache()

    @errors
    def SearchFlights(self, request, context):
        origin, destination = airport(request.origin), airport(request.destination)
        day = request.date.ToDatetime(timezone.utc).date() if request.HasField("date") else None
        with connect() as db:
            revision = db.execute("SELECT revision FROM cache_revision").fetchone()["revision"]
            key = f"search:{origin}:{destination}:{day or 'all'}:{revision}"

            def load():
                sql = FLIGHT_SQL + " WHERE origin = %s AND destination = %s AND status = 'SCHEDULED'"
                params = [origin, destination]
                if day:
                    start = datetime.combine(day, time.min, timezone.utc)
                    sql += " AND departure_time >= %s AND departure_time < %s"
                    params.extend([start, start + timedelta(days=1)])
                rows = db.execute(sql + " ORDER BY departure_time, f.id", params).fetchall()
                return pb.SearchFlightsResponse(flights=[flight_message(row) for row in rows])

            return self.cache.aside(key, pb.SearchFlightsResponse, load)

    @errors
    def GetFlight(self, request, context):
        flight_id = uuid(request.flight_id)
        with connect() as db:
            revision = db.execute("SELECT revision FROM cache_revision").fetchone()["revision"]

            def load():
                row = db.execute(FLIGHT_SQL + " WHERE f.id = %s", (flight_id,)).fetchone()
                if not row:
                    context.abort(grpc.StatusCode.NOT_FOUND, "Рейс не найден")
                return flight_message(row)

            return self.cache.aside(f"flight:{flight_id}:{revision}", pb.Flight, load)

    @errors
    def ReserveSeats(self, request, context):
        flight_id, booking_id = uuid(request.flight_id), uuid(request.booking_id)
        if request.seat_count <= 0:
            raise ValueError("seat_count должен быть положительным")
        with connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (booking_id,))
            old = db.execute("SELECT * FROM seat_reservations WHERE booking_id = %s", (booking_id,)).fetchone()
            if old:
                if str(old["flight_id"]) != flight_id or old["seat_count"] != request.seat_count:
                    context.abort(grpc.StatusCode.ALREADY_EXISTS, "booking_id уже использован с другими параметрами")
                if old["status"] != "ACTIVE":
                    context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Резервация уже отменена")
                return reservation_message(old)
            flight = db.execute("SELECT * FROM flights WHERE id = %s FOR UPDATE", (flight_id,)).fetchone()
            if not flight:
                context.abort(grpc.StatusCode.NOT_FOUND, "Рейс не найден")
            if flight["status"] != "SCHEDULED":
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Рейс недоступен для бронирования")
            if flight["available_seats"] < request.seat_count:
                context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Недостаточно свободных мест")
            db.execute("UPDATE flights SET available_seats = available_seats - %s WHERE id = %s",
                       (request.seat_count, flight_id))
            row = db.execute("""INSERT INTO seat_reservations VALUES (%s, %s, %s, 'ACTIVE')
                RETURNING *""", (booking_id, flight_id, request.seat_count)).fetchone()
            db.execute("UPDATE cache_revision SET revision = revision + 1")
        return reservation_message(row)

    @errors
    def ReleaseReservation(self, request, context):
        booking_id = uuid(request.booking_id)
        with connect() as db:
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (booking_id,))
            row = db.execute("SELECT * FROM seat_reservations WHERE booking_id = %s", (booking_id,)).fetchone()
            if not row:
                context.abort(grpc.StatusCode.NOT_FOUND, "Резервация не найдена")
            if row["status"] == "ACTIVE":
                db.execute("SELECT id FROM flights WHERE id = %s FOR UPDATE", (row["flight_id"],))
                db.execute("UPDATE flights SET available_seats = available_seats + %s WHERE id = %s",
                           (row["seat_count"], row["flight_id"]))
                row = db.execute("""UPDATE seat_reservations SET status = 'RELEASED'
                    WHERE booking_id = %s RETURNING *""", (booking_id,)).fetchone()
                db.execute("UPDATE cache_revision SET revision = revision + 1")
        return reservation_message(row)

    @errors
    def UpdateFlight(self, request, context):
        flight_id = uuid(request.flight_id)
        if request.price <= 0 or request.status not in (pb.SCHEDULED, pb.DEPARTED, pb.CANCELLED, pb.COMPLETED):
            raise ValueError("Некорректная цена или статус рейса")
        with connect() as db:
            row = db.execute("""UPDATE flights SET price = %s, status = %s
                WHERE id = %s RETURNING id""",
                (request.price, pb.FlightStatus.Name(request.status), flight_id)).fetchone()
            if not row:
                context.abort(grpc.StatusCode.NOT_FOUND, "Рейс не найден")
            db.execute("UPDATE cache_revision SET revision = revision + 1")
            result = flight_message(db.execute(FLIGHT_SQL + " WHERE f.id = %s", (flight_id,)).fetchone())
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    migrate("flight")
    server = grpc.server(ThreadPoolExecutor(max_workers=10), interceptors=[Authentication()])
    rpc.add_FlightServiceServicer_to_server(FlightService(), server)
    server.add_insecure_port("[::]:50051")
    server.start()
    log.info("Flight Service listening on :50051")
    server.wait_for_termination()
