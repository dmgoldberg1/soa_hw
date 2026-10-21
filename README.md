# Marketplace

Учебное REST API на Python и FastAPI. Реализованы пункты 1–7 задания: CRUD товаров с пагинацией, фильтрами и мягким удалением; заказы с резервированием остатков и промокодами; валидация и единый формат ошибок.

PostgreSQL, миграции Flyway. Модели и HTTP-обработчики генерируются из `openapi/marketplace.yaml` при сборке.

Для запуска нужны Docker и Docker Compose:

```bash
cp .env.example .env
docker compose up --build
```

Swagger UI: http://localhost:8000/docs

## Пример проверки

В Swagger выполнить запросы по очереди:

1. `POST /products` с телом `{"name":"Книга","price":100,"stock":10,"category":"books","status":"ACTIVE"}`. Ожидается HTTP 201. Скопировать `id` товара.
2. `POST /orders` с телом `{"items":[{"product_id":"ID товара","quantity":2}]}`. В `product_id` подставить полученный UUID, в `X-User-Id` — любой новый UUID пользователя. Ожидается HTTP 201, `status=CREATED`, `total_amount=200`. Скопировать `id` заказа.
3. `GET /products/{id}` для созданного товара. Ожидается `stock=8`.
4. `POST /orders/{id}/cancel` для созданного заказа с тем же `X-User-Id`. Ожидается HTTP 200, `status=CANCELED`.
5. Повторить `GET /products/{id}`. Остаток должен восстановиться: `stock=10`.
