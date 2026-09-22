CREATE TABLE accounts (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  kind            TEXT NOT NULL CHECK (kind IN ('chequing','savings','credit','other')),
  profile_id      TEXT NOT NULL,
  active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE import_batches (
  id              INTEGER PRIMARY KEY,
  account_id      INTEGER NOT NULL REFERENCES accounts(id),
  file_name       TEXT NOT NULL,
  imported_at     TEXT NOT NULL,
  date_from       TEXT,
  date_to         TEXT,
  rows_total      INTEGER,
  rows_new        INTEGER,
  rows_duplicate  INTEGER
);

CREATE TABLE categories (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL,
  parent_id       INTEGER REFERENCES categories(id),
  monthly_budget  INTEGER,
  is_system       INTEGER NOT NULL DEFAULT 0,
  active          INTEGER NOT NULL DEFAULT 1,
  UNIQUE (name, parent_id)
);

CREATE TABLE people (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE rules (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL,
  priority        INTEGER NOT NULL DEFAULT 100,
  enabled         INTEGER NOT NULL DEFAULT 1,
  conditions      TEXT NOT NULL,
  actions         TEXT NOT NULL,
  stop_processing INTEGER NOT NULL DEFAULT 0,
  times_applied   INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL
);

-- Raw import data (txn_date, description, amount, raw_row, fingerprint) is
-- immutable. Everything below the comment is the user/rule layer.
CREATE TABLE transactions (
  id              INTEGER PRIMARY KEY,
  account_id      INTEGER NOT NULL REFERENCES accounts(id),
  batch_id        INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
  txn_date        TEXT NOT NULL,
  posted_date     TEXT,
  description     TEXT NOT NULL,
  amount          INTEGER NOT NULL,
  currency        TEXT NOT NULL DEFAULT 'CAD',
  raw_row         TEXT NOT NULL,
  fingerprint     TEXT NOT NULL UNIQUE,
  -- user/rule layer:
  type            TEXT NOT NULL DEFAULT 'unreviewed'
                  CHECK (type IN ('unreviewed','expense','income','transfer',
                                  'reimbursement','refund','ignore')),
  type_locked     INTEGER NOT NULL DEFAULT 0,
  note            TEXT,
  reviewed        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE splits (
  id              INTEGER PRIMARY KEY,
  transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  amount          INTEGER NOT NULL,
  category_id     INTEGER REFERENCES categories(id),
  person_id       INTEGER REFERENCES people(id),
  note            TEXT,
  locked          INTEGER NOT NULL DEFAULT 0,
  created_by_rule INTEGER REFERENCES rules(id)
);

CREATE TABLE tags (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE
);

CREATE TABLE split_tags (
  split_id        INTEGER NOT NULL REFERENCES splits(id) ON DELETE CASCADE,
  tag_id          INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (split_id, tag_id)
);

CREATE TABLE links (
  id              INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL CHECK (kind IN ('transfer_pair','reimburses','refund_of')),
  from_txn_id     INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  to_txn_id       INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
  to_split_id     INTEGER REFERENCES splits(id) ON DELETE CASCADE,
  amount          INTEGER,
  auto            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE settings (
  key             TEXT PRIMARY KEY,
  value           TEXT NOT NULL
);

CREATE INDEX idx_transactions_account_date ON transactions(account_id, txn_date);
CREATE INDEX idx_transactions_type ON transactions(type);
CREATE INDEX idx_splits_transaction ON splits(transaction_id);
CREATE INDEX idx_import_batches_account ON import_batches(account_id);
