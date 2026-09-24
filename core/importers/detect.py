from pathlib import Path

import yaml

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"


def load_profiles() -> list[dict]:
    """Built-in profiles shipped in the repo (core/importers/profiles/*.yaml)."""
    profiles = []
    for path in sorted(PROFILES_DIR.glob("*.yaml")):
        with open(path, "r", encoding="utf-8") as f:
            profiles.append(yaml.safe_load(f))
    return profiles


def load_all_profiles(conn) -> list[dict]:
    """Built-in profiles plus this device's own custom ones (Settings > Add
    a bank profile) — the full set of CSV shapes this install understands.
    Everywhere a profile needs to be looked up should use this, not
    load_profiles() alone, so a self-service profile works everywhere a
    built-in one does."""
    from core.importers.custom_profiles import list_custom_profiles

    return load_profiles() + list_custom_profiles(conn)


def detect_profile(headers: list[str], profiles: list[dict] | None = None) -> list[dict]:
    """Return every profile whose expected headers are present in the given header row.

    A subset match (rather than exact-equality) so a bank can add extra columns
    (e.g. a running balance) without breaking detection. Pass `profiles` (e.g.
    from load_all_profiles) to include self-service ones; omitting it falls
    back to built-in-only, for callers that don't have a db connection handy.
    """
    header_set = {h.strip() for h in headers}
    matches = []
    for profile in profiles if profiles is not None else load_profiles():
        expected = set(profile["detect"]["headers"])
        if expected.issubset(header_set):
            matches.append(profile)
    return matches
