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
