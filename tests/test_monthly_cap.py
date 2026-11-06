"""The monthly cap, at its edges: a paid request goes out while this calendar month's spend is below the cap (so the
one that reaches it can pass it), and none goes once the spend has reached it. Every stage sends by egress.send."""

from im import egress, llm


def spent(conn, usd: float, at: str) -> None:
    """A request logged at `at` (SQL), costing `usd`."""
    conn.execute(f"""INSERT INTO im.egress_log (segment_ids, model, prompt_version, privacy_policy_version, payload,
                       payload_sha256, cost_usd, created_at) VALUES ('{{}}', 'm', 'p', 'B', 'x', '', %s, {at})""",
                 (usd,))


def goes(conn, cap: float) -> bool:
    """Whether a request costing $0.50 goes out under this cap; one that doesn't is neither sent nor logged."""
    went, rows = [], conn.execute("SELECT count(*) FROM im.egress_log").fetchone()[0]

    def request():
        went.append(True)
        return llm.Reply("{}", "end_turn", "m", "r", 1, 1, 0.5)
    try:
        egress.send(conn, request, model="m", payload="x", segment_ids=[], prompt_version="p", monthly_cap_usd=cap)
    except llm.Stop as e:
        assert str(e).startswith("monthly cap") and not went
        assert conn.execute("SELECT count(*) FROM im.egress_log").fetchone()[0] == rows
        return False
    return True


def test_a_request_goes_below_the_cap_even_if_it_passes_it_and_none_goes_at_it(pipe):
    spent(pipe, 0.99, "now()")
    assert goes(pipe, 1.0)        # a cent under: it goes, and takes the month to $1.49
    assert not goes(pipe, 1.49)   # at the cap
    assert goes(pipe, 1.5)


def test_only_this_calendar_months_spend_counts(pipe):
    spent(pipe, 5.0, "date_trunc('month', now()) - interval '1 microsecond'")   # the last moment of last month
    assert goes(pipe, 1.0)
    # The first moment of this month: earlier this month, so not today (unless today is the 1st), and it counts.
    spent(pipe, 0.5, "date_trunc('month', now())")
    assert not goes(pipe, 1.0)
