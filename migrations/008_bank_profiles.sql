-- Self-service bank profiles (Settings > 'Add a bank profile'): the same
-- shape as the built-in YAML profiles in core/importers/profiles/, but
-- created through the UI and stored locally on this device rather than as
-- a file in the repo.
CREATE TABLE bank_profiles (
  id            TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  config        TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
