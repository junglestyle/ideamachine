"""What code can check about a capture, against the transcript the model was sent (decision 0012).

Run on the model's answer as it came, before names are mapped back: the quote must be in the lines the model saw.
Each check that fails adds a code to the capture's list; an empty list passes. Trials record them; once extraction v5
is live, a capture with any of FAIL is refused and its episode read again on the stronger model.

- The quote is one unbroken stretch of consecutive lines labeled me, copied exactly (only whitespace may differ), and
  those lines are cited. A quote that matches only once case and punctuation are ignored is reported apart, as a
  diagnostic: v4's "word for word" rate counted those.
- At least one cited line is mine.
- Each mishearing's `heard` is in the quote and its `meant` in the reading.
- A pseudonym in the answer is one the transcript had, so it maps back to a name.
- Quote, reading and gist aren't empty.
"""

import re

from im.names import TOKEN

LINE = re.compile(r"^\[(\d+)\] ([^:]+): (.*)$")
FAIL = ("quote_not_in_transcript", "quote_not_mine", "quote_inexact", "quote_lines_not_cited", "cites_none_of_mine",
        "mishearing_not_in_quote", "mishearing_not_in_reading", "unknown_pseudonym", "empty_field")


def lines_of(payload_text: str) -> dict[int, tuple[str, str]]:
    """Line number -> (speaker as sent, text as sent), from a payload's transcript."""
    out = {}
    for row in payload_text.split("\nTranscript:\n", 1)[-1].splitlines():
        m = LINE.match(row)
        if m:
            out[int(m.group(1))] = (m.group(2), m.group(3))
    return out


def _ws(text: str) -> str:
    return " ".join(text.split())


def _fold(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def _runs(lines: dict[int, tuple[str, str]]) -> list[list[int]]:
    """Maximal stretches of consecutive lines labeled me."""
    runs, run = [], []
    for n in sorted(lines):
        if lines[n][0] == "me" and (not run or run[-1] == n - 1):
            run.append(n)
        else:
            if run:
                runs.append(run)
            run = [n] if lines[n][0] == "me" else []
    return runs + ([run] if run else [])


def _locate(quote: str, run: list[int], lines: dict) -> list[int] | None:
    """The lines of `run` the quote spans, if it's in the run's text joined with single spaces."""
    texts = [_ws(lines[n][1]) for n in run]
    joined = " ".join(texts)
    at = joined.find(quote)
    if at < 0:
        return None
    spans, pos = [], 0
    for n, t in zip(run, texts):
        if pos <= at + len(quote) - 1 and at <= pos + len(t) - 1:
            spans.append(n)
        pos += len(t) + 1
    return spans


def check(lines: dict[int, tuple[str, str]], item: dict) -> list[str]:
    """The codes of every check the capture fails, in FAIL's order; [] if it passes."""
    failed = set()
    quote, cited = _ws(item.get("quote", "")), set(item.get("lines", []))
    if not quote or not _ws(item.get("reading", "")) or not _ws(item.get("gist", "")):
        failed.add("empty_field")
    if not any(lines.get(n, ("",))[0] == "me" for n in cited):
        failed.add("cites_none_of_mine")
    if quote:
        found = next((s for s in (_locate(quote, r, lines) for r in _runs(lines)) if s), None)
        if found is not None:
            if not set(found) <= cited:
                failed.add("quote_lines_not_cited")
        else:
            everyone = " ".join(_ws(t) for _, (_, t) in sorted(lines.items()))
            mine = [" ".join(_ws(lines[n][1]) for n in r) for r in _runs(lines)]
            if any(_fold(quote) in _fold(t) for t in mine):
                failed.add("quote_inexact")
            elif quote in everyone or _fold(quote) in _fold(everyone):
                failed.add("quote_not_mine")
            else:
                failed.add("quote_not_in_transcript")
    for m in item.get("mishearings", []):
        if _ws(m.get("heard", "")) not in quote:
            failed.add("mishearing_not_in_quote")
        if _ws(m.get("meant", "")) not in _ws(item.get("reading", "")):
            failed.add("mishearing_not_in_reading")
    sent = set(TOKEN.findall(" ".join(t for _, t in lines.values())))
    said = " ".join([item.get(k, "") for k in ("attributed_to", "reading", "gist")]
                    + [m.get("meant", "") for m in item.get("mishearings", [])])
    if set(TOKEN.findall(said)) - sent:
        failed.add("unknown_pseudonym")
    return [c for c in FAIL if c in failed]
