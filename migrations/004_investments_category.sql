-- Money moved into an account this tool doesn't track (Wealthsimple, RRSP,
-- TFSA, etc.) isn't spending, but also has no "other side" to pair against
-- the way a Scotia->Rogers payment does — so it needs its own category,
-- separate from the generic Transfer type (see spec section 8.1a).
INSERT INTO categories (name, parent_id, is_system) VALUES ('Investments', NULL, 0);
