-- Retire policy C (decision 0008, amended 2026-11-11: policy C retired): names said go out as said under policy B,
-- so nothing looks for names any more. The names GLiNER found per segment (040) go, and with them the names role's
-- grants on them (install/nas.sh drops the role). Rows read under policy C keep its version as history.
DROP TABLE im.segment_names;
