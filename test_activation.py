"""
The activation verdict (WS-RA RA-3): when a box counts as activated, when it
is in grace, and when it locks. Leases are signed with a throwaway key swapped
in for the licence server's.

    DATABASE_URL=sqlite:///:memory: python -m pytest services/license-client/test_activation.py -q
    (with shared/licence_lease.py importable)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import activation
import licence_lease
from database import Base, LocalLicenseCache, SessionLocal, engine

HW = "a" * 64
LK = "FOX-TEST"
KEY = Ed25519PrivateKey.generate()
GRACE = timedelta(hours=72)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(activation, "get_hardware_fingerprint", lambda: HW)
    monkeypatch.setattr(licence_lease, "PUBLIC_KEY_PEM",
                        KEY.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def lease(exp_in: float = 86400, hwid: str = HW, lk: str = LK) -> str:
    claims = {"iss": licence_lease.ISSUER, "aud": licence_lease.AUDIENCE, "lk": hashlib.sha256(lk.encode()).hexdigest(),
              "hwid": hwid, "status": "active", "iat": int(time.time()), "exp": int(time.time() + exp_in)}
    head = b64(json.dumps({"alg": "EdDSA"}).encode())
    body = b64(json.dumps(claims).encode())
    return f"{head}.{body}.{b64(KEY.sign(f'{head}.{body}'.encode()))}"


def licence(db, with_lease=True, **kw):
    row = LocalLicenseCache(
        license_key=LK,
        license_data=kw.pop("data", {"licenseKey": LK}),
        valid_until=datetime.utcnow() + timedelta(days=30),
        license_signature="",
        last_validated_at=datetime.utcnow(),
        **kw,
    )
    db.add(row)
    db.commit()
    if with_lease:
        activation.store_lease(db, lease())
    return row


def test_no_licence(db):
    assert activation.activation_state(db) == {"activated": False, "reason": "not_activated"}


def test_licence_with_lease_is_activated(db):
    licence(db)
    assert activation.activation_state(db) == {"activated": True, "inGrace": False}


def test_licence_without_lease_is_not(db):
    """A cache row alone — e.g. written by hand — does not activate the box."""
    licence(db, with_lease=False)
    assert activation.activation_state(db)["reason"] == "no_lease"


def test_forged_or_foreign_leases(db):
    licence(db)
    activation.store_lease(db, lease(hwid="b" * 64))
    assert activation.activation_state(db)["reason"] == "hardware_changed"
    activation.store_lease(db, lease(lk="FOX-OTHER"))
    assert activation.activation_state(db)["reason"] == "lease_other_licence"
    other = Ed25519PrivateKey.generate()
    head, body, _ = lease().split(".")
    activation.store_lease(db, f"{head}.{body}.{b64(other.sign(f'{head}.{body}'.encode()))}")
    assert activation.activation_state(db)["reason"] == "lease_invalid"


def test_answer_without_lease_keeps_the_old_one(db):
    licence(db)
    activation.store_lease(db, None)
    assert activation.activation_state(db)["activated"]


def test_lease_grace_then_lock_offline(db):
    licence(db)
    activation.store_lease(db, lease(exp_in=-3600))
    s = activation.activation_state(db)
    assert s["activated"] and s["inGrace"] and s["graceReason"] == "offline_too_long"
    activation.store_lease(db, lease(exp_in=-GRACE.total_seconds() - 60))
    assert activation.activation_state(db)["reason"] == "offline_too_long"


def test_lease_ran_out_with_the_licence(db):
    past = (datetime.utcnow() - timedelta(days=4)).isoformat() + "Z"
    licence(db, data={"licenseKey": LK, "expiresAt": past})
    activation.store_lease(db, lease(exp_in=-4 * 86400))
    assert activation.activation_state(db)["reason"] == "expired"


def test_refusal_grace_then_lock(db):
    row = licence(db, is_valid=False, grace_period_started_at=datetime.utcnow() - timedelta(hours=1),
                  validation_error="License has been revoked")
    s = activation.activation_state(db)
    assert s["activated"] and s["graceReason"] == "refused"

    row.grace_period_started_at = datetime.utcnow() - GRACE - timedelta(minutes=1)
    db.commit()
    s = activation.activation_state(db)
    assert s == {**s, "activated": False, "reason": "refused", "message": "License has been revoked"}


def test_earliest_lock_wins(db):
    licence(db, is_valid=False, grace_period_started_at=datetime.utcnow() - timedelta(hours=1))
    activation.store_lease(db, lease(exp_in=-70 * 3600))     # lease locks in ~2 h
    s = activation.activation_state(db)
    assert s["graceReason"] == "offline_too_long"
