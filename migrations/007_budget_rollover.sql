-- Budgets (spec section 6): monthly_budget already exists on categories.
-- This adds an opt-in per-category flag for carrying unused budget forward
-- one month (see core/budgets.py for exactly how that's computed).
ALTER TABLE categories ADD COLUMN budget_rollover INTEGER NOT NULL DEFAULT 0;
