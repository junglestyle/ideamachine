"""The capture gate: Jev's checks on a trial's captures, shown the transcript as sent, decided per check in code; and
its cuts, set from a sheet two models draft and I review only where they disagree."""

import json
import re
from dataclasses import replace
from types import SimpleNamespace

from im import egress, extract, gate, gate_audit, jev, names, trial

from helpers import run
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
    v5, schema = extract.system_prompt("v5"), extract.schema_for("v5")
    scrubber = names.Scrubber(b"k", egress.known_names(pipe))
    bob = names.token("Bob", b"k")

    def respond(text):
        rows = {int(r[1:].split("]")[0]): r.split(": ", 1) for r in text.splitlines() if r.startswith("[")}
        n, (_, said) = next((n, r) for n, r in rows.items() if r[0].endswith(" me") and "build broke" in r[1])
        base = {"quote": said, "lines": [n], "attributed_to": "", "reading": said, "mishearings": []}
        return [base | {"gist": f"{bob}'s build broke again."}, base | {"gist": "Builds always break."},
                base | {"gist": "Nobody should ever fix builds."}]

    trial.run(pipe, FakeClaude(respond), v5, scrubber=scrubber, schema=schema)
    read = (trial.version("claude-opus-5-5", "medium", v5, schema=schema), "claude-opus-5-5")
    client, sent = judge({"Builds always break.": ("overstates",), "Nobody should ever fix builds.": ("mocked",
                                                                                                     "two_points")})
    stats = gate.run(pipe, client, read, scrubber.policy, scrubber, 20.0)
    assert (stats["captures"], stats["asked"], stats["failed"]) == (3, 3, 0)
    shown = sent[0]["state"]
    assert shown["gist"] == f"{bob}'s build broke again." and "Bob" not in str(sent)   # names go out as sent
    assert "build broke again" in shown["lines"] and shown["lines"] in shown["context"]

    s = gate.summary(pipe, client, read, scrubber.policy)
    assert s["decisions"] == {"keep": 1, "repair": 1, "drop": 1}
    assert s["by_check"]["overstates"]["likeliest"][0]["gist"] == "Builds always break."
    assert s["by_check"]["two_points"]["at_cut"] == round(1 / 3, 3)   # flagged, and doesn't decide

    # Its cuts: a sheet of what Jev answered about, two models' drafts, my review of where they disagree.
    folder = tmp_path / "gate-1"
    out = gate_audit.write(pipe, folder, client, read, scrubber.policy, size=4)
    assert out["by_set"] == {"flagged": 2, "not_flagged": 1}
    assert "Bob" in (folder / "sheet.md").read_text()   # the sheet is mine to read: names as said

    def marks(lenient):
        def answer(text):
            past = "always" in text and not lenient
            return {"gist": "past" if past else "ok", "gist_why": "a rule from one case" if past else "as said",
                    "capture": "drop" if "Nobody should" in text else "keep", "capture_why": "mocked"}
        return answer

    for model, lenient in (("claude-opus-5-5", False), ("claude-sonnet-5-5", True)):
        drafter = Drafter(marks(lenient))
        d = gate_audit.draft(pipe, folder, drafter, model, "low", scrubber, 20.0)
        assert d["drafted"] == 3 and "Bob" not in str(drafter.calls)
    r = gate_audit.review(folder)
    assert r["to_review"] == 1
    review = (folder / "review.md").read_text()
    assert "Builds always break." in review and "capture:" not in review   # only the mark they split on
    (folder / "review.md").write_text(re.sub(r"^gist: $", "gist: past", review, flags=re.M))

    s = gate_audit.score(folder)
    assert s["labels"] == {"from_me": 1, "from_drafts": 5, "missing": 0}
    assert (s["repair"]["marked_past_or_unclear"], s["repair"]["auc"], s["repair"]["suggested_cut"]) == (1, 1.0, 0.9)
    assert (s["drop"]["marked_drop"], s["drop"]["auc"], s["drop"]["suggested_cut"]) == (1, 1.0, 0.3)
    assert gate.run(pipe, client, read, scrubber.policy, scrubber, 20.0)["asked"] == 0 and len(sent) == 3
