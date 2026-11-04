# Flight Booking — ДЗ №3

Система бронирования авиабилетов: Booking Service с REST API и Flight Service с gRPC,
у каждого своя PostgreSQL. Реализованы транзакции, авторизация, кеширование через
Redis Sentinel, retry и circuit breaker.

[ER-диаграммы](docs/er.md) · [Коды ошибок gRPC](docs/grpc.md)

## Запуск

Нужны Docker и Docker Compose.

```bash
docker compose up --build
```

Миграции применяются автоматически. При первом запуске добавляются демонстрационные рейсы.
API и примеры запросов: <http://localhost:8000/docs>. Цены указаны в копейках.

## Проверки

```bash
docker compose exec booking python -m unittest discover -s tests -v
```

Проверка отключения Flight Service и переключения Redis-мастера:

```bash
python3 tests/check_failures.py
```

Остановка: `docker compose down`.
