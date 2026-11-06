"""A capture cites every line its quote is on, so forgetting any of them reaches it and what was made from it."""

from im import extract, lattice, observations, quotes

from helpers import run, table
from test_extract import FakeClaude
from test_lattice import FakeMatcher, HashEmbedder, _star_the_lora_capture, seeded  # noqa: F401 (fixture)

LINES = [("s1", "So I was saying, uh, the garden thing."),
         ("s2", "the garden sensors could report soil moisture over LoRa."),
         ("s3", "Battery life is the hard part, maybe a solar trickle charger."),
         ("s4", "Anyway, the hard part is the weekend.")]


def test_a_quote_cites_the_lines_its_words_are_on_and_never_fewer():
    # Running into the next line, and one line off: the citation grows to where the words are, keeping its own.
    _, segs, share = quotes.place("over LoRa. Battery life is the hard part", LINES[1:4], ["s2"])
    assert (segs, share) == (["s2", "s3"], 1.0)
    _, segs, share = quotes.place("soil moisture over LoRa", LINES[2:4], ["s3"])
    assert segs == ["s3"] and share < quotes.FLAG     # not on the line either side: left as cited, and flagged
    _, segs, share = quotes.place("soil moisture over LoRa", LINES[1:4], ["s3"])
    assert (segs, share) == (["s2", "s3"], 1.0)
    # A tidied quote stays on its line: one word it shares with a neighbour ("the") doesn't put it there.
    _, segs, share = quotes.place("Battery life is hard, a solar charger", LINES[1:4], ["s3"])
    assert segs == ["s3"] and share == 1.0
    # The transcript's own line label, copied into the quote, goes; stitched pieces are placed one by one.
    text, segs, _ = quotes.place("[3] me: Battery life is the hard part ... report soil moisture", LINES[1:4], ["s3"])
    assert text == "Battery life is the hard part ... report soil moisture" and segs == ["s2", "s3"]
    _, segs, share = quotes.place("Nothing like this was said at all", LINES[1:4], ["s2"])
    assert segs == ["s2"] and share < quotes.FLAG


def test_forgetting_a_line_a_capture_quoted_without_citing_reaches_it_and_what_was_made_from_it(seeded, cfg,
                                                                                                    stream):
    def respond(text):   # the LoRa line and the start of the next one, citing only the LoRa line
        (n,) = [int(line[1:].split("]")[0]) for line in text.splitlines() if "LoRa" in line]
        return [{"kind": "idea", "said_by": "me", "quote": "over LoRa. Battery life is the hard part", "lines": [n],
                 "gist": "Garden sensors over LoRa; battery is hard", "themes": [], "confidence": 0.9}]

    run(seeded, cfg, stream)
    extract.run_stage(seeded, FakeClaude(respond))
    observations.assign_stage(seeded)
    lattice.match_stage(seeded, FakeMatcher(), HashEmbedder())
    item = _star_the_lora_capture(seeded)
    extract.decide(seeded, item, "keep")
    # Captures made before the stage existed are placed by the first run after it: everything made from them grows.
    assert quotes.place_stage(seeded) == {"checked": 1, "unlabelled": 0, "cited_more": 1, "flagged": 0}
    battery = table(seeded, "SELECT segment_id FROM im.source_segments WHERE text LIKE 'Battery%%'")[0][0]
    assert table(seeded, "SELECT %s = ANY(source_segment_ids), quote_in_lines FROM im.items", battery) == [(True, 1.0)]
    assert quotes.place_stage(seeded)["checked"] == 0   # placed once

    stream.forget(3613, 3628)   # the battery line: not cited by the model, but quoted
    stream.write(stream.dir)
    run(seeded, cfg, stream)
    assert not table(seeded, "SELECT 1 FROM im.items")
    assert not table(seeded, "SELECT 1 FROM im.ideas WHERE statement = 'A new thing.'")
    assert not table(seeded, "SELECT 1 FROM im.item_verdicts")
    assert not table(seeded, "SELECT 1 FROM pub.feedback_events WHERE kind = 'item_star'")
    assert not table(seeded, "SELECT 1 FROM im.observations WHERE source = 'speech'")
    assert not table(seeded, "SELECT 1 FROM im.egress_log WHERE payload LIKE '%%Battery life%%'")   # sent, not kept
