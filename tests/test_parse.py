from core.importers.normalize import normalize_row
from core.importers.parse import parse_csv

PROFILE = {
    "parse": {
        "encoding": "utf-8-sig",
        "skip_rows": 0,
        "date_column": "Date",
        "date_format": "%Y-%m-%d",
        "description_columns": ["Description"],
        "amount": {"mode": "single", "column": "Amount", "invert": False},
        "currency_column": None,
        "skip_if": [{"column": "Status", "equals": "Pending"}],
    }
}


def test_parse_csv_skips_pending_rows():
    csv_text = (
        "Date,Description,Amount,Status\n"
        "2025-08-01,Coffee,3.50,Posted\n"
        "2025-08-02,Pending Charge,10.00,Pending\n"
    )
    rows, skipped = parse_csv(csv_text.encode("utf-8"), PROFILE)
    assert skipped == 1
    assert len(rows) == 1
    assert rows[0]["Description"] == "Coffee"


def test_parse_csv_skips_blank_lines():
    # csv.DictReader already drops blank rows before they reach our code, so
    # trailing blank lines just disappear rather than becoming visible rows.
    csv_text = "Date,Description,Amount,Status\n2025-08-01,Coffee,3.50,Posted\n\n\n"
    rows, _ = parse_csv(csv_text.encode("utf-8"), PROFILE)
    assert len(rows) == 1
    assert rows[0]["Description"] == "Coffee"


def test_parse_csv_handles_bom():
    csv_text = "Date,Description,Amount,Status\n2025-08-01,Coffee,3.50,Posted\n"
    rows, _ = parse_csv(csv_text.encode("utf-8-sig"), PROFILE)
    assert len(rows) == 1
    assert rows[0]["Date"] == "2025-08-01"


def test_normalize_row_returns_none_for_unparseable_summary_line():
    raw_row = {"Date": "TOTAL", "Description": "", "Amount": "1234.56", "Status": "Posted"}
    assert normalize_row(raw_row, PROFILE) is None


def test_normalize_row_handles_parentheses_and_commas():
    raw_row = {"Date": "2025-08-01", "Description": "Refund", "Amount": "($1,234.56)", "Status": "Posted"}
    norm = normalize_row(raw_row, PROFILE)
    assert norm["amount_cents"] == -123456
