SELECT
    count() AS events_processed,
    uniqExact(user_id) AS dau,
    avgOrNullIf(progress_seconds, event_type = 'VIEW_FINISHED') AS avg_watch_seconds,
    countIf(event_type = 'VIEW_FINISHED') / nullIf(countIf(event_type = 'VIEW_STARTED'), 0) AS conversion
FROM cinema.events FINAL
WHERE event_date = {day:Date}
FORMAT JSONEachRow
