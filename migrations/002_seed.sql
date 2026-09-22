-- Starter accounts and categories. All editable later in Settings (Phase 7) --
-- nothing here is hard-coded into app logic.

INSERT INTO accounts (name, kind, profile_id, active) VALUES
  ('Scotia Chequing', 'chequing', 'scotia_chequing', 1),
  ('Rogers MC', 'credit', 'rogers_mc', 1);

INSERT INTO categories (name, parent_id, is_system) VALUES ('Housing', NULL, 0);
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Rent', id, 0 FROM categories WHERE name = 'Housing' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Utilities', id, 0 FROM categories WHERE name = 'Housing' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Internet', id, 0 FROM categories WHERE name = 'Housing' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Tenant Insurance', id, 0 FROM categories WHERE name = 'Housing' AND parent_id IS NULL;

INSERT INTO categories (name, parent_id, is_system) VALUES ('Groceries', NULL, 0);
INSERT INTO categories (name, parent_id, is_system) VALUES ('Eating Out', NULL, 0);
INSERT INTO categories (name, parent_id, is_system) VALUES ('Coffee', NULL, 0);

INSERT INTO categories (name, parent_id, is_system) VALUES ('Transportation', NULL, 0);
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Transit', id, 0 FROM categories WHERE name = 'Transportation' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Rideshare', id, 0 FROM categories WHERE name = 'Transportation' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Fuel', id, 0 FROM categories WHERE name = 'Transportation' AND parent_id IS NULL;

INSERT INTO categories (name, parent_id, is_system) VALUES ('Phone', NULL, 0);
INSERT INTO categories (name, parent_id, is_system) VALUES ('Subscriptions', NULL, 0);
INSERT INTO categories (name, parent_id, is_system) VALUES ('Shopping', NULL, 0);

INSERT INTO categories (name, parent_id, is_system) VALUES ('Health & Fitness', NULL, 0);
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Gym', id, 0 FROM categories WHERE name = 'Health & Fitness' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Pharmacy', id, 0 FROM categories WHERE name = 'Health & Fitness' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Supplements', id, 0 FROM categories WHERE name = 'Health & Fitness' AND parent_id IS NULL;

INSERT INTO categories (name, parent_id, is_system) VALUES ('Education', NULL, 0);
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Tuition', id, 0 FROM categories WHERE name = 'Education' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Books', id, 0 FROM categories WHERE name = 'Education' AND parent_id IS NULL;

INSERT INTO categories (name, parent_id, is_system) VALUES ('Entertainment', NULL, 0);
INSERT INTO categories (name, parent_id, is_system) VALUES ('Travel', NULL, 0);
INSERT INTO categories (name, parent_id, is_system) VALUES ('Gifts', NULL, 0);
INSERT INTO categories (name, parent_id, is_system) VALUES ('Personal Care', NULL, 0);
INSERT INTO categories (name, parent_id, is_system) VALUES ('Fees & Interest', NULL, 0);
INSERT INTO categories (name, parent_id, is_system) VALUES ('Other', NULL, 0);

INSERT INTO categories (name, parent_id, is_system) VALUES ('Income', NULL, 0);
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Salary', id, 0 FROM categories WHERE name = 'Income' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Other Income', id, 0 FROM categories WHERE name = 'Income' AND parent_id IS NULL;

INSERT INTO categories (name, parent_id, is_system) VALUES ('Owed to me', NULL, 1);
