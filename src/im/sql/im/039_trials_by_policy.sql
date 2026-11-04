-- Trials compare models (decision 0008), not prompts against my verdicts: every model reads the same sample of
-- episodes, under a privacy policy that can differ from the live one (C pseudonymizes names said), and is scored
-- against a reference model's captures. A read under one policy isn't a read under another.
ALTER TABLE im.prompt_trials
  ADD COLUMN privacy_policy_version text NOT NULL
    DEFAULT 'B1;speakers-pseudonymized;spoken-names-not-scrubbed;no-registry';
ALTER TABLE im.prompt_trials ALTER COLUMN privacy_policy_version DROP DEFAULT;
ALTER TABLE im.prompt_trials DROP CONSTRAINT prompt_trials_pkey;
ALTER TABLE im.prompt_trials ADD PRIMARY KEY (episode_id, prompt_version, model, privacy_policy_version);
