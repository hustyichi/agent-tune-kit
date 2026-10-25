import re

ATK_ID_FIELD_HINTS = ("atk_id", "atkid")


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def detect_atk_id_field(fieldnames: list[str]) -> str:
    """Return the field name that holds the canonical ATK id, or empty when absent."""
    for field in fieldnames:
        if normalize_name(field) in ATK_ID_FIELD_HINTS:
            return field
    return ""


def detect_roles(fieldnames: list[str], role_candidates) -> dict[str, dict[str, str]]:
    normalized = {field: normalize_name(field) for field in fieldnames}
    roles: dict[str, dict[str, str]] = {}
    used: set[str] = set()
    for role, candidates in role_candidates.items():
        exact_candidates = [normalize_name(candidate) for candidate in candidates]
        chosen = ""
        for candidate in exact_candidates:
            for field, norm in normalized.items():
                if field not in used and norm == candidate:
                    chosen = field
                    break
            if chosen:
                break
        if not chosen:
            for candidate in exact_candidates:
                for field, norm in normalized.items():
                    if field not in used and candidate and candidate in norm:
                        chosen = field
                        break
                if chosen:
                    break
        if chosen:
            used.add(chosen)
            roles[role] = {"field": chosen, "source": "auto"}
        else:
            roles[role] = {"field": "", "source": "manual"}
    return roles
