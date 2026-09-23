# Personal Expense Tracker — Build Specification

**Owner:** Ayush
**Stack:** Python + Streamlit + SQLite (fully local)
**Status:** Ready to build (Phase 1)

---

## 0. Instructions for the AI building this

Read this whole document before writing code. Then follow these working rules:

1. **Privacy is a hard requirement, not a feature.** Nothing in this app may send data off the machine. No cloud APIs, no telemetry, no analytics, no CDN-loaded assets, no bank-login aggregators (Plaid, Flinks, etc.). The only permitted network use is installing packages during setup, and the optional local Ollama integration on `localhost` (Phase 7).
2. **Never ask me to paste real bank data.** Use fake fixture CSVs for development and tests. When you need a bank's CSV format, ask me for the **header row plus one made-up example row** for a purchase and a payment.
3. **Build in phases (Section 12).** Finish one phase, confirm it works with me, then move on. Don't scaffold every feature at once.
4. **I'm strong on concepts and data, not a from-scratch developer.** Keep the code readable, explain key decisions briefly, and give me exact commands to run.
5. **Raw bank data is immutable.** All my adjustments live in separate tables (Section 5). Never overwrite imported transaction fields.
6. **Everything must be configurable.** Categories, rules, people, bank profiles, and month definition live in the database or config files, never hard-coded. Customization is the main reason I'm building this myself.

---

## 1. What this tool is

A private, local web app (running at `http://127.0.0.1:8501`) where I:

1. Upload CSV exports from any of my accounts (Scotiabank chequing, Rogers Mastercard, and more later) through **one upload page and one process**.
2. Get every transaction normalized into a single table, deduplicated, auto-categorized by my rules, with credit card payments and internal transfers detected so nothing is double counted.
3. Review and adjust anything: change category, change type, split a transaction into parts, mark part of it as owed by someone, add notes and tags.
4. See accurate monthly spending by category, budgets vs actual, trends, and who owes me money.

### My accounts and why this is tricky

- **Scotiabank chequing** is my main account. Paycheques come in here; bill payments (including paying Rogers) go out from here. Statement periods run mid-month to mid-month (e.g., Aug 18 – Sep 17).
- **Rogers Mastercard** is where I make almost all purchases. Its statement periods are different (e.g., Jul 3 – Aug 4).
- Paying Rogers from Scotia must **not** count as an expense; the purchases were already counted on the Rogers side.
- I often pay shared costs in full and get paid back. Example: I pay full rent of $1,053 by Interac e-transfer, my share is $351, and two roommates each e-transfer me $351. My rent expense must show $351, and their transfers must not show as income. Rent is only one example; this pattern happens with groceries, dinners, household items, and so on.

---

## 2. Core principles

| Principle | Meaning |
|---|---|
| Local-only | Data never leaves the machine (Section 10). |
| Transactions, not statements | Statement periods are irrelevant. Each transaction's own date decides which month it belongs to. |
| One pipeline | Every file from every account goes through the same import → normalize → dedupe → rules → transfer-matching → review flow. |
| Raw data is immutable | Imported rows are never edited. Adjustments are layered on top. |
| Manual beats automatic | Anything I set by hand is locked and never overwritten by rules on re-run. |
| Trustworthy numbers | The app warns when a month's data is incomplete for any account. |
| Learns from me | Manual fixes can be turned into rules in one click. |

---

## 3. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| UI | Streamlit | Multi-page app; use `st.data_editor` for editable grids. |
| Database | SQLite (stdlib `sqlite3`) | Single file, stored **outside** the repo. Enable `PRAGMA foreign_keys = ON`. |
| Data handling | pandas | CSV parsing and report aggregation. |
| Charts | Altair (bundled with Streamlit) or Plotly | Must render offline; no CDN. |
| Bank profiles | YAML (`pyyaml`) | One file per bank/account format. |
| Tests | pytest | Fixture CSVs with fake data only. |
| Env | `venv` or `uv` | Pin versions in `requirements.txt`. |
| Optional (Phase 7) | Ollama on localhost | Local LLM for category suggestions. |

Migrations: a simple versioned folder of `.sql` files applied in order, tracked in a `schema_version` table. No heavy ORM needed; a small repository layer in `core/db.py` is enough.

---

## 4. Project structure

