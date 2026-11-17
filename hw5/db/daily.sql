SELECT
    count() AS events,
    uniqExact(user_id) AS dau,
    avgOrNullIf(progress_seconds, event_type = 'VIEW_FINISHED') AS avg_watch_seconds,
    countIf(event_type = 'VIEW_FINISHED') / nullIf(countIf(event_type = 'VIEW_STARTED'), 0) AS conversion,
    sumMapIf(map(movie_id, toUInt64(1)), event_type = 'VIEW_STARTED') AS movie_views
FROM cinema.events FINAL
WHERE event_date = {day:Date32}
