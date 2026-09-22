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

## Current status: Phase 1 (Foundation and import)

- SQLite schema with immutable raw transactions and a versioned migration runner.
- YAML bank-profile system (`core/importers/profiles/`) — **the Scotia and
  Rogers profiles are placeholders** with made-up column names, until real
  header rows are supplied (see spec §14).
- Import pipeline: detect profile → parse → normalize → fingerprint-dedupe → insert.
- Import page: multi-file upload, per-file account detection (editable),
  preview, import summary, batch history with delete, and a coverage timeline.

Not built yet: Review/splits, transfer matching, dashboards, rules engine,
owed-to-me/refunds/budgets, backups/export. See §12 of the spec for the phase plan.

## Privacy

- Keep the data directory on an encrypted disk (FileVault/BitLocker) or an
  encrypted volume (e.g. VeraCrypt).
- Don't put the data directory inside a cloud-sync folder (iCloud Drive,
  OneDrive, Google Drive, Dropbox).
- Uploaded CSVs are parsed in memory and never saved to disk as files —
  only their parsed rows are written to the local SQLite database.