```
expense-tracker/
├── app/
│   ├── Home.py                  # Overview: current month summary, warnings, quick links
│   └── pages/
│       ├── 1_Import.py
│       ├── 2_Review.py
│       ├── 3_Dashboard.py
│       ├── 4_Owed_To_Me.py
│       ├── 5_Rules.py
│       ├── 6_Budgets.py
│       └── 7_Settings.py        # accounts, categories, people, month definition, backup
├── core/
│   ├── config.py                # data dir path, settings loading
│   ├── db.py                    # connection, migrations, queries
│   ├── importers/
│   │   ├── profiles/            # scotia_chequing.yaml, rogers_mc.yaml, ...
│   │   ├── detect.py            # identify profile from header row
│   │   ├── parse.py             # read CSV using a profile
│   │   └── normalize.py         # to the canonical schema + sign convention
│   ├── fingerprint.py           # dedupe hashing
│   ├── rules.py                 # rule engine
│   ├── transfers.py             # transfer pair detection
│   ├── splits.py                # split creation/validation, split templates
│   ├── receivables.py           # owed-to-me balances, reimbursement linking
│   ├── coverage.py              # imported date ranges and gaps per account
│   └── reports.py               # monthly aggregation, budgets, trends
├── migrations/
│   └── 001_initial.sql
├── tests/
│   ├── fixtures/                # FAKE CSVs only
│   └── test_*.py
├── .streamlit/config.toml
├── .gitignore
├── requirements.txt
└── README.md
```

**Data directory:** default `~/ExpenseTrackerData/` (overridable with env var `EXPENSE_TRACKER_DATA`). Contains `tracker.db` and `backups/`. The repo must never contain real data.

---

## 5. Data model

### 5.1 Canonical sign convention

Across the whole app: **negative = money leaving me, positive = money coming to me**, from the perspective of that account.

- Scotia purchase or bill payment: negative.
- Scotia paycheque: positive.
- Rogers purchase: negative (even if Rogers exports it as positive; the profile handles inversion).
- Rogers payment received: positive.

### 5.2 Schema (initial migration)

```sql
CREATE TABLE accounts (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,         -- "Scotia Chequing", "Rogers MC"
  kind            TEXT NOT NULL CHECK (kind IN ('chequing','savings','credit','other')),
  profile_id      TEXT NOT NULL,                -- matches YAML profile filename
  active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE import_batches (
  id              INTEGER PRIMARY KEY,
  account_id      INTEGER NOT NULL REFERENCES accounts(id),
  file_name       TEXT NOT NULL,
  imported_at     TEXT NOT NULL,                -- ISO timestamp
  date_from       TEXT,                         -- earliest txn date in file
  date_to         TEXT,                         -- latest txn date in file
  rows_total      INTEGER,
  rows_new        INTEGER,
  rows_duplicate  INTEGER
);

CREATE TABLE transactions (
  id              INTEGER PRIMARY KEY,
  account_id      INTEGER NOT NULL REFERENCES accounts(id),
  batch_id        INTEGER NOT NULL REFERENCES import_batches(id),
  txn_date        TEXT NOT NULL,                -- ISO date; transaction date if bank provides it
  posted_date     TEXT,                         -- optional
  description     TEXT NOT NULL,                -- raw bank description, untouched
  amount          INTEGER NOT NULL,             -- in CENTS, canonical sign
  currency        TEXT NOT NULL DEFAULT 'CAD',
  raw_row         TEXT NOT NULL,                -- original CSV row as JSON, for audit
  fingerprint     TEXT NOT NULL UNIQUE,
  -- user/rule layer (NOT raw data):
  type            TEXT NOT NULL DEFAULT 'unreviewed'
                  CHECK (type IN ('unreviewed','expense','income','transfer',
                                  'reimbursement','refund','ignore')),
  type_locked     INTEGER NOT NULL DEFAULT 0,   -- 1 = set manually, rules must not change
  note            TEXT,
  reviewed        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE categories (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL,
  parent_id       INTEGER REFERENCES categories(id),
  monthly_budget  INTEGER,                      -- cents, nullable
  is_system       INTEGER NOT NULL DEFAULT 0,   -- e.g. "Owed to me"
  active          INTEGER NOT NULL DEFAULT 1,
  UNIQUE (name, parent_id)
);

CREATE TABLE people (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE splits (
  id              INTEGER PRIMARY KEY,
  transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  amount          INTEGER NOT NULL,             -- cents, same sign as parent
  category_id     INTEGER REFERENCES categories(id),
  person_id       INTEGER REFERENCES people(id),-- set = this portion is owed by that named person
  is_splitwise    INTEGER NOT NULL DEFAULT 0,   -- 1 = this portion is fronted for others via
                                                 -- Splitwise (receivable, not my spending);
                                                 -- independent of person_id (see 8.2a)
  note            TEXT,
  locked          INTEGER NOT NULL DEFAULT 0,   -- 1 = manual, rules must not change
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
  to_split_id     INTEGER REFERENCES splits(id) ON DELETE CASCADE, -- for reimburses
  amount          INTEGER,                      -- cents; allows partial reimbursement
  auto            INTEGER NOT NULL DEFAULT 1    -- 0 = linked manually
);

CREATE TABLE rules (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL,
  priority        INTEGER NOT NULL DEFAULT 100, -- lower runs first
  enabled         INTEGER NOT NULL DEFAULT 1,
  conditions      TEXT NOT NULL,                -- JSON, see Section 7
  actions         TEXT NOT NULL,                -- JSON, see Section 7
  stop_processing INTEGER NOT NULL DEFAULT 0,
  times_applied   INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL
);

CREATE TABLE settings (
  key             TEXT PRIMARY KEY,
  value           TEXT NOT NULL                 -- JSON
);
```

