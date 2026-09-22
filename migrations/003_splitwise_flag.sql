-- Splitwise-tracked shared expenses (spec section 8.2a): a split line can be
-- marked as fronted for others via Splitwise, independent of person_id, since
-- Splitwise may track a whole group rather than one named person.
ALTER TABLE splits ADD COLUMN is_splitwise INTEGER NOT NULL DEFAULT 0;
