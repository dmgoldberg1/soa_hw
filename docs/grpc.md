# Ошибки Flight Service

Контракт: [flight.proto](../proto/flight.proto).

| Методы | Код | Причина |
|---|---|---|
| Все | UNAUTHENTICATED | Отсутствует или неверен x-api-key |
| Все | INVALID_ARGUMENT | Некорректные входные данные |
| Все | UNAVAILABLE | Ошибка доступа к PostgreSQL |
| GetFlight, ReserveSeats, UpdateFlight | NOT_FOUND | Рейс не найден |
| ReserveSeats | RESOURCE_EXHAUSTED | Недостаточно мест |
| ReserveSeats | FAILED_PRECONDITION | Рейс недоступен для бронирования или резервация уже отменена |
| ReserveSeats | ALREADY_EXISTS | booking_id использован с другими параметрами |
| ReleaseReservation | NOT_FOUND | Резервация не найдена |

Повтор ReserveSeats с тем же booking_id и параметрами возвращает существующую
ACTIVE-резервацию. Повтор ReleaseReservation возвращает прежний результат.
Цены указаны в копейках; необязательная дата поиска задаёт сутки UTC.