**Money is stored as integer cents** to avoid floating-point errors. Convert only for display.

### 5.3 Invariants (enforce in code, test them)

- The sum of a transaction's splits must equal the transaction amount. If a transaction has no splits, treat it as one implicit split with no category.
- Splits with `person_id` set, or `is_splitwise = 1`, are **receivables**, not my spending. The two are independent: a Splitwise line doesn't need a named person (Splitwise may track a whole group), and a named-person line doesn't need `is_splitwise` (direct arrangements like the rent example, settled by e-transfer).
- `type = transfer` transactions never appear in spending or income totals.
- `type = reimbursement` transactions are never income; they reduce what people owe me.
- `type = refund` transactions reduce spending in the category of the original purchase (or the category I assign).

---

## 6. Import pipeline

One upload page accepts one or many CSV files at once. For each file:

### 6.1 Detect profile
Read the header row and match it against every YAML profile's `detect.headers`. If exactly one matches, use it. If none or several match, ask me to pick the account/profile. If a profile is shared by multiple accounts (e.g., two Scotia accounts with the same format), ask which account.

### 6.2 Profile format (YAML)

The exact column names are **unknown until I provide them**. Build the profile system so the formats below are just config. Example shape:

```yaml
id: rogers_mc
display_name: Rogers Mastercard
detect:
  headers: ["Date", "Posted Date", "Description", "Amount"]   # placeholder
parse:
  encoding: utf-8-sig
  skip_rows: 0
  date_column: Date
  posted_date_column: Posted Date          # optional
  date_format: "%Y-%m-%d"                  # placeholder
  description_columns: [Description]       # concatenated with " | " if several
  amount:
    mode: single                           # single | debit_credit
    column: Amount
    invert: true                           # purchases exported positive -> make negative
    # for debit_credit mode:
    # debit_column: Withdrawal
    # credit_column: Deposit
  currency_column: null
  skip_if:
    - column: Status
      equals: Pending                      # never import pending transactions
```

Handle robustly: currency symbols and commas in amounts, parentheses for negatives, blank lines, trailing summary rows, BOM, and different date formats. Store the original row in `raw_row`.

### 6.3 Fingerprint and dedupe
`fingerprint = sha256(account_id | txn_date | amount_cents | normalized_description | occurrence_index)`

- `normalized_description`: uppercase, collapse whitespace, trim.
- `occurrence_index`: the 1st, 2nd, 3rd... identical (date, amount, description) row **within the same file**. This keeps two genuine $3.50 coffees on the same day, while still skipping them when an overlapping file is imported later.
- Rows whose fingerprint already exists are skipped and counted as duplicates.

### 6.4 Post-import steps (in order)
1. Insert new transactions as `unreviewed`.
2. Run the rules engine on new transactions (Section 7).
3. Run transfer detection (Section 8.1).
4. Update coverage (Section 9.2).
5. Show a summary: rows total / new / duplicates / auto-categorized / needs review, plus a link to the Review page.

### 6.5 Undo an import
Settings or Import page: delete an entire batch (with confirmation). Cascades to its transactions, splits, and links.

---

## 7. Rules engine

