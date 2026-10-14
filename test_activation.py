"""
The activation verdict (WS-RA RA-3): when a box counts as activated, when it
is in grace, and when it locks.

    DATABASE_URL=sqlite:///:memory: python -m pytest services/license-client/test_activation.py -q
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

import activation
from config import settings
from database import Base, LocalLicenseCache, SessionLocal, SystemConfig, engine

HW = "a" * 64
GRACE = timedelta(hours=settings.OFFLINE_GRACE_PERIOD_HOURS)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(activation, "get_hardware_fingerprint", lambda: HW)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


def licence(db, **kw):
    row = LocalLicenseCache(
        license_key="FOX-TEST",
        license_data=kw.pop("data", {"licenseKey": "FOX-TEST"}),
        valid_until=datetime.utcnow() + timedelta(days=30),
        license_signature="",
        last_validated_at=kw.pop("last_validated_at", datetime.utcnow()),
        **kw,
    )
    db.add(row)
    db.commit()
    return row


def test_no_licence(db):
    assert activation.activation_state(db) == {"activated": False, "reason": "not_activated"}


def test_fresh_licence_is_activated_and_records_hardware(db):
    licence(db)
    assert activation.activation_state(db) == {"activated": True, "inGrace": False}
    assert db.query(SystemConfig).filter_by(key="activated_hardware_id").one().value == HW


def test_other_hardware_is_refused(db):
    licence(db)
    activation.record_activation_hardware(db, "b" * 64)
    assert activation.activation_state(db)["reason"] == "hardware_changed"


def test_expired_licence_grace_then_lock(db):
    now = datetime.utcnow()
    licence(db, data={"expiresAt": (now - timedelta(hours=1)).isoformat() + "Z"})
    s = activation.activation_state(db)
    assert s["activated"] and s["inGrace"] and s["graceReason"] == "expired"

    db.query(LocalLicenseCache).delete()
    db.commit()
    licence(db, data={"expiresAt": (now - GRACE - timedelta(minutes=1)).isoformat()})
    s = activation.activation_state(db)
    assert not s["activated"] and s["reason"] == "expired"


def test_refusal_grace_then_lock(db):
    row = licence(db, is_valid=False, grace_period_started_at=datetime.utcnow() - timedelta(hours=1),
                  validation_error="License has been revoked")
    s = activation.activation_state(db)
    assert s["activated"] and s["graceReason"] == "refused"

    row.grace_period_started_at = datetime.utcnow() - GRACE - timedelta(minutes=1)
    db.commit()
    s = activation.activation_state(db)
    assert s == {**s, "activated": False, "reason": "refused", "message": "License has been revoked"}


def test_offline_window(db):
    row = licence(db, last_validated_at=datetime.utcnow() - timedelta(days=activation.OFFLINE_DAYS - 1))
    assert activation.activation_state(db) == {"activated": True, "inGrace": False}

    row.last_validated_at = datetime.utcnow() - timedelta(days=activation.OFFLINE_DAYS, hours=1)
    db.commit()
    s = activation.activation_state(db)
    assert s["activated"] and s["graceReason"] == "offline_too_long"

    row.last_validated_at = datetime.utcnow() - timedelta(days=activation.OFFLINE_DAYS) - GRACE - timedelta(hours=1)
    db.commit()
    assert activation.activation_state(db)["reason"] == "offline_too_long"


def test_earliest_lock_wins(db):
    now = datetime.utcnow()
    licence(db, data={"expiresAt": (now - timedelta(hours=2)).isoformat()},
            is_valid=False, grace_period_started_at=now - timedelta(hours=1))
    s = activation.activation_state(db)
    assert s["graceReason"] == "expired"
