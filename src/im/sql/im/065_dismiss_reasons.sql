-- Why I dismissed a card, when I say (the web view; `im feedback --reasons` counts them): an idea_dismiss's payload
-- may carry a reason (feedback.DismissReason). not_a_thought: not a qualifying thought; misrepresents: it
-- misrepresents what I said; duplicate: it duplicates another card, and "of" may name which; not_for_me: valid, but
-- I don't want it surfaced. Without a reason a dismissal means only that I dismissed it: why is unknown.
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_dismiss_reason CHECK (
  kind <> 'idea_dismiss' OR (
    (payload->>'reason' IS NULL OR payload->>'reason' IN ('not_a_thought', 'misrepresents', 'duplicate', 'not_for_me'))
    AND (payload->>'of' IS NULL OR (
      payload->>'reason' = 'duplicate'
      AND payload->>'of' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      AND payload->>'of' <> idea_id::text))));
