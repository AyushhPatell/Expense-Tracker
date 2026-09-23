INSERT INTO categories (name, parent_id, is_system)
  SELECT 'Laundry', id, 0 FROM categories WHERE name = 'Housing' AND parent_id IS NULL;
