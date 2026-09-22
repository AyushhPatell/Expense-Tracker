import csv


def parse_csv(file_bytes: bytes, profile: dict) -> tuple[list[dict], int]:
    """Parse raw CSV bytes according to a bank profile.

    Returns (rows, skipped_count): rows is a list of dicts (raw string values,
    original column names, original file order) after skip_rows and skip_if
    filtering; skipped_count covers blank lines and skip_if matches. Rows that
    parse but turn out not to be real transactions (e.g. a trailing summary
    line with no valid date) are filtered later, in normalize.py.
    """
    parse_cfg = profile["parse"]
    encoding = parse_cfg.get("encoding", "utf-8")
    text = file_bytes.decode(encoding)
    lines = text.splitlines()

    skip_rows = parse_cfg.get("skip_rows", 0)
    lines = lines[skip_rows:]

    reader = csv.DictReader(lines)
    skip_if = parse_cfg.get("skip_if") or []

    rows = []
    skipped = 0
    for raw_row in reader:
        if all((v is None or str(v).strip() == "") for v in raw_row.values()):
            skipped += 1
            continue

        if _matches_skip_if(raw_row, skip_if):
            skipped += 1
            continue

        rows.append(raw_row)

    return rows, skipped


def _matches_skip_if(raw_row: dict, skip_if: list[dict]) -> bool:
    for cond in skip_if:
        value = (raw_row.get(cond["column"]) or "").strip()
        if value == str(cond["equals"]):
            return True
    return False
