WITH {day:Date32} AS cohort_day
SELECT
    day_number,
    if(
        addDays(cohort_day, day_number) > {today:Date32},
        NULL,
        avg(has(active_days, addDays(cohort_day, day_number)))
    ) AS retention
FROM
(
    SELECT
        user_id,
        minOrNullIf(event_date, event_type = 'VIEW_STARTED') AS first_view,
        groupUniqArrayIf(event_date, event_date >= cohort_day) AS active_days
    FROM cinema.events
    WHERE event_date <= least(addDays(cohort_day, 7), {today:Date32})
    GROUP BY user_id
    HAVING first_view = cohort_day
)
ARRAY JOIN range(8) AS day_number
GROUP BY day_number
ORDER BY day_number
