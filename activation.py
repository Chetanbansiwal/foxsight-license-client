"""Activation: whether this box may be used, and the in-app email activation.

WS-RA RA-3 (REGISTRY_ACCESS_SPEC.md §6, §10). The API gateway refuses everything
but login, licence and health routes until `activation_state()` says the box is
activated, so this verdict is what makes "use requires an email" true on a
running box.

The verdict rests on the licence server's signed lease (shared/licence_lease.py):
a box is activated only while it holds a genuine lease for its cached licence
and this machine's hardware, unexpired or within its 72 h grace. The licence
server issues a lease on every activation and successful validation (at most 30
days, never past the licence's expiry), so a site that is offline keeps working
for that long, and a hand-edited cache cannot fake an activation. On top of the
lease, a refusal by the licence server (revoked, deactivated) locks the box
72 h after it is first seen, without waiting for the lease to run out.

Computed locally — no call to the licence server — because the gateway asks on
every request (cached there for a few seconds). The background validator in
main.py keeps the lease fresh.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx
from sqlalchemy.orm import Session

import licence_lease
from config import settings
from database import LocalLicenseCache, SystemConfig
from hardware_fingerprint import get_hardware_fingerprint

_LEASE_KEY = "licence_lease"
_PENDING_KEY = "pending_activation"


def _get_kv(db: Session, key: str) -> Optional[str]:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row else None


def _set_kv(db: Session, key: str, value: Optional[str]) -> None:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if value is None:
        if row:
            db.delete(row)
    elif row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))
    db.commit()


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def store_lease(db: Session, token: Optional[str]) -> None:
    """Keep the lease from an activation or validation answer. An answer
    without one (a licence server with no signing key) keeps the old lease."""
    if token:
        _set_kv(db, _LEASE_KEY, token)


def current_lease(db: Session) -> Optional[str]:
    return _get_kv(db, _LEASE_KEY)


def activation_state(db: Session) -> Dict[str, Any]:
    """The box's activation verdict, from the local cache alone."""
    cached = db.query(LocalLicenseCache).first()
    if not cached:
        return {"activated": False, "reason": "not_activated"}

    now = datetime.utcnow()
    data = cached.license_data if isinstance(cached.license_data, dict) else {}
    expires = _parse_time(data.get("expiresAt"))

    lease = licence_lease.check(current_lease(db), licence_key=cached.license_key,
                                hardware_id=get_hardware_fingerprint())
    if not lease["activated"]:
        reason = lease["reason"]
        if reason == "lease_expired":
            # Ran out because the licence expired, or because the box has not
            # reached the licence server for the lease's lifetime.
            reason = "expired" if expires and now >= expires else "offline_too_long"
        out = {"activated": False, "reason": reason}
        if lease.get("lockedAt"):
            out["lockedAt"] = lease["lockedAt"]
        return out

    # A refusal by the licence server locks after the grace period, even with
    # an unexpired lease (the lease was issued before the refusal).
    grace = timedelta(hours=settings.OFFLINE_GRACE_PERIOD_HOURS)
    if cached.is_valid is False and cached.grace_period_started_at:
        locks_at = cached.grace_period_started_at + grace
        if now >= locks_at:
            out = {"activated": False, "reason": "refused", "lockedAt": locks_at.isoformat() + "Z"}
            if cached.validation_error:
                out["message"] = cached.validation_error
            return out
        if not lease["inGrace"] or locks_at < _parse_time(lease["locksAt"]):
            return {"activated": True, "inGrace": True, "graceReason": "refused",
                    "locksAt": locks_at.isoformat() + "Z"}

    if lease["inGrace"]:
        return {"activated": True, "inGrace": True,
                "graceReason": "expired" if expires and now >= expires else "offline_too_long",
                "locksAt": lease["locksAt"]}
    return {"activated": True, "inGrace": False}


# ---------------------------------------------------------------- email activation
#
# The same device-code flow the installer used, driven from the box's UI: the
# administrator enters an email, the licence server emails a confirmation link,
# and the box polls until the link is clicked, then activates the licence it is
# given. The device code stays on the box (SystemConfig), never in the browser.

