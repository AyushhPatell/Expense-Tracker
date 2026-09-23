-- Owed-to-Me balances (spec 8.3 / core/receivables.py) need two things a
-- plain running total can't express on its own:
-- 1. "Settled up through here" — money already reconciled by some other
--    means (e.g. netted inside the Splitwise app itself, never a literal
--    bank transfer this tool would see) shouldn't keep inflating the
--    all-time balance forever.
-- 2. "Flag this for later" — a specific entry I'm not sure about yet,
--    independent of settling.
ALTER TABLE splits ADD COLUMN settled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE splits ADD COLUMN flagged INTEGER NOT NULL DEFAULT 0;
