"""Policy C: names said become stable keyed pseudonyms, and come back."""

from im import names

KEY = b"test key"


def test_a_pseudonym_is_stable_keyed_and_ignores_case():
    t = names.token("Julie", KEY)
    assert t == names.token("julie", KEY) and names.TOKEN.fullmatch(t)
    assert t != names.token("Julie", b"another key") and t != names.token("Julia", KEY)


def test_names_from_every_source_are_replaced_word_by_word_and_mapped_back():
    s = names.Scrubber(KEY, known=["Julie Chen", "Will Park"], mine=["Nathan"], finder=lambda texts: {"Raj"})
    said = ["Julie Chen said Raj and Nathan agree.", "Will you ask Will? Julie's coming.", "I will see."]
    out, back, mine = s.scrub(said)
    julie, chen, raj, will = (names.token(w, KEY) for w in ("Julie", "Chen", "Raj", "Will"))
    assert out[0] == f"{julie} {chen} said {raj} and {mine[0]} agree."
    # "Will" is a common word: left alone where a sentence starts, and never in lower case.
    assert out[1] == f"Will you ask {will}? {julie}'s coming." and out[2] == "I will see."
    assert mine == [names.token("Nathan", KEY)]
    assert [names.unscrub(t, back) for t in out] == said
    assert "ner=" + names.NER_MODEL in s.policy


def test_nothing_known_or_found_leaves_the_text_alone():
    s = names.Scrubber(KEY, finder=lambda texts: {"me", "I"})   # pronouns aren't names
    assert s.scrub(["Tell me what I said."]) == (["Tell me what I said."], {}, [])
    assert "ner=none" in names.Scrubber(KEY).policy


def test_a_long_line_is_read_in_pieces_gliner_can_see_whole():
    long = " ".join(["word"] * 450) + ". Julie said so. " + "Short one."
    pieces = names.chunks(long, size=200)
    assert all(len(p.split()) <= 200 for p in pieces) and " ".join(pieces).split() == long.split()
    assert names.chunks("One. Two three.", size=200) == ["One. Two three."] and names.chunks("   ") == []


def test_names_found_ahead_are_used_without_asking_the_finder():
    asked = []
    s = names.Scrubber(KEY, finder=lambda texts: asked.append(texts) or set())
    out, back, _ = s.scrub(["Raj came."], found={"Raj"})
    assert out == [f"{names.token('Raj', KEY)} came."] and not asked


class CountingFinder:
    """GLiNER's interface: finds capitalized words that aren't the first in the text, and counts what it's asked."""
    id = "fake@0.5"

    def __init__(self):
        self.asked = []

    def per_text(self, texts):
        self.asked += texts
        return [[w.strip("?.,!") for w in t.split()[1:] if w[:1].isupper()] for t in texts]


def test_a_segment_is_read_once_and_kept(pipe, cfg, stream):
    from dataclasses import replace

    from im import egress, trial
    from helpers import run, table

    turns = stream.conversations["c-conv"].turns
    turns[0] = replace(turns[0], text="Did you see the build broke again, Bob?")
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    finder = CountingFinder()
    s = names.Scrubber(KEY, finder=finder)
    eids = trial.sample(pipe)
    w = names.warm(pipe, finder, eids)
    segments = table(pipe, "SELECT count(*) FROM im.segment_names WHERE finder = 'fake@0.5'")[0][0]
    assert w["segments"] == w["newly_read"] == segments == len(finder.asked) > 0
    p = [egress.build(pipe, e, s) for e in eids]
    assert len(finder.asked) == segments                            # every payload came from the cache
    assert any(names.token("Bob", KEY) in x.text and "Bob" not in x.text for x in p)
    assert names.warm(pipe, finder, eids)["newly_read"] == 0


def test_the_backlog_goes_in_chunks_newest_first_until_none_are_left(pipe, cfg, stream):
    from helpers import run, table

    run(pipe, cfg, stream)
    finder = CountingFinder()
    total = table(pipe, """SELECT count(DISTINCT es.segment_id) FROM im.episode_segments es
                           JOIN im.episodes e USING (episode_id) WHERE e.current""")[0][0]
    first = names.pending(pipe, finder, 2)
    assert first == {"items": 2, "more": True}
    chunks, more = 1, True
    while more:
        r = names.pending(pipe, finder, 4)
        chunks, more = chunks + 1, r["more"]
    assert len(finder.asked) == total and names.pending(pipe, finder, 4) == {"items": 0, "more": False}
