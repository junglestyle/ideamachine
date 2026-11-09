-- Jev's answers, kept so a question asked again about the same state isn't sent again: the same-point check between
-- trial captures (im.trial), and later the capture gate. A cache: derived, safe to empty, and holding nothing that
-- was said, only a hash of the text the state was built from (before names were pseudonymized) and the
-- probabilities. `question_set` is the version of the questions asked (their wording, and the model).

CREATE TABLE im.jev_answers (
  question_set  text NOT NULL,
  key_sha256    text NOT NULL,
  answers       jsonb NOT NULL,      -- question name -> probability of yes
  egress_id     bigint NULL REFERENCES im.egress_log(egress_id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (question_set, key_sha256)
);
