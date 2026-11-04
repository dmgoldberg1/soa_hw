# ER-диаграммы

## Flight Service

```mermaid
erDiagram
    airlines ||--o{ flight_numbers : owns
    flight_numbers ||--o{ flights : identifies
    flights ||--o{ seat_reservations : reserves
    airlines {
        integer id PK
        text code UK
        text name
    }
    flight_numbers {
        text flight_number PK
        integer airline_id FK
    }
    flights {
        uuid id PK
        text flight_number FK
        text origin
        text destination
        timestamptz departure_time
        timestamptz arrival_time
        integer total_seats
        integer available_seats
        bigint price
        text status
    }
    seat_reservations {
        uuid booking_id PK "Логическая ссылка на booking-db"
        uuid flight_id FK
        integer seat_count
        text status
    }
    cache_revision {
        integer id PK "Техническая таблица, единственная строка"
        bigint revision
    }
```

## Booking Service

```mermaid
erDiagram
    bookings {
        uuid id PK
        text user_id
        uuid flight_id "Логическая ссылка на flight-db"
        text passenger_name
        text passenger_email
        integer seat_count
        bigint total_price
        text status
    }
```

Схема соответствует 3NF: данные авиакомпаний и связь номера рейса с авиакомпанией вынесены в отдельные таблицы. Данные пассажира относятся к конкретному бронированию.

`bookings.flight_id` и `seat_reservations.booking_id` — логические ссылки между разными БД. Физические FK действуют внутри БД Flight Service. Пара номера рейса и UTC-даты вылета уникальна; ограничения целостности заданы в SQL-миграциях.