Rules are how the app gets smarter. They are ordered by `priority`.

### 7.1 Conditions (JSON, all must match)

```json
{
  "all": [
    {"field": "description", "op": "contains", "value": "ROGERS"},
    {"field": "description", "op": "contains", "value": "PAYMENT"},
    {"field": "account", "op": "equals", "value": "Scotia Chequing"},
    {"field": "direction", "op": "equals", "value": "out"},
    {"field": "amount_abs", "op": "between", "value": [1040, 1070]},
    {"field": "day_of_month", "op": "between", "value": [25, 31]}
  ]
}
```

Supported fields: `description`, `account`, `account_kind`, `direction` (in/out), `amount_abs` (dollars in the UI, cents internally), `day_of_month`, `weekday`.
Supported ops: `contains`, `not_contains`, `equals`, `starts_with`, `regex`, `between`, `gt`, `lt`, `in`. Text comparisons are case-insensitive. Support `any` groups in addition to `all`.

### 7.2 Actions (JSON)

```json
{
  "set_type": "expense",
  "set_category": "Housing > Rent",
  "split_template": [
    {"share": "fixed", "value": 351.00, "category": "Housing > Rent"},
    {"share": "fixed", "value": 351.00, "person": "Roommate A"},
    {"share": "remainder", "person": "Roommate B"}
  ],
  "add_tags": ["shared"],
  "set_note": "Rent - my share 351",
  "mark_reviewed": false
}
```

Split template share types: `fixed` (dollar amount), `percent`, `equal` (divide evenly among N lines), `remainder` (whatever is left, so totals always match). If the transaction amount doesn't fit the template (e.g., rent changed), don't apply the split; flag the transaction for review instead.

### 7.3 Behaviour
- Rules only touch fields that are **not locked**. Manual edits set `type_locked` / `locked`.
- `stop_processing` stops later rules from applying to that transaction.
- **"Re-run rules"** button: applies all rules to all unlocked transactions (with a dry-run preview showing what would change).
- **"Test rule"** in the rule editor: shows which existing transactions would match before saving.
- **"Make this a rule"**: after I manually categorize or split a transaction on the Review page, offer a pre-filled rule (description keyword, account, direction, amount range ±2%) that I can edit and save.
- Track `times_applied` so I can see which rules are useful.

### 7.4 Seed rules (disabled or editable examples)
- Scotia outgoing payment to Rogers → `transfer`.
- Rogers "payment received" → `transfer`.
- Payroll deposit keyword → `income`, category "Income > Salary".
- Bank fees / interest charges → `expense`, category "Fees & Interest".

The exact keywords come from my real descriptions later; seed them as placeholders I can edit.

---

## 8. Transaction types in detail

### 8.1 Transfers (credit card payments, moves between my accounts)
Paying Rogers from Scotia creates two rows: `-500.00` on Scotia and `+500.00` on Rogers. Both must be `transfer` so the Rogers purchases are counted only once.

**Auto-pairing algorithm** (runs after each import and on demand):
- Candidates: transactions on **different** accounts, **opposite** signs, **identical** absolute amount, dates within **7 days** (configurable), neither already paired.
- Prefer the pair with the smallest date gap. Pairing is one-to-one.
- Boost confidence when either side matches a transfer rule or keyword ("PAYMENT", "TRANSFER", "ROGERS"). Low-confidence matches (no keyword) are **suggested** on the Review page for me to confirm, not applied silently.
- On pairing: set both to `transfer` (unless locked) and create a `transfer_pair` link.
- **Unmatched transfers:** if one side is marked `transfer` but its partner hasn't been imported yet, show it in an "Unmatched transfers" list. It still stays out of spending.
- Handles partial and multiple card payments naturally, since each payment pairs independently.

### 8.1a Transfers to accounts outside this tool
Some outgoing transfers never have a matching side to pair against — money moved into an investment account, RRSP, TFSA, savings, or anywhere else this tool doesn't import from (e.g. a Wealthsimple contribution). These still aren't spending, so they still get `type = transfer`, but since there's no partner transaction to link, they'll sit in the "Unmatched transfers" list permanently — that's expected, not a sign something's broken, and no different in effect from a matched transfer (still excluded from spending either way).

Unlike a plain account-to-account transfer, it's still useful to know *how much* is going into investments over time. A transfer-typed transaction can still carry a normal category split (the type-level exclusion from spending always wins regardless of category, per 5.3), so these get categorized under **Investments** — giving a filterable, reportable total without it ever counting as spending.

