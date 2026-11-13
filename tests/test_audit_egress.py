"""What the audits send: every request a sheet, a draft or the analogy spike makes goes out under policy B, names
said as said, and is one egress row, holding exactly what was sent and the segments behind the clusters it showed."""

import json
from pathlib import Path
from types import SimpleNamespace

from im import analogy_spike, connect, edge_audit, egress, probe_audit

from helpers import HashEmbedder
from test_edge_audit import NAMED, fill, named_clusters  # noqa: F401 (fixture)
from test_lattice import seeded  # noqa: F401 (fixture)
from test_probe_audit import every_pair, fake_jev
from test_probe_audit import fill as fill_probe

class FakeChat:
    """Answers any audit's question, naming Bob in its reasoning if it was shown him, as a model would; and records
    every request."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def sent(self):
        return [c["messages"][0]["content"] for c in self.calls]

    def create(self, **kw):
        self.calls.append(kw)
        text = kw["messages"][0]["content"]
        who = "Bob" if "Bob" in text else "someone"
        props = kw["output_config"]["format"]["schema"]["properties"]
        n = text.count("\n")   # a batch: "Ideas:" and then one line per idea
        if "answer" in props:
            answer = {"answer": "yes", "why": f"{who} pulls"}
        elif "label" in props:
            answer = {"label": "related", "direction": "none", "analogy": f"{who} moves", "why": f"{who} belongs"}
        elif "ideas" in props:
            answer = {"ideas": [{"idea": i, "structures": [f"{who} line {i}", f"b {i}", f"c {i}", f"d {i}"]}
                                for i in range(1, n + 1)]}
        elif "structures" in props:
            answer = {"structures": [{"idea": i, "structure": f"{who} acts {i}"} for i in range(1, n + 1)]}
        else:
            answer = {"pairs": [{"a": 1, "b": 2, "why": f"{who} pulls"}, {"a": 1, "b": 3, "why": f"{who} again"}]}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(answer))], stop_reason="end_turn",
                               model=kw["model"], _request_id="r",
                               usage=SimpleNamespace(input_tokens=900, output_tokens=90))


def said_on(conn, ideas) -> list:
    """The segments of what these clusters show as said, worked out from what a sheet shows of them."""
    words = [q["words"] for i in ideas for q in connect.side_data(conn, i)["said"]]
    return sorted(s for (s,) in conn.execute("SELECT segment_id FROM im.source_segments WHERE text = ANY(%s)",
                                             (words,)))


def last_egress(conn) -> int:
    return conn.execute("SELECT coalesce(max(egress_id), 0) FROM im.egress_log").fetchone()[0]


def assert_logged(conn, since: int, requests: list[tuple[str, list]]) -> None:
    """One egress row per request, in order: what was sent, the segments behind the clusters it showed, policy B."""
    rows = conn.execute("""SELECT payload, segment_ids, privacy_policy_version FROM im.egress_log
                           WHERE egress_id > %s ORDER BY egress_id""", (since,)).fetchall()
    assert [(p, sorted(s), policy) for p, s, policy in rows] == \
        [(text, said_on(conn, ideas), egress.POLICY_VERSION) for text, ideas in requests]
    assert any(s for _, s, _ in rows)   # some showed something said, so a row without its segments shows


def assert_named(sent: list[str]) -> None:
    assert any("Bob" in t for t in sent)   # his name went out as said


def test_a_b4_draft_sends_names_as_said(named_clusters, tmp_path):  # noqa: F811
    conn = named_clusters
    edge_audit.write(conn, tmp_path)
    assert NAMED in (tmp_path / "sheet.md").read_text()
    fill(tmp_path, lambda p: ("rel", ""))
    since, claude = last_egress(conn), FakeChat()
    out = edge_audit.draft(conn, claude, tmp_path)
    assert_named(claude.sent())
    pairs = [p for _, p in sorted(json.loads((tmp_path / "key.json").read_text())["pairs"].items(),
                                  key=lambda kv: int(kv[0]))]
    assert_logged(conn, since, [(t, p["ideas"]) for t, p in zip(claude.sent(), pairs, strict=True)])
    draft = json.loads(Path(out["draft"]).read_text()).values()
    for field, said in (("analogy", "Bob moves"), ("why", "Bob belongs")):
        assert said in [d[field] for d in draft]


def test_a_jev_b4_draft_sends_names_as_said(named_clusters, tmp_path):  # noqa: F811
    conn = named_clusters
    edge_audit.write(conn, tmp_path)
    fill(tmp_path, lambda p: ("rel", ""))
    since, (client, sent) = last_egress(conn), fake_jev()
    edge_audit.draft(conn, client, tmp_path, "jev-1.14.0")
    assert_named([json.dumps(b["state"]) for b in sent])
    pairs = [p for _, p in sorted(json.loads((tmp_path / "key.json").read_text())["pairs"].items(),
                                  key=lambda kv: int(kv[0]))]
    assert_logged(conn, since, [(json.dumps(b), p["ideas"]) for b, p in zip(sent, pairs, strict=True)])


def test_a_tension_sheet_and_its_draft_send_names_as_said(named_clusters, tmp_path):  # noqa: F811
    conn = named_clusters
    since, proposer = last_egress(conn), FakeChat()
    probe_audit.write(conn, proposer, None, "tension", tmp_path)
    (picks,) = proposer.sent()   # the model set: one request reading every one of my clusters
    assert_named([picks])
    mine = [r[0] for r in edge_audit._clusters(conn) if r[2]]
    assert_logged(conn, since, [(picks, mine)])
    # Its reasons come back as given (which of its pairs the sheet draws depends on the clusters' ids).
    assert [p["why"] for p in probe_audit.picks(conn, FakeChat(), "tension", mine)] == ["Bob pulls", "Bob again"]
    key = json.loads((tmp_path / "key.json").read_text())

    fill_probe(tmp_path, lambda p: "no")
    since, claude = last_egress(conn), FakeChat()
    d = probe_audit.draft(conn, claude, tmp_path, "claude-opus-5-5")
    assert_named(claude.sent())
    pairs = [p for _, p in sorted(key["pairs"].items(), key=lambda kv: int(kv[0]))]
    assert_logged(conn, since, [(t, p["ideas"]) for t, p in zip(claude.sent(), pairs, strict=True)])
    whys = [a["why"] for a in json.loads(Path(d["draft"]).read_text()).values()]
    assert "Bob pulls" in whys


def test_an_analogy_sheets_facets_send_names_as_said(named_clusters, tmp_path):  # noqa: F811
    conn = named_clusters
    since, claude = last_egress(conn), FakeChat()
    probe_audit.write(conn, claude, HashEmbedder(), "analogy", tmp_path, only="facets")
    (batch,) = claude.sent()   # every one of my clusters, in one batch
    assert_named([batch])
    mine = [r[0] for r in edge_audit._clusters(conn) if r[2]]
    assert_logged(conn, since, [(batch, mine)])
    lines = [s for structures in json.loads((tmp_path / "facets.json").read_text()).values() for s in structures]
    assert any(s.startswith("Bob line") for s in lines)


def test_jevs_pool_sends_names_as_said(named_clusters, tmp_path, monkeypatch):  # noqa: F811
    conn = named_clusters
    monkeypatch.setattr(probe_audit, "JEV_WORKERS", 1)   # one at a time: sent, logged and answered in order
    pairs = every_pair(conn)
    since, (client, sent) = last_egress(conn), fake_jev()
    probe_audit.jev_pool(conn, client, pairs, tmp_path)
    assert_named([json.dumps(b["state"]) for b in sent])
    assert_logged(conn, since, [(json.dumps(b), p["ideas"]) for b, p in zip(sent, pairs, strict=True)])


def test_a_jev_draft_sends_names_as_said(named_clusters, tmp_path):  # noqa: F811
    conn = named_clusters
    probe_audit.write(conn, FakeChat(), None, "tension", tmp_path)
    fill_probe(tmp_path, lambda p: "no")
    since, (client, sent) = last_egress(conn), fake_jev()
    probe_audit.draft(conn, client, tmp_path, "jev-1.14.0")
    assert_named([json.dumps(b["state"]) for b in sent])
    key = json.loads((tmp_path / "key.json").read_text())
    pairs = [p for _, p in sorted(key["pairs"].items(), key=lambda kv: int(kv[0]))]
    assert_logged(conn, since, [(json.dumps(b), p["ideas"]) for b, p in zip(sent, pairs, strict=True)])


def test_the_analogy_spike_sends_names_as_said(named_clusters, tmp_path):  # noqa: F811
    conn = named_clusters
    edge_audit.write(conn, tmp_path, size=2)
    fill(tmp_path, lambda p: ("none", "analogy: same move"))
    since, claude = last_egress(conn), FakeChat()
    analogy_spike.run(conn, claude, HashEmbedder(), tmp_path, others=10)
    (batch,) = claude.sent()   # the pairs' clusters and every other, in one batch
    assert_named([batch])
    shown = [i for (i,) in conn.execute("""SELECT c.idea_id::text FROM pub.cards c
                                           JOIN im.idea_embeddings e USING (idea_id)""")]
    assert_logged(conn, since, [(batch, shown)])
    structures = json.loads((tmp_path / "structures.json").read_text()).values()
    assert any(s.startswith("Bob acts") for s in structures)
