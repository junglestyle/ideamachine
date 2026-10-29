-- An open proposal leaves Lattice's list as soon as I keep or dismiss it, not at the next run (which settles it).
CREATE OR REPLACE VIEW pub.proposals AS
SELECT p.proposal_id, p.idea_id, x.title, x.statement, p.weight, p.made_at
FROM im.proposals p
JOIN im.idea_text x USING (idea_id)
JOIN im.idea_kept k USING (idea_id)
WHERE p.outcome IS NULL AND k.kept_as IS NULL
  AND NOT EXISTS (SELECT 1 FROM pub.feedback_events f
                  WHERE f.idea_id = p.idea_id AND f.kind = 'idea_dismiss' AND f.at > p.made_at);
