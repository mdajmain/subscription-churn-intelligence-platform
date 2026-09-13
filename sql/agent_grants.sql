-- Step 8.1 — a dedicated, minimally-privileged Postgres role for the
-- investigation agent. Every tool in agent/tools.py connects with this
-- role's credentials, never the app's own `churn` user. REVOKE first,
-- then GRANT only the four views explicitly, so the role starts from
-- "can see nothing" rather than "can see everything except what we
-- remembered to block."

DROP ROLE IF EXISTS agent_readonly;
CREATE ROLE agent_readonly WITH LOGIN PASSWORD 'agent_readonly_pw';

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM agent_readonly;
REVOKE ALL ON SCHEMA public FROM agent_readonly;

GRANT USAGE ON SCHEMA public TO agent_readonly;
GRANT SELECT ON v_monthly_metrics TO agent_readonly;
GRANT SELECT ON v_segment_snapshot TO agent_readonly;
GRANT SELECT ON v_activity_freshness TO agent_readonly;
GRANT SELECT ON v_prediction_summary TO agent_readonly;

-- Defense in depth beyond the grants: agent/tools.py also sets a
-- per-connection statement_timeout, so even an approved, legitimately
-- expensive query can't hang an investigation.
ALTER ROLE agent_readonly SET statement_timeout = '5s';