class ActivationError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _cloud_error(resp: httpx.Response) -> ActivationError:
    try:
        body = resp.json()
        message = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) \
            else body.get("error") or body.get("message")
    except ValueError:
        message = None
    status = resp.status_code if resp.status_code in (400, 404, 409, 429) else 502
    return ActivationError(status, message or f"Licence server answered HTTP {resp.status_code}")


def _pending(db: Session) -> Optional[Dict[str, Any]]:
    raw = _get_kv(db, _PENDING_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _public(pending: Dict[str, Any], state: str) -> Dict[str, Any]:
    out = {"state": state, "email": pending.get("email"), "userCode": pending.get("userCode"),
           "expiresAt": pending.get("expiresAt")}
    if pending.get("devConfirmUrl"):
        # Only a non-production licence server returns it (no real mailbox in dev).
        out["devConfirmUrl"] = pending["devConfirmUrl"]
    return out


async def start_email_activation(db: Session, email: str) -> Dict[str, Any]:
    email = (email or "").strip()
    if not email or "@" not in email:
        raise ActivationError(400, "Enter a valid email address")
    base = settings.LICENSE_API_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.LICENSE_API_TIMEOUT) as client:
        try:
            resp = await client.post(f"{base}/license/device-code/start",
                                     json={"hardwareId": get_hardware_fingerprint()})
        except httpx.HTTPError as e:
            raise ActivationError(502, f"Could not reach the licence server: {e}")
        if resp.status_code >= 300:
            raise _cloud_error(resp)
        started = (resp.json() or {}).get("data") or {}
        if not started.get("deviceCode") or not started.get("userCode"):
            raise ActivationError(502, "The licence server's reply had no activation code")

        try:
            resp = await client.post(f"{base}/license/device-code/verify",
                                     json={"userCode": started["userCode"], "email": email})
        except httpx.HTTPError as e:
            raise ActivationError(502, f"Could not reach the licence server: {e}")
        if resp.status_code >= 300:
            raise _cloud_error(resp)
        verified = (resp.json() or {}).get("data") or {}

    pending = {
        "deviceCode": started["deviceCode"],
        "userCode": started["userCode"],
        "email": email.lower(),
        "expiresAt": (datetime.utcnow() + timedelta(seconds=int(started.get("expiresIn") or 900))).isoformat() + "Z",
    }
    if verified.get("devConfirmUrl"):
        pending["devConfirmUrl"] = verified["devConfirmUrl"]
    _set_kv(db, _PENDING_KEY, json.dumps(pending))
    return _public(pending, "email_sent")


def cancel_email_activation(db: Session) -> None:
    _set_kv(db, _PENDING_KEY, None)


async def poll_email_activation(db: Session, activate) -> Dict[str, Any]:
    """Where the email activation stands. Once the link is clicked, activates
    the issued licence through `activate(license_key)` (LicenseClient)."""
    pending = _pending(db)
    if not pending:
        return {"state": "none"}

    base = settings.LICENSE_API_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.LICENSE_API_TIMEOUT) as client:
            resp = await client.post(f"{base}/license/device-code/poll",
                                     json={"deviceCode": pending["deviceCode"]})
    except httpx.HTTPError:
        # Transient: keep waiting.
        return _public(pending, "email_sent")
    if resp.status_code == 404:
        cancel_email_activation(db)
        return {"state": "expired"}
    if resp.status_code >= 300:
        return _public(pending, "email_sent")

    data = (resp.json() or {}).get("data") or {}
    status = data.get("status")
    if status == "expired":
        cancel_email_activation(db)
        return {"state": "expired", "email": pending.get("email")}
    if status != "complete":
        # pending / email_sent / verified (verified completes on the next poll)
        return _public(pending, "email_sent")

    licence_key = (data.get("license") or {}).get("licenseKey")
    if not licence_key:
        return _public(pending, "email_sent")
    # The poll also carries the shared registry key for old installers; it is
    # not used — the box logs in to registry.foxsight.ai with its licence.
    result = await activate(licence_key)
    if not result.get("success"):
        raise ActivationError(502, result.get("error") or "Activation failed")
    cancel_email_activation(db)
    return {"state": "activated", "email": pending.get("email")}