### 8.2 Splits and "owed to me"
Any transaction can be split into lines. Each line has an amount, and either a category (my spending) or a person (owed to me).

Examples:
- Rent $1,053 → Rent $351 · Owed by Roommate A $351 · Owed by Roommate B $351
- Costco $120 → Groceries $80 · Household $25 · Owed by Roommate A $15
- Dinner $90 → Eating Out $30 · Owed by Friend X $30 · Owed by Friend Y $30

Split editor UI: add/remove lines, "split equally among N people", "my share = X, rest owed by [people]", and a live "remaining to allocate" counter that must reach $0.00 before saving.

### 8.2a Splitwise-tracked shares
Some shared expenses (mostly group ones — dinners, trips, group purchases) get logged in the Splitwise app instead of being tracked person-by-person here. These split the same way — my share → a spending category, the rest → a receivable line — but the receivable line is ticked **"Splitwise"** (`is_splitwise = 1`) instead of, or in addition to, picking a specific person. A Splitwise line doesn't need a `person_id`: Splitwise may track a whole group's breakdown itself, and this tool only needs to know "this much wasn't really mine."

Example: Dinner $90, paid in full on my card, logged as a group expense in Splitwise → Eating Out $30 (mine) · Splitwise $60 (fronted for the group).

Effects:
- The Splitwise line is excluded from "my spending" the same as any receivable (Section 5.3).
- The Dashboard and Home totals show a separate **"Via Splitwise"** figure alongside category spending, so "what I actually spent" and "what I fronted through Splitwise" are never conflated.
- Unlike a roommate's direct e-transfer, a Splitwise line isn't expected to close out via one matching incoming transfer — Splitwise nets multiple expenses and settles later, sometimes as a single lump payment covering several unrelated Splitwise lines. So these lines are allowed to sit open indefinitely; Section 8.3's reimbursement linking is optional here, not required to "finish" a transaction.
- Future idea (Section 15): export the list of Splitwise-tagged lines for a period, to cross-check against what's actually entered in the Splitwise app.

**The reverse case — settling a Splitwise debt I owe:** just as common as fronting one. Someone else covers something in Splitwise, it nets out that I owe my share, and I pay them back directly (an e-transfer to them, not through Splitwise itself). This is the mirror image of the example above: the *whole* transaction is Splitwise-related, not a fraction of it.
- `type = expense` (this is genuinely my spending, just never showed up as a bank-visible purchase since someone else paid).
- One split line, covering the full amount, `is_splitwise = 1`, no category, no person.
- Same exclusion applies: this line is a receivable-shaped exclusion from category spending, and counts toward "Via Splitwise" — just as an outflow instead of the usual inflow.
- `is_splitwise` isn't tied to a sign: it can appear on an outgoing line (I owe, I'm paying it off — this case) or an incoming one (someone settles a Splitwise debt they owe me, `type = reimbursement`). The Dashboard's "Via Splitwise" figure should show these as separate **sent** and **received** sub-totals, not just one net number, so both directions stay visible (Section 11).

### 8.3 Reimbursements
An incoming e-transfer from a roommate is `reimbursement`, not income.
- Link it to one or more open "owed" split lines (`reimburses` link, with an amount so partial payments work).
- Auto-suggest links: an incoming transfer whose description contains a person's name (configurable aliases per person, e.g., the name as it appears in Interac descriptions) and whose amount matches an open owed amount within the last 60 days.
- If I receive money that isn't linked to anything yet, it can sit as an unlinked reimbursement credit for that person.

### 8.4 Refunds
A credit on the Rogers card for a returned item is `refund`. It reduces spending in the category it's assigned to (optionally linked to the original purchase via `refund_of`). It is never counted as income.

### 8.5 Ignore
`ignore` is for rows I want excluded completely (test charges, reversed transactions, and so on).

---

## 9. Months, reporting periods, and coverage

### 9.1 Month definition
Statement periods are ignored. Each transaction is assigned to a period by `txn_date`.
- Default: calendar month (1st–last day).
- Setting: custom start day (e.g., month starts on the 15th), or payday-to-payday (period boundaries at each transaction matched by the payroll rule).
- All reports use the selected definition.

