"""Capture eligibility: a random sample of live captures, labeled by two models under a frozen rubric, the items
they split on (and some they agree on) marked by me blind, and model agreement scored apart from my validation."""

import json
import re
from pathlib import Path

import pytest

from im import egress, eligibility_audit as ea, extract, fixtures, llm

from helpers import run, table
from test_archive_captures import archive_capture
from test_extract import FakeClaude

TERRA, CARBON = "openai/gpt-6.1-terra", "google/gemini-4-carbon"
SMALL = {"Great, thanks.", "Decided then.", "Agreed.", "Okay, that's it.", "Add a task for that."}


def every_line_of_mine(text):
    """An extractor capturing each line I said, its gist the line itself."""
    out = []
    for row in text.splitlines():
        if m := re.match(r"^\[(\d+)\] me: (.*)$", row):
            out.append({"quote": m.group(2), "lines": [int(m.group(1))], "attributed_to": "",
                        "reading": m.group(2), "mishearings": [], "gist": m.group(2)})
    return out


class Labeler:
    """A model reached through OpenRouter, labeling each item by `label(gist)`, with a reason naming the model."""

    def __init__(self, model, label):
        self.model, self.label, self.calls = model, label, []

    def complete(self, model, effort, system, schema, text, max_tokens, max_price=None):
        self.calls.append({"system": system, "text": text})
        gist = re.search(r"^Gist: (.*)$", text, re.M).group(1)
        answer = {"label": self.label(gist), "why": f"reason-from-{self.model}"}
        return llm.Reply(json.dumps(answer), "end_turn", model, "req", 300, 20, 0.001)


def terra(gist):
    return "ineligible" if gist in SMALL else "eligible"


def carbon(gist):   # splits from Terra on two items, one of them by saying uncertain
    split = {"Decided then.": "eligible", "Thinking out loud about the garden thing.": "uncertain"}
    return split.get(gist, terra(gist))


@pytest.fixture
def sheet(pipe, cfg, stream, tmp_path):
    """A sheet of every live capture, with one archive capture under the same read left out; and the read."""
    run(pipe, cfg, stream)
    read = (extract.run_stage(pipe, FakeClaude(every_line_of_mine))["prompt_version"], "claude-opus-5-5")
    typed = archive_capture(pipe, fixtures.T0)
    pipe.execute("UPDATE im.items SET prompt_version = %s, privacy_policy_version = %s WHERE item_id = %s",
                 (read[0], egress.POLICY_VERSION, typed))
    folder = tmp_path / "eligibility-1"
    sent = table(pipe, "SELECT count(*) FROM im.egress_log")
    out = ea.write(pipe, folder, read, size=1000)
    assert table(pipe, "SELECT count(*) FROM im.egress_log") == sent   # the sheet sends nothing
    return folder, out


def drafted(pipe, folder):
    labelers = {TERRA: Labeler(TERRA, terra), CARBON: Labeler(CARBON, carbon)}
    for model, labeler in labelers.items():
        ea.draft(pipe, labeler, folder, model)
    return labelers


def gists(folder) -> dict[str, str]:
    return {n: re.search(r"^Gist: (.*)$", body, re.M).group(1)
            for n, body in ea._sections((folder / "sheet.md").read_text()).items()}


def mark(folder: Path, marks: dict[str, str]) -> None:
    text = (folder / "review.md").read_text()
    for n, label in marks.items():
        text = re.sub(rf"(^## {n}\n(?:.*\n)*?)mark: ", rf"\g<1>mark: {label}", text, count=1, flags=re.M)
    (folder / "review.md").write_text(text)


def test_the_sheet_keys_a_random_sample_of_live_captures_under_the_rubric(pipe, sheet):
    folder, out = sheet
    key = json.loads((folder / "key.json").read_text())
    live = table(pipe, "SELECT count(*) FROM im.items WHERE episode_id IS NOT NULL")[0][0]
    assert out["items"] == len(key["items"]) == live and key["rubric"] == ea.rubric() and key["seed"] == 10
    assert key["population"]["archive_skipped"] == 1
    assert set(key["sample"]["by_kind"]) == {"monologue", "conversation"}
    assert sum(key["sample"]["by_route"].values()) == live
    text = (folder / "sheet.md").read_text()
    assert ea.rubric()["sha256"][:12] in text
    body = text.split("\n## 1\n", 1)[1]
    assert "Around it:" in body and "Cited lines:" in body and "Quote: " in body and "Gist: " in body
    assert not re.search(r"\b(eligible|ineligible|uncertain|score|0\.\d)\b", body)
    for item in key["items"].values():
        assert item["item_id"] and item["read_sha256"] and item["shown_sha256"]


