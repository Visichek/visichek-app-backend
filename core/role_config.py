from __future__ import annotations

from collections.abc import Sequence

from limits import parse as parse_rate


DEFAULT_ANONYMOUS_RATE = "20/minute"
DEFAULT_ROLE_RATE = "80/minute"
DEFAULT_ADMIN_RATE = "140/minute"
DEFAULT_RECEPTIONIST_RATE = "120/minute"
DEFAULT_DEPT_ADMIN_RATE = "100/minute"
DEFAULT_SUPER_ADMIN_RATE = "140/minute"
DEFAULT_AUDITOR_RATE = "80/minute"
DEFAULT_SECURITY_OFFICER_RATE = "80/minute"
DEFAULT_DPO_RATE = "80/minute"

LEGACY_ROLE_ALIASES = {"member": "user"}

SYSTEM_USER_ROLE_DEFAULTS = {
    "receptionist": DEFAULT_RECEPTIONIST_RATE,
    "dept_admin": DEFAULT_DEPT_ADMIN_RATE,
    "super_admin": DEFAULT_SUPER_ADMIN_RATE,
    "auditor": DEFAULT_AUDITOR_RATE,
    "security_officer": DEFAULT_SECURITY_OFFICER_RATE,
    "dpo": DEFAULT_DPO_RATE,
}


def normalize_role(role: str | None) -> str:
    value = (role or "anonymous").strip().lower() or "anonymous"
    return LEGACY_ROLE_ALIASES.get(value, value)


def build_role_rate_limits_csv(non_admin_roles: Sequence[str], include_admin: bool = True) -> str:
    entries = [f"anonymous:{DEFAULT_ANONYMOUS_RATE}"]
    entries.extend(f"{normalize_role(role)}:{DEFAULT_ROLE_RATE}" for role in non_admin_roles)
    if include_admin:
        entries.append(f"admin:{DEFAULT_ADMIN_RATE}")
    return ",".join(entries)


def parse_role_rate_limits(raw: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not raw:
        return parsed

    for entry in raw.split(","):
        value = entry.strip()
        if not value or ":" not in value:
            continue
        role, limit = value.split(":", 1)
        role_key = normalize_role(role)
        limit_value = limit.strip()
        if role_key and limit_value:
            parsed[role_key] = limit_value

    return parsed


def build_role_rate_limits(raw: str | None, *, fallback_csv: str):
    configured = parse_role_rate_limits(raw)
    fallback = parse_role_rate_limits(fallback_csv)

    selected = configured or fallback
    if "anonymous" not in selected:
        selected["anonymous"] = DEFAULT_ANONYMOUS_RATE
    if "admin" not in selected:
        selected["admin"] = DEFAULT_ADMIN_RATE

    final_limits = {}
    for role, rule in selected.items():
        try:
            final_limits[role] = parse_rate(rule)
        except Exception:
            continue

    if "anonymous" not in final_limits:
        final_limits["anonymous"] = parse_rate(DEFAULT_ANONYMOUS_RATE)

    # Ensure VisiChek system user roles have defaults
    for role, default_rate in SYSTEM_USER_ROLE_DEFAULTS.items():
        if role not in final_limits:
            try:
                final_limits[role] = parse_rate(default_rate)
            except Exception:
                continue

    return final_limits
