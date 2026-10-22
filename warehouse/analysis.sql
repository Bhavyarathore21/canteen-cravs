-- ============================================================================
-- Canteen Cravs: business questions answered with SQL on the star schema.
-- Each query starts with "-- name:" so run_analysis.py can run and save them.
-- Money is in rupees. "Revenue" only counts Completed orders.
-- ============================================================================

-- name: 01_weekly_trend
-- Q1. Is the app growing? Orders, revenue and waiting time per week.
SELECT d.week_of_year                                   AS week,
       MIN(d.date)                                      AS week_starts,
       COUNT(*)                                         AS orders,
       ROUND(SUM(CASE WHEN f.order_status = 'Completed' THEN f.total_amount ELSE 0 END)) AS revenue,
       ROUND(AVG(f.wait_minutes), 1)                    AS avg_wait_min
FROM fact_orders f
JOIN dim_date d USING (date_key)
GROUP BY d.week_of_year
ORDER BY week_starts;

-- name: 02_peak_hours
-- Q2. When is the rush? Average number of orders per open day, by hour.
SELECT t.hour_label,
       t.day_slot,
       ROUND(COUNT(*) * 1.0 / (SELECT COUNT(*) FROM dim_date WHERE is_open_day), 1) AS avg_orders_per_day
FROM fact_orders f
JOIN dim_time t USING (time_key)
GROUP BY t.hour_label, t.day_slot
ORDER BY t.hour_label;

-- name: 03_top_items
-- Q3. What sells? Units and revenue per item (Completed orders), plus units wasted
-- (cooked but never collected, i.e. Expired orders).
SELECT i.item_name,
       i.category,
       SUM(CASE WHEN oi.order_status = 'Completed' THEN oi.quantity ELSE 0 END)   AS units_sold,
       SUM(CASE WHEN oi.order_status = 'Completed' THEN oi.line_total ELSE 0 END) AS revenue,
       SUM(CASE WHEN oi.order_status = 'Expired'   THEN oi.quantity ELSE 0 END)   AS units_wasted,
       ROUND(100.0 * SUM(CASE WHEN oi.order_status = 'Completed' THEN oi.line_total ELSE 0 END)
             / SUM(SUM(CASE WHEN oi.order_status = 'Completed' THEN oi.line_total ELSE 0 END)) OVER (), 1) AS pct_of_revenue
FROM fact_order_items oi
JOIN dim_menu_item i USING (item_key)
GROUP BY i.item_name, i.category
ORDER BY revenue DESC;

-- name: 04_sellouts
-- Q4. What runs out, and when? One row per item with the days it sold out
-- and the usual time.
WITH first_out AS (
    SELECT item_key, date_key, MIN(event_time) AS sold_out_at
    FROM fact_stock_events
    WHERE is_sell_out
    GROUP BY item_key, date_key
)
SELECT i.item_name,
       COUNT(*)                                                     AS days_sold_out,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM dim_date WHERE is_open_day), 1) AS pct_of_open_days,
       strftime(TIMESTAMP '2000-01-01' + to_seconds(CAST(AVG(epoch(sold_out_at) % 86400) AS BIGINT)), '%H:%M') AS usual_time
FROM first_out f
JOIN dim_menu_item i USING (item_key)
GROUP BY i.item_name
ORDER BY days_sold_out DESC;

