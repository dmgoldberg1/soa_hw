import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from uuid import UUID, uuid4

import grpc
import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import flight_pb2 as pb
import flight_pb2_grpc as rpc
from booking.resilience import Resilience
from common import connect, migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
channel = grpc.insecure_channel(os.environ.get("FLIGHT_GRPC_TARGET", "flight:50051"), options=[
    ("grpc.enable_retries", 0),
    ("grpc.initial_reconnect_backoff_ms", 100),
    ("grpc.min_reconnect_backoff_ms", 100),
    ("grpc.max_reconnect_backoff_ms", 1000),
    ("grpc.dns_min_time_between_resolutions_ms", 1000),
])
channel.subscribe(lambda state: log.info("gRPC connection: %s", state.name), try_to_connect=True)
stub = rpc.FlightServiceStub(grpc.intercept_channel(channel, Resilience()))
metadata = (("x-api-key", os.environ["GRPC_API_KEY"]),)
timeout = float(os.environ.get("GRPC_TIMEOUT", "1"))


@asynccontextmanager
async def lifespan(app):
    migrate("booking")
    yield
    channel.close()


app = FastAPI(title="Flight Booking", lifespan=lifespan)


@app.exception_handler(psycopg.Error)
async def database_error(request, error):
    log.error("Booking database error: %s", error)
    return JSONResponse(status_code=503, content={"detail": "База бронирований недоступна"})


def call(method, request):
    try:
        return getattr(stub, method)(request, metadata=metadata, timeout=timeout)
    except grpc.RpcError as error:
        status = {
            grpc.StatusCode.INVALID_ARGUMENT: 400,
            grpc.StatusCode.NOT_FOUND: 404,
            grpc.StatusCode.RESOURCE_EXHAUSTED: 409,
            grpc.StatusCode.ALREADY_EXISTS: 409,
            grpc.StatusCode.FAILED_PRECONDITION: 409,
            grpc.StatusCode.UNAUTHENTICATED: 401,
            grpc.StatusCode.UNAVAILABLE: 503,
            grpc.StatusCode.DEADLINE_EXCEEDED: 503,
        }.get(error.code(), 502)
        raise HTTPException(status, error.details()) from error


def flight_json(flight):
    return {
        "id": flight.id, "flight_number": flight.flight_number, "airline": flight.airline,
        "origin": flight.origin, "destination": flight.destination,
        "departure_time": flight.departure_time.ToDatetime(timezone.utc),
        "arrival_time": flight.arrival_time.ToDatetime(timezone.utc),
        "total_seats": flight.total_seats, "available_seats": flight.available_seats,
        "price": flight.price, "status": pb.FlightStatus.Name(flight.status),
    }


class NewBooking(BaseModel):
    user_id: str = Field(min_length=1, pattern=r"\S")
    flight_id: UUID
    passenger_name: str = Field(min_length=1, pattern=r"\S")
    passenger_email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    seat_count: int = Field(gt=0, le=2147483647)


@app.get("/flights")
def search_flights(origin: str = Query(pattern=r"^[A-Z]{3}$"),
                   destination: str = Query(pattern=r"^[A-Z]{3}$"), date: date | None = None):
    request = pb.SearchFlightsRequest(origin=origin, destination=destination)
    if date is not None:
        request.date.FromDatetime(datetime.combine(date, time.min, timezone.utc))
    return [flight_json(flight) for flight in call("SearchFlights", request).flights]


@app.get("/flights/{flight_id}")
def get_flight(flight_id: UUID):
    return flight_json(call("GetFlight", pb.GetFlightRequest(flight_id=str(flight_id))))


@app.post("/bookings", status_code=201)
def create_booking(data: NewBooking):
    flight = call("GetFlight", pb.GetFlightRequest(flight_id=str(data.flight_id)))
    booking_id = str(uuid4())
    reserved = False
    try:
        with connect() as db:
            call("ReserveSeats", pb.ReserveSeatsRequest(flight_id=str(data.flight_id),
                 booking_id=booking_id, seat_count=data.seat_count))
            reserved = True
            row = db.execute("""INSERT INTO bookings VALUES (%s, %s, %s, %s, %s, %s, %s, 'CONFIRMED')
                RETURNING *""", (booking_id, data.user_id, str(data.flight_id), data.passenger_name,
                data.passenger_email, data.seat_count, data.seat_count * flight.price)).fetchone()
    except Exception as error:
        if reserved or (isinstance(error, HTTPException) and error.status_code == 503):
            try:
                call("ReleaseReservation", pb.ReleaseReservationRequest(booking_id=booking_id))
            except HTTPException as release_error:
                if release_error.status_code != 404:
                    log.exception("Compensation failed for booking %s", booking_id)
        raise
    return row


@app.get("/bookings")
def list_bookings(user_id: str = Query(min_length=1)):
    with connect() as db:
        return db.execute("SELECT * FROM bookings WHERE user_id = %s ORDER BY id", (user_id,)).fetchall()


@app.get("/bookings/{booking_id}")
def get_booking(booking_id: UUID):
    with connect() as db:
        row = db.execute("SELECT * FROM bookings WHERE id = %s", (str(booking_id),)).fetchone()
        if not row:
            raise HTTPException(404, "Бронирование не найдено")
        return row


@app.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: UUID):
    with connect() as db:
        row = db.execute("SELECT * FROM bookings WHERE id = %s FOR UPDATE", (str(booking_id),)).fetchone()
        if not row:
            raise HTTPException(404, "Бронирование не найдено")
        if row["status"] != "CONFIRMED":
            raise HTTPException(409, "Бронирование уже отменено")
        call("ReleaseReservation", pb.ReleaseReservationRequest(booking_id=str(booking_id)))
        row = db.execute("UPDATE bookings SET status = 'CANCELLED' WHERE id = %s RETURNING *",
                         (str(booking_id),)).fetchone()
    return row
