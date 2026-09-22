from core.fingerprint import assign_occurrence_indexes, compute_fingerprint, normalize_description


def test_normalize_description_collapses_whitespace_and_case():
    assert normalize_description("  Coffee   Shop  ") == "COFFEE SHOP"


def test_assign_occurrence_indexes_increments_per_duplicate_key():
    rows = [
        {"txn_date": "2025-08-25", "amount_cents": -350, "description": "Coffee Shop"},
        {"txn_date": "2025-08-25", "amount_cents": -350, "description": "coffee shop"},
        {"txn_date": "2025-08-25", "amount_cents": -500, "description": "Groceries"},
    ]
    assert assign_occurrence_indexes(rows) == [1, 2, 1]


def test_compute_fingerprint_differs_by_occurrence_index():
    fp1 = compute_fingerprint(1, "2025-08-25", -350, "Coffee Shop", 1)
    fp2 = compute_fingerprint(1, "2025-08-25", -350, "Coffee Shop", 2)
    assert fp1 != fp2


def test_compute_fingerprint_same_inputs_same_output():
    fp1 = compute_fingerprint(1, "2025-08-25", -350, "Coffee Shop", 1)
    fp2 = compute_fingerprint(1, "2025-08-25", -350, "COFFEE   SHOP", 1)
    assert fp1 == fp2  # description normalization makes these equivalent