-- name: 05_lost_sales_estimate
-- Q5. How much money is lost when items sell out?
-- Nobody records the customers who wanted an item that was gone, so we ESTIMATE it:
--   1. On days an item was still available, find how many units of it each order
--      contained in every hour  ("units per order").
--   2. On days it sold out, take the orders placed AFTER the sell-out time and apply
--      that same rate: those are the units customers would probably have bought.
WITH first_out AS (                       -- when each item first sold out each day
    SELECT item_key, date_key, MIN(event_time) AS sold_out_at
    FROM fact_stock_events
    WHERE is_sell_out
    GROUP BY item_key, date_key
),
orders_per_hour AS (                      -- how many orders arrived in each hour
    SELECT date_key, time_key, COUNT(*) AS orders
    FROM fact_orders
    GROUP BY date_key, time_key
),
units_per_hour AS (                       -- how many units of each item were sold in each hour
    SELECT date_key, time_key, item_key, SUM(quantity) AS units
    FROM fact_order_items
    GROUP BY date_key, time_key, item_key
),
grid AS (                                 -- every (day, hour, item) combination
    SELECT o.date_key, o.time_key, o.orders, i.item_key, i.price,
           COALESCE(u.units, 0)                                  AS units,
           f.sold_out_at,
           CAST(d.date AS TIMESTAMP) + to_hours(o.time_key)      AS hour_start
    FROM orders_per_hour o
    CROSS JOIN dim_menu_item i
    JOIN dim_date d ON d.date_key = o.date_key
    LEFT JOIN units_per_hour u ON u.date_key = o.date_key AND u.time_key = o.time_key AND u.item_key = i.item_key
    LEFT JOIN first_out f ON f.item_key = i.item_key AND f.date_key = o.date_key
),
rates AS (                                -- units of the item per order, in hours it was available
    SELECT item_key, time_key, SUM(units) * 1.0 / SUM(orders) AS units_per_order
    FROM grid
    WHERE sold_out_at IS NULL OR sold_out_at >= hour_start + INTERVAL 1 HOUR
    GROUP BY item_key, time_key
),
lost AS (                                 -- hours after (or during) the sell-out
    SELECT g.item_key, g.date_key, g.price,
           g.orders
           * LEAST(1.0, epoch(g.hour_start + INTERVAL 1 HOUR - GREATEST(g.sold_out_at, g.hour_start)) / 3600.0)
           * COALESCE(r.units_per_order, 0)                       AS lost_units
    FROM grid g
    LEFT JOIN rates r ON r.item_key = g.item_key AND r.time_key = g.time_key
    WHERE g.sold_out_at IS NOT NULL
      AND g.hour_start + INTERVAL 1 HOUR > g.sold_out_at
),
by_item AS (
    SELECT i.item_name,
           COUNT(DISTINCT l.date_key)          AS days_sold_out,
           ROUND(SUM(l.lost_units))            AS est_lost_units,
           ROUND(SUM(l.lost_units * l.price))  AS est_lost_revenue
    FROM lost l
    JOIN dim_menu_item i ON i.item_key = l.item_key
    GROUP BY i.item_name
)
SELECT * FROM (
    SELECT * FROM by_item
    UNION ALL
    SELECT 'TOTAL', NULL, SUM(est_lost_units), SUM(est_lost_revenue) FROM by_item
)
ORDER BY (item_name = 'TOTAL'), est_lost_revenue DESC;

-- name: 06_wait_times
-- Q6. How long do students wait for their food (order placed -> ready)?
-- p90 means "9 out of 10 students waited less than this".
SELECT t.hour_label,
       t.day_slot,
       COUNT(*)                                     AS orders,
       ROUND(AVG(f.wait_minutes), 1)                AS avg_wait_min,
       ROUND(quantile_cont(f.wait_minutes, 0.9), 1) AS p90_wait_min
FROM fact_orders f
JOIN dim_time t USING (time_key)
WHERE f.wait_minutes IS NOT NULL
GROUP BY t.hour_label, t.day_slot
ORDER BY t.hour_label;

