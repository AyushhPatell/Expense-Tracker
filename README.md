# Expense Tracker

A private, local-only expense tracker. Runs entirely on your machine — no cloud
APIs, no telemetry, no external network calls (see `tests/test_no_network.py`).

Full design spec: [`expense-tracker-spec.md`](expense-tracker-spec.md).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the app

```bash
streamlit run app/Home.py
```

Opens at `http://127.0.0.1:8501`. The database lives outside the repo, at
`~/ExpenseTrackerData/tracker.db` by default — override with the
`EXPENSE_TRACKER_DATA` environment variable (e.g. to point at an encrypted
volume).

## Run the tests

```bash
pytest
```

Tests only ever touch fake fixture CSVs in `tests/fixtures/` and a temporary
database — never your real data directory.

## Current status: Phase 4 (Dashboard and periods)

- SQLite schema with immutable raw transactions and a versioned migration runner.
- YAML bank-profile system (`core/importers/profiles/`) matching the real
  Scotiabank chequing and Rogers Mastercard CSV export formats.
- Import pipeline: detect profile → parse → normalize → fingerprint-dedupe → insert.
- Import page: multi-file upload, per-file account detection (editable),
  preview, import summary, batch history with delete, and a coverage timeline.
- Review page: filters, an editable grid with bulk actions, and a full split
  editor (receivables, Splitwise lines, "split equally," "my share = X, rest
  owed by...", an equal-split-including-me shortcut).
- Transfers: auto-pairing between accounts (credit card payments, etc.),
  suggested/unmatched-transfer queues, manual confirm.
- Dashboard: monthly spending by category, income vs. spending, net savings,
  a separate Via Splitwise (sent/received) total, account breakdown, top
  merchants, a 6-month trend, and coverage-completeness warnings per period.
- Import: an optional "only import on/after a date" cutoff, for filling a
  coverage gap from an overlapping statement without pulling in history
  you've deliberately decided not to track.
- Owed to Me: a lean v1 — all-time running balance per person and a
  chronological ledger, from every split ever tagged to that person
  (shared-expense receivables and plain personal loans both work the same
  way). Explicit per-item reimbursement linking (spec 8.3) isn't built yet —
  balances are accurate without it, just not itemized per specific payment.

Not built yet: rules engine, refunds handling, budgets, backups/export.
See §12 of the spec for the phase plan.

**Note on fixtures:** `tests/fixtures/*.csv` use the real column structure
from both banks but entirely invented values — never real transaction data.
That's a hard rule for this project (spec §0.2): only fake data belongs in
the repo or in chat.

## Privacy

- Keep the data directory on an encrypted disk (FileVault/BitLocker) or an
  encrypted volume (e.g. VeraCrypt).
- Don't put the data directory inside a cloud-sync folder (iCloud Drive,
  OneDrive, Google Drive, Dropbox).
- Uploaded CSVs are parsed in memory and never saved to disk as files —
  only their parsed rows are written to the local SQLite database.
