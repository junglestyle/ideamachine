-- Run status for Lattice: is the unattended hourly run healthy? Numbers and messages only, no conversation text.

-- The last two weeks of `im run`s. cost_usd is everything the run sent to Claude (extraction, matching, themes).
CREATE VIEW pub.runs AS
SELECT r.run_id, r.started_at, r.finished_at,
       r.stats->>'error' AS error,
       array_remove(ARRAY[r.stats->'extract'->>'stopped', r.stats->'lattice'->>'stopped', r.stats->'themes'->>'stopped',
                          r.stats->'themes'->'file'->>'stopped', r.stats->'themes'->'propose'->>'stopped'], NULL)
         || coalesce(ARRAY(SELECT jsonb_array_elements_text(r.stats->'triage_notes')), '{}') AS warnings,
       coalesce((r.stats->>'conversations_changed')::int, 0) AS conversations_changed,
       coalesce((r.stats->'extract'->>'read')::int, 0) AS episodes_read,
       coalesce((r.stats->'extract'->>'items')::int, 0) AS captured,
       coalesce((r.stats->'extract'->>'refused')::int, 0) AS refused,
       (SELECT coalesce(sum(g.cost_usd), 0) FROM im.egress_log g
        WHERE g.created_at BETWEEN r.started_at AND coalesce(r.finished_at, now())) AS cost_usd,
       (r.stats->>'monthly_cap_usd')::numeric AS monthly_cap_usd
FROM im.runs r
WHERE r.command = 'run' AND r.started_at > now() - interval '14 days';

-- What this month has cost so far, from the egress log (the same number the budget cap checks).
CREATE VIEW pub.spend AS
SELECT coalesce(sum(cost_usd), 0) AS month_to_date, date_trunc('month', now()) AS month
FROM im.egress_log WHERE created_at >= date_trunc('month', now());

GRANT SELECT ON pub.runs, pub.spend TO lattice_app;