-- name: 07_payments_and_no_shows
-- Q7. UPI vs cash: who cancels, who doesn't collect, and what does it cost?
SELECT f.payment_method,
       COUNT(*)                                                                     AS orders,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)                           AS pct_of_orders,
       ROUND(100.0 * AVG(CASE WHEN f.order_status = 'Cancelled' THEN 1 ELSE 0 END), 1) AS cancel_rate_pct,
       ROUND(100.0 * AVG(CASE WHEN f.order_status = 'Expired'   THEN 1 ELSE 0 END), 1) AS no_show_rate_pct,
       ROUND(SUM(CASE WHEN f.order_status = 'Expired' THEN f.total_amount ELSE 0 END)) AS food_made_not_collected,
       ROUND(SUM(CASE WHEN f.order_status = 'Expired' AND f.payment_status <> 'Paid'
                      THEN f.total_amount ELSE 0 END))                              AS money_never_received,
       ROUND(SUM(f.refunded_amount))                                                AS refunded
FROM fact_orders f
GROUP BY f.payment_method
ORDER BY orders DESC;

-- name: 08_repeat_no_shows
-- Q8. Is a small group of students responsible for most no-shows?
WITH grouped AS (
    SELECT CASE WHEN no_show_count >= 3 THEN '3 or more no-shows'
                WHEN no_show_count >= 1 THEN '1 to 2 no-shows'
                ELSE 'never' END AS group_name,
           COUNT(*)           AS students,
           SUM(no_show_count) AS no_shows
    FROM dim_user
    WHERE role = 'user'
    GROUP BY 1
)
SELECT group_name,
       students,
       no_shows,
       ROUND(100.0 * students / SUM(students) OVER (), 1) AS pct_of_students,
       ROUND(100.0 * no_shows / SUM(no_shows) OVER (), 1) AS pct_of_all_no_shows
FROM grouped
ORDER BY no_shows DESC;

-- name: 09_prep_recommendation
-- Q9. How much should the kitchen prepare? Last 28 open days, per item.
-- A rule of thumb (not a forecast): often sold out -> prepare more; large leftovers -> prepare less.
WITH recent AS (
    SELECT date_key
    FROM dim_date
    WHERE is_open_day
      AND date > (SELECT MAX(date) FROM dim_date WHERE is_open_day) - INTERVAL 28 DAY
),
prep AS (
    SELECT item_key, MAX(quantity_after) AS daily_prep
    FROM fact_stock_events
    WHERE event_type = 'daily_prep'
    GROUP BY item_key
),
sold AS (                                  -- units that left the kitchen (cancelled orders excluded)
    SELECT date_key, item_key, SUM(quantity) AS units
    FROM fact_order_items
    WHERE order_status <> 'Cancelled'
    GROUP BY date_key, item_key
),
out_days AS (
    SELECT DISTINCT item_key, date_key FROM fact_stock_events WHERE is_sell_out
)
SELECT i.item_name,
       p.daily_prep,
       ROUND(AVG(COALESCE(s.units, 0)), 1)                          AS avg_units_sold,
       COUNT(*)                                                     AS open_days,
       COUNT(o.date_key)                                            AS days_sold_out,
       ROUND(100.0 * COUNT(o.date_key) / COUNT(*))                  AS pct_days_sold_out,
       ROUND(AVG(CASE WHEN o.date_key IS NULL THEN p.daily_prep - COALESCE(s.units, 0) END), 1) AS avg_leftover_other_days,
       CASE WHEN 100.0 * COUNT(o.date_key) / COUNT(*) >= 25 THEN 'Prepare about 15% more'
            WHEN 100.0 * COUNT(o.date_key) / COUNT(*) >= 10 THEN 'Prepare about 8% more'
            WHEN AVG(CASE WHEN o.date_key IS NULL THEN p.daily_prep - COALESCE(s.units, 0) END) > 0.4 * p.daily_prep THEN 'Prepare less (leftovers)'
            ELSE 'Keep as is' END                                   AS suggestion
FROM recent r
CROSS JOIN dim_menu_item i
JOIN prep p ON p.item_key = i.item_key
LEFT JOIN sold s     ON s.date_key = r.date_key AND s.item_key = i.item_key
LEFT JOIN out_days o ON o.date_key = r.date_key AND o.item_key = i.item_key
GROUP BY i.item_name, p.daily_prep
ORDER BY pct_days_sold_out DESC, i.item_name;
