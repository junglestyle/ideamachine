"""The capture gate: Jev's checks on the live captures, shown the transcript as sent, decided per check in code; and
its cuts, set from a sheet two models draft and I review only where they disagree."""

import json
import re
from dataclasses import replace
from types import SimpleNamespace

from im import egress, extract, fixtures, gate, gate_audit, jev

from helpers import run
from test_archive_captures import archive_capture
from test_extract import FakeClaude


class Drafter:
    """A chat model drafting the gate sheet: `answer(text)` is its marks for one capture."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        a = self.answer(kw["messages"][0]["content"])
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(a))], stop_reason="end_turn",
                               model=kw["model"], _request_id="req_d",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def judge(by_gist):
    """Jev answering 0.9 to the checks `by_gist` names for a capture's gist, 0.1 to the rest."""
    sent = []

    def post(body):
        sent.append(body)
        yes = by_gist.get(body["state"]["gist"], ())
        return {"model": "jev-1.14.0", "usage": {"input_tokens": 500, "output_tokens": 7},
                "answers": {q: {"type": "noul", "noul": 0.9 if q in yes else 0.1} for q in body["questions"]}}
    return jev.Jev(post=post), sent


def test_the_gate_keeps_repairs_or_drops_each_capture_from_what_jev_says_of_it(pipe, cfg, stream, tmp_path):
    turns = stream.conversations["c-conv"].turns
    turns[0] = replace(turns[0], text="Did you see the build broke again, Bob?")
    stream.write(stream.dir)
    run(pipe, cfg, stream)

    def respond(text):
        rows = {int(r[1:].split("]")[0]): r.split(": ", 1) for r in text.splitlines() if r.startswith("[")}
        said = [(n, r[1]) for n, r in rows.items() if r[0].endswith(" me") and "build broke" in r[1]]
        if not said:
            return []
        (n, line), = said
        base = {"quote": line, "lines": [n], "attributed_to": "", "reading": line, "mishearings": []}
        return [base | {"gist": "Bob's build broke again."}, base | {"gist": "Builds always break."},
                base | {"gist": "Nobody should ever fix builds."}]

    read = (extract.run_stage(pipe, FakeClaude(respond))["prompt_version"], "claude-opus-5-5")
    typed = archive_capture(pipe, fixtures.T0)   # no lines to check its gist against: left out, and counted
    pipe.execute("UPDATE im.items SET prompt_version = %s, privacy_policy_version = %s WHERE item_id = %s",
                 (read[0], egress.POLICY_VERSION, typed))
    client, sent = judge({"Builds always break.": ("overstates",), "Nobody should ever fix builds.": ("mocked",
                                                                                                     "two_points")})
    due = gate.run(pipe, client, read, 20.0, limit=0)
    assert (due["captures"], due["due"], due["asked"], due["archive_skipped"], len(sent)) == (3, 3, 0, 1, 0)
    stats = gate.run(pipe, client, read, 20.0)
    assert (stats["captures"], stats["asked"], stats["failed"], stats["due"]) == (3, 3, 0, 0)
    shown = sent[0]["state"]
    assert shown["gist"] == "Bob's build broke again."
    assert "build broke again" in shown["lines"] and shown["lines"] in shown["context"]

    s = gate.summary(pipe, client, read)
    assert s["decisions"] == {"keep": 1, "repair": 1, "drop": 1}
    assert s["by_check"]["overstates"]["likeliest"][0]["gist"] == "Builds always break."
    assert s["by_check"]["two_points"]["at_cut"] == 1   # flagged, and doesn't decide

    # Its cuts: a sheet of what Jev answered about, two models' drafts, my review of where they disagree.
    folder = tmp_path / "gate-1"
    out = gate_audit.write(pipe, folder, client, read, size=4)
    assert out["by_set"] == {"flagged": 2, "not_flagged": 1}
    assert "Bob" in (folder / "sheet.md").read_text()

    def marks(lenient):
        def answer(text):
            past = "always" in text and not lenient
            return {"gist": "past" if past else "ok", "gist_why": "a rule from one case" if past else "as said",
                    "capture": "drop" if "Nobody should" in text else "keep", "capture_why": "mocked"}
        return answer

    for model, lenient in (("claude-opus-5-5", False), ("claude-sonnet-5-5", True)):
        drafter = Drafter(marks(lenient))
        d = gate_audit.draft(pipe, folder, drafter, model, "low", 20.0)
        assert d["drafted"] == 3
    r = gate_audit.review(folder)
    assert r["to_review"] == 1
    review = (folder / "review.md").read_text()
    assert "Builds always break." in review and "capture:" not in review   # only the mark they split on
    (folder / "review.md").write_text(re.sub(r"^gist: $", "gist: past", review, flags=re.M))

    s = gate_audit.score(folder)
    assert s["labels"] == {"from_me": 1, "from_drafts": 5, "missing": 0}
    assert (s["repair"]["marked_past_or_unclear"], s["repair"]["auc"], s["repair"]["suggested_cut"]) == (1, 1.0, 0.9)
    assert (s["drop"]["marked_drop"], s["drop"]["auc"], s["drop"]["suggested_cut"]) == (1, 1.0, 0.3)
    assert gate.run(pipe, client, read, 20.0)["asked"] == 0 and len(sent) == 3