### 9.2 Coverage and completeness
For each account, compute which date ranges have been imported (the union of `import_batches` date_from/date_to).
- Settings/Import page: a visual timeline per account showing covered ranges and gaps.
- Every report for a period checks all **active** accounts. If any account isn't covered for the full period, show a clear warning, e.g. **"Incomplete: Rogers MC has no data after Sep 4. September totals are partial."**
- Note: a batch's date range comes from its first and last transaction. If a statement period had quiet days at the edges, that's acceptable; allow me to manually extend a batch's covered range.

---

## 10. Privacy and security requirements

1. `.streamlit/config.toml`:
   ```toml
   [server]
   address = "127.0.0.1"
   headless = true

   [browser]
   gatherUsageStats = false
   ```
2. The database lives in the data directory outside the repo. `.gitignore` must include `*.db`, `*.csv`, `data/`, `backups/`, `.env`.
3. Uploaded CSV contents are parsed in memory and stored only as rows in SQLite. Don't save copies of the uploaded files.
4. No external network calls anywhere in app code. Add a test that scans the codebase for `requests`, `httpx`, `urllib` usage outside the optional Ollama module.
5. Charts and UI assets must work offline.
6. **Backup:** a Settings button that copies `tracker.db` to `backups/tracker-YYYYMMDD-HHMM.db` using SQLite's backup API. Keep the last N (configurable).
7. **Export:** CSV/Excel export of transactions and reports, saved locally.
8. README should recommend: full-disk encryption (BitLocker/FileVault) or keeping the data folder in an encrypted VeraCrypt volume, and never placing the data folder inside OneDrive/iCloud/Google Drive sync folders.
9. Optional later: app-level passcode on launch.

---

## 11. Pages and UI

### Home
Current period at a glance: total spent, top categories, budget status, owed-to-me total, unreviewed count, completeness warnings, unmatched transfers.

### 1. Import
Multi-file uploader → per-file detected account (editable) → preview of first rows after normalization → Import button → summary. Below: import history with delete-batch option and the coverage timeline.

### 2. Review
The main work screen.
- Filters: period, account, type, category, unreviewed only, amount range, text search, tag.
- Editable grid (`st.data_editor`): date, account, description, amount, type, category, note, reviewed checkbox.
- Row detail panel: split editor, link to reimbursements/transfer partner, tags, "Make this a rule".
- Bulk actions: set type/category for selected rows, mark reviewed.
- Queues: Unreviewed, Suggested transfer pairs, Unmatched transfers, Suggested reimbursement links, Rule-split failures.
- Show which rule set each value (so I understand why something was categorized).

### 3. Dashboard
For the selected period (with completeness warning):
- Spending by category (bar or treemap), with drill-down to parent → child → transactions.
- Income vs spending, net savings, savings rate.
- Month-over-month trend per category (last 6/12 months).
- Top merchants.
- Account breakdown (where the spending happened).
- A separate "Via Splitwise" section for the period (`is_splitwise` split lines — see 8.2a), broken into **sent** (outgoing lines — dinners I fronted, debts I settled) and **received** (incoming lines — Splitwise debts paid back to me), not one net figure. Shown next to spending, not inside it.
All spending numbers exclude transfers, receivable splits (including Splitwise lines), and ignored rows, and subtract refunds.

### 4. Owed to Me
Per person: total owed, total reimbursed, balance, and the list of open items with dates. Click an item to see the original transaction. Option to mark an item as forgiven/settled manually. A separate "Via Splitwise" section totals open Splitwise lines (Section 8.2a), split into sent/received the same as the Dashboard — these aren't tied to a specific person and aren't expected to close out one-for-one against a reimbursement.

### 5. Rules
List with priority (reorderable), enabled toggle, times applied. Editor with a condition/action builder (no raw JSON needed, but a JSON view is available), test against existing data, and dry-run preview.

### 6. Budgets
Monthly budget per category, actual vs budget with progress bars, over-budget highlighting, and optional rollover of unused budget.

### 7. Settings
Accounts (add/edit, assign profile), categories (tree, add/rename/merge/deactivate; merging reassigns splits), people and their name aliases, month definition, transfer matching window, backup/restore, export, delete batch.

### Default categories (editable seed)
Housing (Rent, Utilities, Internet, Tenant Insurance, Laundry) · Groceries · Eating Out · Coffee · Transportation (Transit, Rideshare, Fuel) · Phone · Subscriptions · Shopping · Health & Fitness (Gym, Pharmacy, Supplements) · Education (Tuition, Books) · Entertainment · Travel · Gifts · Personal Care · Fees & Interest · Investments (see 8.1a — used on `transfer`-typed transactions, never counted as spending) · Other · Income (Salary, Other Income) · system: "Owed to me".

