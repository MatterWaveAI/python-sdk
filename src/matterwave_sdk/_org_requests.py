"""The requests the organisation clients make: the org, its members,
invites and join links, its policies, domains and identity providers, its
billing administration, and the device-login decisions. Pure, like
`_native`: each function returns the `Request` a method sends.

Every route here except the two lookups gates on the caller's membership
row, so it takes a person's token and refuses an API key.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._base import Request, _segment
from ._native import _email_address

ROLES = ("owner", "admin", "member", "viewer")


def _role(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}, got {role!r}")
    return role


def _bounded(name: str, value: int, low: int, high: int) -> int:
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low} to {high}, got {value}")
    return value


def _token(token: str) -> dict[str, str]:
    if not token or len(token) > 256:
        raise ValueError("token must be the invite's token, 1 to 256 characters")
    return {"token": token}


def _get(path: str) -> Request:
    return Request("GET", path, None, keyed=True, stream=False)


# --- the org -------------------------------------------------------------------


def get_org_request() -> Request:
    return _get("/api/org")


def rename_org_request(name: str) -> Request:
    value = name.strip()
    if not value or len(value) > 120:
        raise ValueError("an org name is 1 to 120 characters")
    return Request("PATCH", "/api/org", {"name": value}, keyed=True, stream=False)


def close_org_request(confirm: str) -> Request:
    return Request("POST", "/api/org/close", {"confirm": confirm}, keyed=True, stream=False)


# --- members -------------------------------------------------------------------


def _member_path(subject: str, suffix: str = "") -> str:
    return f"/api/org/members/{_segment('subject', subject)}{suffix}"


def list_members_request() -> Request:
    return _get("/api/org/members")


def remove_member_request(subject: str) -> Request:
    return Request("DELETE", _member_path(subject), None, keyed=True, stream=False)


def set_member_role_request(subject: str, role: str) -> Request:
    body = {"role": _role(role)}
    return Request("PUT", _member_path(subject, "/role"), body, keyed=True, stream=False)


def transfer_ownership_request(subject: str) -> Request:
    path = _member_path(subject, "/transfer-ownership")
    return Request("POST", path, None, keyed=True, stream=False)


# --- invites -------------------------------------------------------------------


def _invite_path(invite_id: str, suffix: str = "") -> str:
    return f"/api/org/invites/{_segment('invite_id', invite_id)}{suffix}"


def list_invites_request() -> Request:
    return _get("/api/org/invites")


def create_invite_request(email: str, role: str) -> Request:
    """The address lowercased and shape-checked the way signup reads it, so
    a typo is refused before an invite is mailed."""
    body = {"email": _email_address(email), "role": _role(role)}
    return Request("POST", "/api/org/invites", body, keyed=True, stream=False)


def revoke_invite_request(invite_id: str) -> Request:
    return Request("DELETE", _invite_path(invite_id), None, keyed=True, stream=False)


def resend_invite_request(invite_id: str) -> Request:
    return Request("POST", _invite_path(invite_id, "/resend"), None, keyed=True, stream=False)


def lookup_invite_request(token: str) -> Request:
    """Anonymous: the invite's token is the whole credential."""
    return Request("POST", "/api/org/invites/lookup", _token(token), keyed=False, stream=False)


def accept_invite_request(token: str) -> Request:
    return Request("POST", "/api/org/invites/accept", _token(token), keyed=True, stream=False)


# --- invite links --------------------------------------------------------------


def list_invite_links_request() -> Request:
    return _get("/api/org/invite-links")


def create_invite_link_request(role: str, max_uses: int, expires_in_days: int) -> Request:
    if _role(role) == "owner":
        raise ValueError("ownership is never granted by link; invite an owner by email")
    body = {
        "role": role,
        "max_uses": _bounded("max_uses", max_uses, 1, 500),
        "expires_in_days": _bounded("expires_in_days", expires_in_days, 1, 30),
    }
    return Request("POST", "/api/org/invite-links", body, keyed=True, stream=False)


def revoke_invite_link_request(link_id: str) -> Request:
    path = f"/api/org/invite-links/{_segment('link_id', link_id)}"
    return Request("DELETE", path, None, keyed=True, stream=False)


def lookup_link_request(token: str) -> Request:
    """Anonymous: the link's token is the whole credential."""
    return Request("POST", "/api/join/lookup", _token(token), keyed=False, stream=False)


def join_request(token: str) -> Request:
    return Request("POST", "/api/join", _token(token), keyed=True, stream=False)


# --- policies ------------------------------------------------------------------


def get_model_policy_request() -> Request:
    return _get("/api/org/models")


def put_model_policy_request(allowed: Sequence[str] | None) -> Request:
    """`None` allows every model the plan does; a list allows exactly those
    (the house model is always added back)."""
    if allowed is not None and not allowed:
        raise ValueError("allowed must name at least one model, or None to lift the restriction")
    if allowed is not None and len(allowed) > 200:
        raise ValueError("allow at most 200 models")
    body: dict[str, Any] = {"allowed": list(allowed) if allowed is not None else None}
    return Request("PUT", "/api/org/models", body, keyed=True, stream=False)


def get_sharing_request() -> Request:
    return _get("/api/org/sharing")