def test_drafts_are_independent_and_review_is_blind_with_audited_agreed_items(pipe, sheet):
    folder, _ = sheet
    with pytest.raises(SystemExit):   # Opus wrote the captures
        ea.draft(pipe, Labeler("claude-sonnet-5-5", terra), folder, "claude-sonnet-5-5")
    labelers = drafted(pipe, folder)
    shown = {n: body.strip() for n, body in ea._sections((folder / "sheet.md").read_text()).items()}
    for labeler in labelers.values():
        assert len(labeler.calls) == len(shown)
        for call in labeler.calls:   # the rubric and one item as the sheet shows it, nothing else
            assert call["system"] == ea.DRAFT_SYSTEM and ea.RUBRIC in call["system"]
            assert call["text"] in shown.values() and "reason-from" not in call["text"]
    again = ea.draft(pipe, labelers[TERRA], folder, TERRA)
    assert again["asked"] == 0 and len(labelers[TERRA].calls) == len(shown)   # resumable: nothing asked twice

    out = ea.review(folder, audited=3)
    g = gists(folder)
    disputed = {n for n in g if g[n] in ("Decided then.", "Thinking out loud about the garden thing.")}
    rkey = json.loads((folder / "review-key.json").read_text())
    assert set(rkey["disputed"]) == disputed and len(rkey["audited"]) == 3 and not disputed & set(rkey["audited"])
    assert (out["disputed"], out["audited"], out["listed"]) == (2, 3, 5)
    text = (folder / "review.md").read_text()
    sections = ea._sections(text)
    assert set(sections) == disputed | set(rkey["audited"])
    for n, body in sections.items():   # each item as on the sheet, then blanks to mark: nothing tells them apart
        assert body.strip() == shown[n] + "\n\nmark: \nnote:"
    assert not re.search(r"reason-from|terra|carbon|openai|google", text, re.I)

    mark(folder, {n: "i" for n in disputed})
    marked = (folder / "review.md").read_text()
    assert ea.review(folder)["marked"] == 2 and (folder / "review.md").read_text() == marked   # read back, kept


def test_a_folder_refuses_another_rubric(pipe, sheet, monkeypatch):
    folder, _ = sheet
    drafted(pipe, folder)
    monkeypatch.setattr(ea, "RUBRIC", ea.RUBRIC + "\n7. A new tie-break.")
    for step in (lambda: ea.draft(pipe, Labeler(TERRA, terra), folder, TERRA), lambda: ea.review(folder),
                 lambda: ea.score(folder)):
        with pytest.raises(SystemExit, match="rubric"):
            step()


def test_the_rubric_doc_is_the_rubric():
    doc = (Path(__file__).parent.parent / "docs/design/eligibility-audit.md").read_text()
    assert ea.RUBRIC in doc and ea.rubric()["sha256"] in doc


def test_score_keeps_model_agreement_apart_from_my_validation(pipe, sheet):
    folder, _ = sheet
    drafted(pipe, folder)
    before = ea.score(folder)
    a = before["a_model_agreement"]
    n = len(gists(folder))
    assert (a["N"], a["agreed"], a["raw"]) == (n, n - 2, round((n - 2) / n, 3)) and 0 < a["kappa"] < 1
    assert before["models"] == [CARBON, TERRA]   # in the drafts' file order
    assert a["confusion"][f"{CARBON}=eligible"][f"{TERRA}=ineligible"] == 1
    assert a["confusion"][f"{CARBON}=uncertain"][f"{TERRA}=eligible"] == 1
    assert before["b_human_validation"].startswith("no review yet")

    ea.review(folder, audited=3)
    g, rkey = gists(folder), json.loads((folder / "review-key.json").read_text())
    flipped, *kept = rkey["audited"]
    mine = {n: "ineligible" if g[n] == "Decided then." else "e" for n in rkey["disputed"]}
    mine |= {flipped: "eligible" if terra(g[flipped]) == "ineligible" else "ineligible"}
    mine |= {k: terra(g[k]) for k in kept}
    mark(folder, mine)
    after = ea.score(folder)
    assert after["a_model_agreement"] == a   # my marks never move the models' agreement
    b = after["b_human_validation"]
    assert b["N_marked"] == 5
    assert b["accuracy_against_my_marks"][TERRA] == {"N": 5, "correct": 4, "accuracy": 0.8}
    assert b["accuracy_against_my_marks"][CARBON] == {"N": 5, "correct": 2, "accuracy": 0.4}
    assert b["agreed_label_held"] == {"N_audited": 3, "N_marked": 3, "held": 2, "rate": 0.667}
    c = after["c_ineligible_rate"]
    final = {k: terra(v) for k, v in g.items()} | {k: ea.MARKS[v] for k, v in mine.items()}
    bad = sum(v == "ineligible" for v in final.values())
    assert (c["N_items"], c["from_my_marks"], c["from_agreed_labels"], c["unresolved"]) == (n, 5, n - 5, [])
    assert (c["ineligible"], c["eligible"], c["uncertain"], c["N"]) == (bad, n - bad, 0, n)
    assert c["rate"] == round(bad / n, 3) and c["ci95"][0] < c["rate"] < c["ci95"][1]