---

## 12. Build phases and acceptance criteria

### Phase 1 — Foundation and import
- Project scaffold, config, data dir, migrations, Streamlit config, `.gitignore`, README with run commands.
- YAML profile system, detection, parsing, normalization, fingerprint dedupe.
- Fake fixture CSVs for Scotia and Rogers (placeholder formats until I supply the real headers).
- Import page with summary and history; coverage computation.
- **Done when:** importing the same fixture twice creates zero duplicates; overlapping fixtures import only new rows; two identical same-day rows in one file are both kept; signs are correct for both accounts; tests pass.

### Phase 2 — Review and splits
- Review page grid with filters and bulk edits; type/category editing with locking.
- Split editor with validation; receivable splits with people.
- **Done when:** I can turn a $1,053 transaction into $351 Rent + two owed lines, totals validate, and edits survive a restart.

### Phase 3 — Transfers
- Auto-pairing, suggested pairs queue, unmatched transfers list.
- **Done when:** a Scotia → Rogers payment in the fixtures pairs automatically and disappears from spending; a payment with only one side imported shows as unmatched and is still excluded.

### Phase 4 — Dashboard and periods
- Monthly reports, calendar vs custom month setting, completeness warnings, trends.
- **Done when:** September totals are correct across both accounts with mismatched statement periods, and a missing-data warning appears when one account's coverage is short.

### Phase 5 — Rules engine
- Conditions/actions, priority, locking, test/dry-run, "Make this a rule", split templates, seed rules.
- **Done when:** a rent rule auto-splits next month's rent on import, and re-running rules never changes manually locked values.

### Phase 6 — Owed-to-me, refunds, budgets
- Reimbursement linking (manual + suggested), partial payments, per-person balances, refunds, budgets page.
- Splitwise-tagged split lines (Section 8.2a): excluded from spending, shown as a separate "Via Splitwise" total on the Dashboard and Owed-to-Me page.
- **Done when:** after roommates' transfers are linked, the Owed page shows $0 balance for that month, and neither transfer appears as income. A dinner split with a Splitwise line shows the correct reduced amount in "my spending" and the fronted amount under "Via Splitwise."

### Phase 7 — Polish and extras
- Backup/restore, export to CSV/Excel, category merge, tags, top merchants.
- Optional: local Ollama category suggestions for unreviewed rows (off by default, localhost only, shows as suggestions I accept or reject).
- Optional: PDF statement import via `pdfplumber` (only if a bank lacks CSV export).

---

## 13. Testing

- pytest with **fake** fixture CSVs covering: both sign conventions, debit/credit column mode, overlapping files, identical same-day rows, pending rows, foreign-currency rows, trailing summary lines, and a credit card payment pair.
- Unit tests for: fingerprinting, normalization, split validation (sums), split templates (fixed/percent/equal/remainder), transfer pairing (window, one-to-one, closest date), rule matching for each operator, lock behaviour, period assignment (calendar and custom), coverage gaps, receivable balances with partial reimbursements, and report totals excluding transfers/receivables.
- The no-network test from Section 10.

---

## 14. Open items I'll provide when asked

1. **Header row + one fake example row** from a Scotiabank chequing CSV export (a purchase/bill payment and a deposit).
2. The same from a Rogers Mastercard CSV export (a purchase and a payment received).
3. How the Rogers payment appears on each side (description text, with fake amounts).
4. How Interac e-transfers in and out appear in Scotia descriptions (whether the other person's name is included).
5. My preferred month definition (probably calendar month).
6. My final category list and roommates' names (entered through Settings, not code).

---

## 15. Future ideas (not in scope yet)

- Additional accounts (savings, TFSA, a second card) via new YAML profiles.
- Recurring-payment detection and an "upcoming bills" view.
- Subscription tracker (detects monthly repeating merchants).
- Year-end summary and tax-relevant category export.
- Goals (e.g., savings targets) with progress.
- Mobile-friendly layout for viewing on my phone over the local network (opt-in only, since it relaxes `127.0.0.1` binding).
- Export/list of all Splitwise-tagged split lines for a period (Section 8.2a), to cross-check against what's actually entered in the Splitwise app.
