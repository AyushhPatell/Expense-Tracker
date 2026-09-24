"""The rules engine (spec section 7): conditions/actions, matching, applying,
dry-run preview, and rule management. This is how the app gets smarter about
recurring transactions instead of me re-categorizing the same rent payment
and coffee purchase every single month.

Design notes worth knowing before editing this file:

- Conditions are a small recursive tree: a leaf is {"field", "op", "value"};
  a group is {"all": [...]} or {"any": [...]} whose items can themselves be
  leaves or nested groups. rule_matches() walks this directly against a
  transaction's context dict (_build_ctx). amount_abs is compared in CENTS
  (the app's canonical money unit) even though the UI shows dollars.

- Actions are applied by preview_or_apply_rule(), which is the single place
  that knows how to turn an actions dict into real changes. It always
  computes what WOULD change; only when commit=True does it actually write.
  This one function backs three different call sites (apply on import,
  "re-run rules", and dry-run preview) so the matching/application logic
  never has to be duplicated or drift out of sync between them.

- "Manual beats automatic" (spec section 2) is enforced per-field, not
  per-transaction: a locked type stops set_type; a locked split stops
  split_template/set_category; nothing else is blocked. A transaction with a
  manually-set type but no category yet can still be auto-categorized, and
  vice versa.

- split_template's "equal" and "remainder" share types both draw from the
  same leftover pool (whatever's left after fixed/percent lines), split
  evenly across however many equal/remainder lines exist. A template only
  fails to apply (spec 7.2: "doesn't fit... flag for review instead") when
  there's a shortfall/overshoot with nothing able to absorb it — no
  equal/remainder line present while fixed+percent lines don't sum exactly,
  or fixed+percent lines alone already exceed the transaction (a negative
  remainder). Otherwise the remainder line simply flexes to whatever's
  actually left — that's what lets a "my $351 + roommate's remainder" rent
  template keep working correctly even after a rent increase, without
  needing to be re-edited every time.

- set_category and split_template are alternatives, not additive: if a
  rule's actions include both (the spec's own rent example does, somewhat
  redundantly, since the template already assigns per-line categories),
  split_template wins and set_category is ignored for that rule.

- set_note only applies when the transaction doesn't already have a note.
  There's no schema column to "lock" a note the way type/splits are locked,
  so treating "already has text" as the manual signal is the simplest way to
  avoid a rule stomping something I typed by hand on every re-run.
"""

import json
import re
from datetime import date, datetime, timezone

from core.categories import list_categories, list_people
from core.splits import (
    equal_shares,
    get_splits,
    get_transaction,
    has_locked_split,
    is_simple_split,
    replace_splits,
    set_note,
    set_reviewed,
    set_single_category,
    set_type,
)

TEXT_FIELDS = ("description", "account", "account_kind", "direction")
NUMERIC_FIELDS = ("amount_abs", "day_of_month", "weekday")
FIELDS = TEXT_FIELDS + NUMERIC_FIELDS
OPS = ("contains", "not_contains", "starts_with", "equals", "regex", "in", "between", "gt", "lt")
SHARE_TYPES = ("fixed", "percent", "equal", "remainder")


# ---------------------------------------------------------------------------
# Condition matching
# ---------------------------------------------------------------------------


def _build_ctx(txn_row) -> dict:
    """A transaction's condition-matchable fields. txn_row must include the
    joined account_name/account_kind columns (see _transaction_ctx)."""
    txn_date = date.fromisoformat(txn_row["txn_date"])
    return {
        "description": txn_row["description"] or "",
        "account": txn_row["account_name"],
        "account_kind": txn_row["account_kind"],
        "direction": "out" if txn_row["amount"] < 0 else "in",
        "amount_abs": abs(txn_row["amount"]),
        "day_of_month": txn_date.day,
        "weekday": txn_date.weekday(),  # Monday=0 .. Sunday=6
    }


