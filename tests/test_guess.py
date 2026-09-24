from core.importers.guess import guess_amount_config, guess_date_column, guess_description_columns


def test_guess_date_column_finds_iso_dates():
    rows = [
        {"Date": "2025-08-17", "Amount": "12.34", "Description": "Coffee"},
        {"Date": "2025-08-18", "Amount": "5.00", "Description": "Tea"},
    ]
    col, fmt = guess_date_column(rows, ["Date", "Amount", "Description"])
    assert col == "Date"
    assert fmt == "%Y-%m-%d"


def test_guess_date_column_finds_slash_format():
    rows = [
        {"TxnDate": "08/17/2025", "Amt": "12.34"},
        {"TxnDate": "08/18/2025", "Amt": "5.00"},
    ]
    col, fmt = guess_date_column(rows, ["TxnDate", "Amt"])
    assert col == "TxnDate"
    assert fmt == "%m/%d/%Y"


def test_guess_date_column_returns_none_when_nothing_matches():
    rows = [{"A": "hello", "B": "world"}]
    col, fmt = guess_date_column(rows, ["A", "B"])
    assert col is None
    assert fmt is None


def test_guess_date_column_ignores_a_column_with_one_bad_value():
    """A column where most values parse but one doesn't shouldn't be
    trusted — that's more likely the wrong column than a data quirk."""
    rows = [
        {"Maybe": "2025-08-17", "Ref": "REF001"},
        {"Maybe": "not-a-date", "Ref": "REF002"},
    ]
    col, fmt = guess_date_column(rows, ["Maybe", "Ref"])
    assert col is None


def test_guess_amount_config_single_column_prefers_amount_named():
    rows = [{"Date": "2025-08-17", "Balance": "1200.00", "Amount": "-4.50"}]
    result = guess_amount_config(rows, ["Date", "Balance", "Amount"], exclude={"Date"})
    assert result["mode"] == "single"
    assert result["column"] == "Amount"


def test_guess_amount_config_debit_credit_mode():
    # Debit/credit columns are typically sparse — each row fills one and
    # leaves the other blank — so the sample needs a row for each to prove
    # both columns are genuinely numeric.
    rows = [
        {"Date": "2025-08-17", "Withdrawal": "50.00", "Deposit": ""},
        {"Date": "2025-08-18", "Withdrawal": "", "Deposit": "200.00"},
    ]
    result = guess_amount_config(rows, ["Date", "Withdrawal", "Deposit"], exclude={"Date"})
    assert result["mode"] == "debit_credit"
    assert result["debit_column"] == "Withdrawal"
    assert result["credit_column"] == "Deposit"


def test_guess_amount_config_excludes_claimed_columns():
    rows = [{"Date": "2025-08-17", "Amount": "-4.50"}]
    result = guess_amount_config(rows, ["Date", "Amount"], exclude={"Date", "Amount"})
    assert result["column"] is None


def test_guess_description_columns():
    headers = ["Date", "Merchant Name", "Amount", "Reference Number"]
    result = guess_description_columns(headers, claimed={"Date", "Amount"})
    assert result == ["Merchant Name"]


def test_guess_description_columns_excludes_claimed():
    headers = ["Date", "Description", "Amount"]
    result = guess_description_columns(headers, claimed={"Date", "Amount", "Description"})
    assert result == []