def put_sharing_request(enabled: bool) -> Request:
    return Request("PUT", "/api/org/sharing", {"enabled": enabled}, keyed=True, stream=False)


def list_org_shares_request() -> Request:
    return _get("/api/org/shares")


def revoke_org_share_request(share_id: str) -> Request:
    path = f"/api/org/shares/{_segment('share_id', share_id)}"
    return Request("DELETE", path, None, keyed=True, stream=False)


def get_sso_request() -> Request:
    return _get("/api/org/sso")


def put_sso_request(required: bool) -> Request:
    return Request("PUT", "/api/org/sso", {"sso_required": required}, keyed=True, stream=False)


def mint_recovery_code_request() -> Request:
    return Request("POST", "/api/org/sso/recovery-code", None, keyed=True, stream=False)


def get_mfa_request() -> Request:
    return _get("/api/org/mfa")


def put_mfa_request(required: bool) -> Request:
    return Request("PUT", "/api/org/mfa", {"mfa_required": required}, keyed=True, stream=False)


# --- domains and identity providers --------------------------------------------


def _domain_path(domain: str, suffix: str = "") -> str:
    return f"/api/org/domains/{_segment('domain', domain)}{suffix}"


def list_domains_request() -> Request:
    return _get("/api/org/domains")


def _domain(domain: str) -> str:
    """Lowercased, trimmed of spaces and the root's trailing dot, as the
    route stores it; a value with no dot or a blank label is refused."""
    value = domain.strip().lower().rstrip(".")
    labels = value.split(".")
    if len(labels) < 2 or not all(labels) or any(" " in label for label in labels):
        raise ValueError(f"not a domain like example.com: {domain!r}")
    if len(value) > 253:
        raise ValueError("a domain is 1 to 253 characters, like example.com")
    return value


def claim_domain_request(domain: str) -> Request:
    return Request(
        "POST", "/api/org/domains", {"domain": _domain(domain)}, keyed=True, stream=False
    )


def release_domain_request(domain: str) -> Request:
    return Request("DELETE", _domain_path(domain), None, keyed=True, stream=False)


def domain_challenge_request(domain: str) -> Request:
    return _get(_domain_path(domain, "/challenge"))


def verify_domain_request(domain: str) -> Request:
    return Request("POST", _domain_path(domain, "/verify"), None, keyed=True, stream=False)


def list_idps_request() -> Request:
    return _get("/api/org/idp")


def attach_idp_request(
    *,
    name: str,
    issuer: str,
    client_id: str,
    client_secret: str,
    scopes: Sequence[str] | None,
) -> Request:
    """An OIDC provider. The secret is sent once and never read back. A
    plaintext issuer is refused here (no provider accepts one) and the
    trailing slash is trimmed, as the route's own validator does."""
    if not name.strip():
        raise ValueError("name must not be blank")
    if not issuer.strip().startswith("https://"):
        raise ValueError("issuer must be an https:// URL")
    if not client_id.strip():
        raise ValueError("client_id must not be blank")
    if not client_secret.strip():
        raise ValueError("client_secret must not be blank")
    body: dict[str, Any] = {
        "kind": "oidc",
        "name": name.strip(),
        "issuer": issuer.strip().rstrip("/"),
        "client_id": client_id.strip(),
        "client_secret": client_secret,
    }
    if scopes is not None:
        body["scopes"] = list(scopes)
    return Request("POST", "/api/org/idp", body, keyed=True, stream=False)


def detach_idp_request(provider: str) -> Request:
    path = f"/api/org/idp/{_segment('provider', provider)}"
    return Request("DELETE", path, None, keyed=True, stream=False)


# --- billing administration ----------------------------------------------------


def checkout_request(plan: str, annual: bool) -> Request:
    if not plan:
        raise ValueError("plan must be a plan code, such as `pro`")
    body = {"plan": plan, "annual": annual}
    return Request("POST", "/api/billing/checkout", body, keyed=True, stream=False)


def portal_request() -> Request:
    return Request("POST", "/api/billing/portal", None, keyed=True, stream=False)


def invoices_request() -> Request:
    return _get("/api/billing/invoices")


def put_plan_request(plan: str, reason: str | None) -> Request:
    if not plan or len(plan) > 64:
        raise ValueError("plan must be a plan code, such as `free`")
    body: dict[str, Any] = {"plan": plan}
    if reason is not None:
        if len(reason) > 500:
            raise ValueError("reason must be 500 characters or fewer")
        body["reason"] = reason
    return Request("PUT", "/api/plan", body, keyed=True, stream=False)


def plan_history_request() -> Request:
    return _get("/api/plan/history")


# --- device login decisions ----------------------------------------------------


def _decide(path: str, user_code: str, name: str | None = None) -> Request:
    if not user_code or len(user_code) > 32:
        raise ValueError("user_code must be the code the device shows")
    body: dict[str, Any] = {"user_code": user_code}
    if name is not None:
        body["name"] = name
    return Request("POST", path, body, keyed=True, stream=False)


def device_lookup_request(user_code: str) -> Request:
    return _decide("/api/auth/device/lookup", user_code)


def device_approve_request(user_code: str, name: str | None) -> Request:
    return _decide("/api/auth/device/approve", user_code, name)


def device_deny_request(user_code: str) -> Request:
    return _decide("/api/auth/device/deny", user_code)
