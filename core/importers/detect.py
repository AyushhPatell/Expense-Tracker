from pathlib import Path

import yaml

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"


def load_profiles() -> list[dict]:
    profiles = []
    for path in sorted(PROFILES_DIR.glob("*.yaml")):
        with open(path, "r", encoding="utf-8") as f:
            profiles.append(yaml.safe_load(f))
    return profiles


def detect_profile(headers: list[str]) -> list[dict]:
    """Return every profile whose expected headers are present in the given header row.

    A subset match (rather than exact-equality) so a bank can add extra columns
    (e.g. a running balance) without breaking detection.
    """
    header_set = {h.strip() for h in headers}
    matches = []
    for profile in load_profiles():
        expected = set(profile["detect"]["headers"])
        if expected.issubset(header_set):
            matches.append(profile)
    return matches
