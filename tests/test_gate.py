"""The capture gate: Jev's checks on a trial's captures, shown the transcript as sent, decided per check in code."""

from dataclasses import replace

from im import egress, extract, gate, jev, names, trial

from helpers import run
from test_extract import FakeClaude


def judge(by_gist):
    """Jev answering 0.9 to the checks `by_gist` names for a capture's gist, 0.1 to the rest."""
    sent = []

    def post(body):
        sent.append(body)
        yes = by_gist.get(body["state"]["gist"], ())
        return {"model": "jev-1.14.0", "usage": {"input_tokens": 500, "output_tokens": 7},
                "answers": {q: {"type": "noul", "noul": 0.9 if q in yes else 0.1} for q in body["questions"]}}
    return jev.Jev(post=post), sent


def test_the_gate_keeps_repairs_or_drops_each_capture_from_what_jev_says_of_it(pipe, cfg, stream):
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
    assert gate.run(pipe, client, read, scrubber.policy, scrubber, 20.0)["asked"] == 0 and len(sent) == 3