def _transaction_ctx(conn, transaction_id: int) -> tuple:
    """Returns (txn_row, ctx) — the raw row (joined with account) and its
    matchable field dict, fetched fresh so matching always sees committed
    state, never a stale in-progress edit."""
    row = conn.execute(
        "SELECT t.*, a.name AS account_name, a.kind AS account_kind "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = ?",
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown transaction id {transaction_id}")
    return row, _build_ctx(row)


def _as_text(value) -> str:
    return str(value).strip().lower()


def _eval_leaf(cond: dict, ctx: dict) -> bool:
    field, op, value = cond["field"], cond["op"], cond.get("value")
    if field not in ctx:
        raise ValueError(f"Unknown condition field '{field}'")
    actual = ctx[field]

    if op == "contains":
        return _as_text(value) in _as_text(actual)
    if op == "not_contains":
        return _as_text(value) not in _as_text(actual)
    if op == "starts_with":
        return _as_text(actual).startswith(_as_text(value))
    if op == "regex":
        return re.search(str(value), str(actual), re.IGNORECASE) is not None
    if op == "equals":
        return _as_text(actual) == _as_text(value) if isinstance(actual, str) else actual == value
    if op == "in":
        return _as_text(actual) in {_as_text(v) for v in value} if isinstance(actual, str) else actual in value
    if op == "between":
        lo, hi = value
        return lo <= actual <= hi
    if op == "gt":
        return actual > value
    if op == "lt":
        return actual < value
    raise ValueError(f"Unknown operator '{op}'")


def _eval_node(node: dict, ctx: dict) -> bool:
    if "all" in node:
        return all(_eval_node(child, ctx) for child in node["all"])
    if "any" in node:
        return any(_eval_node(child, ctx) for child in node["any"])
    return _eval_leaf(node, ctx)


def rule_matches(conditions: dict, ctx: dict) -> bool:
    """conditions is a not-yet-saved or already-saved rule's condition tree;
    ctx is a _build_ctx()-shaped dict. Exposed standalone (not just via
    preview_or_apply_rule) so 'Test rule' can check a draft rule that isn't
    saved yet, without needing a transaction id."""
    return _eval_node(conditions, ctx)


def test_conditions_against_existing(conn, conditions: dict, limit: int = 20) -> dict:
    """'Test rule' (spec 7.3): which existing transactions a not-yet-saved
    condition tree would match, for the rule editor to show before I commit
    to saving it. Returns a capped sample plus the true total count."""
    rows = conn.execute(
        "SELECT t.*, a.name AS account_name, a.kind AS account_kind "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "ORDER BY t.txn_date DESC, t.id DESC"
    ).fetchall()

    matches = []
    total = 0
    for row in rows:
        if rule_matches(conditions, _build_ctx(row)):
            total += 1
            if len(matches) < limit:
                matches.append(
                    {
                        "transaction_id": row["id"],
                        "txn_date": row["txn_date"],
                        "account": row["account_name"],
                        "description": row["description"],
                        "amount": row["amount"],
                    }
                )
    return {"total": total, "sample": matches}


# ---------------------------------------------------------------------------
# Split template resolution
# ---------------------------------------------------------------------------


def _resolve_split_template(conn, txn_amount: int, template: list[dict]):
    """Resolves a split_template action into concrete split lines (cents),
    or returns None if the template doesn't reconcile to txn_amount (spec
    7.2 — the transaction should be flagged for review instead of getting a
    wrong split). See the module docstring for exactly when that happens."""
    if not template:
        return None

    cat_id_by_label = {c["label"]: c["id"] for c in list_categories(conn)}
    person_id_by_label = {p["label"]: p["id"] for p in list_people(conn)}

    sign = 1 if txn_amount >= 0 else -1
    total_abs = abs(txn_amount)

    line_amounts: list[int | None] = [None] * len(template)
    resolved_categories: list[int | None] = [None] * len(template)
    resolved_people: list[int | None] = [None] * len(template)
    leftover_indexes = []
    fixed_percent_total = 0

    for i, line in enumerate(template):
        category_label = line.get("category")
        person_label = line.get("person")
        if category_label:
            if category_label not in cat_id_by_label:
                return None
            resolved_categories[i] = cat_id_by_label[category_label]
        if person_label:
            if person_label not in person_id_by_label:
                return None
            resolved_people[i] = person_id_by_label[person_label]

        share = line.get("share")
        if share == "fixed":
            amt = sign * round(abs(float(line["value"])) * 100)
            line_amounts[i] = amt
            fixed_percent_total += amt
        elif share == "percent":
            amt = sign * round(total_abs * (float(line["value"]) / 100))
            line_amounts[i] = amt
            fixed_percent_total += amt
        elif share in ("equal", "remainder"):
            leftover_indexes.append(i)
        else:
            return None  # unknown share type

    leftover = txn_amount - fixed_percent_total
    if leftover_indexes:
        if leftover != 0 and (leftover < 0) != (txn_amount < 0):
            return None  # fixed/percent lines alone already overshoot the transaction
        shares = equal_shares(abs(leftover), len(leftover_indexes))
        leftover_sign = 1 if leftover >= 0 else -1
        for idx, share_amt in zip(leftover_indexes, shares):
            line_amounts[idx] = leftover_sign * share_amt
    elif leftover != 0:
        return None  # nothing left to absorb the mismatch

    resolved = []
    for i, line in enumerate(template):
        resolved.append(
            {
                "amount": line_amounts[i],
                "category_id": resolved_categories[i],
                "person_id": resolved_people[i],
                "is_splitwise": bool(line.get("is_splitwise")),
                "note": line.get("note"),
            }
        )
    return resolved


# ---------------------------------------------------------------------------
# Applying a single rule to a single transaction
# ---------------------------------------------------------------------------


def _get_or_create_tag(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def _current_split_lines(conn, transaction_id: int) -> list[dict]:
    return [
        {
            "amount": s["amount"],
            "category_id": s["category_id"],
            "person_id": s["person_id"],
            "is_splitwise": bool(s["is_splitwise"]),
        }
        for s in get_splits(conn, transaction_id)
    ]


def _lines_match(a: list[dict], b: list[dict]) -> bool:
    """Order-independent comparison — replace_splits always deletes and
    reinserts, so re-running an unchanged rule shouldn't be reported (or
    written) as a change just because the template's line order doesn't
    match whatever order they happened to land in before."""
    key = lambda lines: sorted(repr((l["amount"], l["category_id"], l["person_id"], l["is_splitwise"])) for l in lines)
    return key(a) == key(b)


def _apply_tags(conn, transaction_id: int, tag_names: list[str]) -> None:
    if not tag_names:
        return
    split_ids = [
        r["id"] for r in conn.execute("SELECT id FROM splits WHERE transaction_id = ?", (transaction_id,)).fetchall()
    ]
    for tag_name in tag_names:
        tag_id = _get_or_create_tag(conn, tag_name)
        for split_id in split_ids:
            conn.execute("INSERT OR IGNORE INTO split_tags (split_id, tag_id) VALUES (?, ?)", (split_id, tag_id))
    conn.commit()


def preview_or_apply_rule(conn, transaction_id: int, rule: dict, commit: bool) -> dict:
    """Evaluates one rule (a dict with 'id', 'name', 'conditions', 'actions')
    against one transaction. Always returns what matched/would-change;
    writes to the database only when commit=True. This is the single
    implementation behind applying rules on import, 're-run rules', and
    dry-run preview — see the module docstring."""
    txn_row, ctx = _transaction_ctx(conn, transaction_id)
    if not rule_matches(rule["conditions"], ctx):
        return {"matched": False, "changes": {}, "split_failure": None}

    actions = rule["actions"]
    changes: dict = {}
    split_failure = None

    if "set_type" in actions and not txn_row["type_locked"] and actions["set_type"] != txn_row["type"]:
        changes["type"] = actions["set_type"]
        if commit:
            set_type(conn, transaction_id, actions["set_type"], lock=False)

    tags_applied = False
    if "split_template" in actions:
        resolved = _resolve_split_template(conn, txn_row["amount"], actions["split_template"])
        if resolved is None:
            split_failure = "Split template doesn't reconcile to the transaction amount"
        elif not has_locked_split(conn, transaction_id):
            if not _lines_match(resolved, _current_split_lines(conn, transaction_id)):
                changes["split"] = resolved
                if commit:
                    replace_splits(conn, transaction_id, resolved, locked=False, rule_id=rule["id"])
            tags_applied = True  # splits exist (new or already-matching) — safe to (re-)tag them
    elif "set_category" in actions:
        if is_simple_split(conn, transaction_id) and not has_locked_split(conn, transaction_id):
            cat_id_by_label = {c["label"]: c["id"] for c in list_categories(conn)}
            cat_id = cat_id_by_label.get(actions["set_category"])
            if cat_id is not None:
                current = _current_split_lines(conn, transaction_id)
                already_set = len(current) == 1 and current[0]["category_id"] == cat_id and not current[0]["person_id"] and not current[0]["is_splitwise"]
                if not already_set:
                    changes["category"] = actions["set_category"]
                    if commit:
                        set_single_category(conn, transaction_id, cat_id, locked=False, rule_id=rule["id"])
                tags_applied = True

    if "add_tags" in actions and actions["add_tags"]:
        changes["tags"] = actions["add_tags"]
        if commit and tags_applied:
            _apply_tags(conn, transaction_id, actions["add_tags"])

    if "set_note" in actions and not txn_row["note"]:
        changes["note"] = actions["set_note"]
        if commit:
            set_note(conn, transaction_id, actions["set_note"])

    if "mark_reviewed" in actions:
        desired = bool(actions["mark_reviewed"])
        if desired != bool(txn_row["reviewed"]):
            changes["reviewed"] = desired
            if commit:
                set_reviewed(conn, transaction_id, desired)

    return {"matched": True, "rule_id": rule["id"], "rule_name": rule["name"], "changes": changes, "split_failure": split_failure}


# ---------------------------------------------------------------------------
# Running the whole rule set
# ---------------------------------------------------------------------------


def _load_enabled_rules(conn) -> list[dict]:
    return [r for r in list_rules(conn) if r["enabled"]]


def apply_rules_to_transactions(conn, transaction_ids: list[int], commit: bool = True) -> dict:
    """Applies every enabled rule, in priority order, to each given
    transaction — stopping early for a transaction once a matching rule sets
    stop_processing. Used right after import (spec 6.4 step 2), by 're-run
    rules', and (with commit=False) for a dry-run preview. Returns counts
    plus per-transaction split-template failures to surface for review."""
    rules = _load_enabled_rules(conn)
    matched = 0
    changed = 0
    failures = []
    preview_rows = []

    for txn_id in transaction_ids:
        for rule in rules:
            result = preview_or_apply_rule(conn, txn_id, rule, commit=commit)
            if not result["matched"]:
                continue
            matched += 1
            if commit:
                conn.execute("UPDATE rules SET times_applied = times_applied + 1 WHERE id = ?", (rule["id"],))
                conn.commit()
            if result["changes"]:
                changed += 1
                preview_rows.append(
                    {
                        "transaction_id": txn_id,
                        "rule_name": rule["name"],
                        "changes": result["changes"],
                    }
                )
            if result["split_failure"]:
                failures.append(
                    {"transaction_id": txn_id, "rule_name": rule["name"], "reason": result["split_failure"]}
                )
            if rule["stop_processing"]:
                break

    return {"matched": matched, "changed": changed, "failures": failures, "preview": preview_rows}


def apply_rules_to_all(conn) -> dict:
    """'Re-run rules': every transaction, not just newly-imported ones. Safe
    to call repeatedly — every field it touches is guarded by its own lock
    (see module docstring), so already-reviewed/locked transactions simply
    no-op rather than needing a separate 'unlocked only' filter here."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM transactions ORDER BY id").fetchall()]
    return apply_rules_to_transactions(conn, ids, commit=True)


def preview_rules_run(conn, transaction_ids: list[int] | None = None) -> dict:
    """Dry-run version of apply_rules_to_all / apply_rules_to_transactions —
    computes what would change without writing anything, for the Rules
    page's preview."""
    if transaction_ids is None:
        transaction_ids = [r["id"] for r in conn.execute("SELECT id FROM transactions ORDER BY id").fetchall()]
    return apply_rules_to_transactions(conn, transaction_ids, commit=False)


def get_rule_split_failures(conn) -> list[dict]:
    """Transactions where a matching, enabled rule's split_template can't
    reconcile to the amount (spec 11: 'Rule-split failures' queue). Computed
    live via a dry run rather than stored, so it's always current and needs
    no extra schema. Scoped to unreviewed transactions — that's the set
    actually waiting on the rules engine to do something useful with them."""
    rules = [r for r in _load_enabled_rules(conn) if "split_template" in r["actions"]]
    if not rules:
        return []
    txn_ids = [r["id"] for r in conn.execute("SELECT id FROM transactions WHERE reviewed = 0").fetchall()]

    failures = []
    for txn_id in txn_ids:
        for rule in rules:
            result = preview_or_apply_rule(conn, txn_id, rule, commit=False)
            if result["matched"] and result["split_failure"]:
                txn = get_transaction(conn, txn_id)
                failures.append(
                    {
                        "transaction_id": txn_id,
                        "txn_date": txn["txn_date"],
                        "description": txn["description"],
                        "amount": txn["amount"],
                        "rule_name": rule["name"],
                        "reason": result["split_failure"],
                    }
                )
                break
    return failures


# ---------------------------------------------------------------------------
# Rule CRUD
# ---------------------------------------------------------------------------


def list_rules(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM rules ORDER BY priority ASC, id ASC").fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "priority": r["priority"],
            "enabled": bool(r["enabled"]),
            "conditions": json.loads(r["conditions"]),
            "actions": json.loads(r["actions"]),
            "stop_processing": bool(r["stop_processing"]),
            "times_applied": r["times_applied"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_rule(conn, rule_id: int) -> dict:
    row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown rule id {rule_id}")
    return {
        "id": row["id"],
        "name": row["name"],
        "priority": row["priority"],
        "enabled": bool(row["enabled"]),
        "conditions": json.loads(row["conditions"]),
        "actions": json.loads(row["actions"]),
        "stop_processing": bool(row["stop_processing"]),
        "times_applied": row["times_applied"],
        "created_at": row["created_at"],
    }


def create_rule(
    conn,
    name: str,
    conditions: dict,
    actions: dict,
    priority: int = 100,
    enabled: bool = True,
    stop_processing: bool = False,
) -> int:
    if not name.strip():
        raise ValueError("Rule name can't be blank")
    if not conditions:
        raise ValueError("A rule needs at least one condition")
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO rules (name, priority, enabled, conditions, actions, stop_processing, times_applied, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
        (name.strip(), priority, 1 if enabled else 0, json.dumps(conditions), json.dumps(actions), 1 if stop_processing else 0, now),
    )
    conn.commit()
    return cur.lastrowid


def update_rule(
    conn,
    rule_id: int,
    name: str,
    conditions: dict,
    actions: dict,
    priority: int,
    enabled: bool,
    stop_processing: bool,
) -> None:
    if not name.strip():
        raise ValueError("Rule name can't be blank")
    if not conditions:
        raise ValueError("A rule needs at least one condition")
    conn.execute(
        "UPDATE rules SET name = ?, priority = ?, enabled = ?, conditions = ?, actions = ?, stop_processing = ? "
        "WHERE id = ?",
        (
            name.strip(),
            priority,
            1 if enabled else 0,
            json.dumps(conditions),
            json.dumps(actions),
            1 if stop_processing else 0,
            rule_id,
        ),
    )
    conn.commit()


def set_rule_priority_and_enabled(conn, rule_id: int, priority: int, enabled: bool, stop_processing: bool) -> None:
    """Quick path for the rules-list grid: reorder/toggle without touching
    conditions or actions."""
    conn.execute(
        "UPDATE rules SET priority = ?, enabled = ?, stop_processing = ? WHERE id = ?",
        (priority, 1 if enabled else 0, 1 if stop_processing else 0, rule_id),
    )
    conn.commit()


def delete_rule(conn, rule_id: int) -> None:
    # Splits this rule created point back at it (created_by_rule); detach
    # them first so the delete doesn't fail under foreign_keys=ON, and so
    # those splits simply become "not attributed to any rule" rather than
    # being destroyed — the split itself (and its data) stays put.
    conn.execute("UPDATE splits SET created_by_rule = NULL WHERE created_by_rule = ?", (rule_id,))
    conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# "Make this a rule"
# ---------------------------------------------------------------------------


# Generic banking vocabulary that shows up in almost every description of
# its kind (every card purchase says "purchase", every bill payment says
# "payment") — picking one of these as the suggested keyword would match
# far more than just this transaction, so they're excluded before falling
# back to plain "longest word" against whatever's left (usually the actual
# merchant/payee name, e.g. "Coffee" out of "pos purchase | Fake Coffee Shop").
_GENERIC_DESCRIPTION_WORDS = {
    "POS", "PURCHASE", "PAYMENT", "BILL", "DEPOSIT", "WITHDRAWAL", "TRANSFER",
    "INTERAC", "FREE", "DEBIT", "CREDIT", "THANK", "PREAUTH", "RECURRING",
}


def _suggest_keyword(description: str) -> str:
    words = re.findall(r"[A-Za-z]{4,}", description or "")
    candidates = [w for w in words if w.upper() not in _GENERIC_DESCRIPTION_WORDS] or words
    if candidates:
        return max(candidates, key=len).upper()
    return (description or "").strip().upper()


def suggest_rule_draft(conn, transaction_id: int) -> dict:
    """A pre-filled (unsaved) rule from an already-categorized transaction
    (spec 7.3 'Make this a rule'): description keyword, account, direction,
    amount range +-2%, and the type/category currently set on it. Real
    Scotia e-transfer descriptions carry no distinguishing text ('Free
    Interac E-Transfer' for every transfer in or out) — the amount_abs and
    account/direction conditions are what actually narrow those down, not
    the keyword."""
    txn, ctx = _transaction_ctx(conn, transaction_id)
    splits = get_splits(conn, transaction_id)
    keyword = _suggest_keyword(txn["description"])
    amount_abs = abs(txn["amount"])

    conditions = {
        "all": [
            {"field": "description", "op": "contains", "value": keyword},
            {"field": "account", "op": "equals", "value": ctx["account"]},
            {"field": "direction", "op": "equals", "value": ctx["direction"]},
            {
                "field": "amount_abs",
                "op": "between",
                "value": [round(amount_abs * 0.98), round(amount_abs * 1.02)],
            },
        ]
    }

    actions: dict = {}
    if txn["type"] != "unreviewed":
        actions["set_type"] = txn["type"]
    if len(splits) == 1 and splits[0]["category_id"] and not splits[0]["person_id"] and not splits[0]["is_splitwise"]:
        cat_label_by_id = {c["id"]: c["label"] for c in list_categories(conn)}
        actions["set_category"] = cat_label_by_id.get(splits[0]["category_id"])

    return {
        "name": f"Auto: {keyword.title()}",
        "conditions": conditions,
        "actions": actions,
        "priority": 100,
        "enabled": True,
        "stop_processing": False,
    }
