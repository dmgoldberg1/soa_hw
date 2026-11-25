WITH {day:Date} AS target,
users AS
(
    SELECT
        user_id,
        minIf(event_date, event_type = 'VIEW_STARTED') AS cohort,
        groupUniqArrayIf(event_date, event_date >= subtractDays(target, 7)) AS active_days
    FROM cinema.events FINAL
    WHERE event_date <= addDays(target, 7)
    GROUP BY user_id
    HAVING countIf(event_type = 'VIEW_STARTED') > 0
       AND cohort IN (target, subtractDays(target, 1), subtractDays(target, 7))
)
SELECT
    cohort,
    count() AS cohort_size,
    sumForEach(arrayMap(offset -> toUInt64(has(active_days, addDays(cohort, offset))), range(8))) AS returned
FROM users
GROUP BY cohort
FORMAT JSONEachRow
