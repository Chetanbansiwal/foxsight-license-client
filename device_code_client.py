"""Registry-token cache for the on-prem stack.

install.sh on the customer box runs the device-code activation directly
against the cloud — license-client is not involved in the bootstrap. Once
activation succeeds, install.sh writes the issued registry credentials into
.env (REGISTRY_TOKEN, REGISTRY_USERNAME, REGISTRY_TOKEN_EXPIRES_AT). compose
passes those through to this service as env vars.

This module's only remaining job is to hand those credentials back out to
`foxsight update` (update.sh), which calls /api/license/registry-token to get
a token for `docker login ghcr.io` before pulling fresh images.

We persist into SystemConfig on first read so subsequent calls don't have to
re-parse env vars, and so that a future "rotate token" path can overwrite the
cached value without touching .env.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from database import SystemConfig


_RT_KEY = "registry_token_json"


def _set_kv(db: Session, key: str, value: str) -> None:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        row = SystemConfig(key=key, value=value)
        db.add(row)
    db.commit()


def _get_kv(db: Session, key: str) -> Optional[str]:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row else None


def _seed_from_env(db: Session) -> Optional[Dict[str, Any]]:
    """If REGISTRY_TOKEN/USERNAME/EXPIRES_AT are set in env, persist them and
    return the resulting dict. Otherwise return None."""
    token = os.environ.get("REGISTRY_TOKEN", "").strip()
    username = os.environ.get("REGISTRY_USERNAME", "").strip()
    if not token or not username:
        return None
    payload = {
        "username": username,
        "token": token,
        "expiresAt": os.environ.get("REGISTRY_TOKEN_EXPIRES_AT", "").strip(),
    }
    _set_kv(db, _RT_KEY, json.dumps(payload))
    return payload


def get_registry_token(db: Session) -> Optional[Dict[str, Any]]:
    """Return the cached registry token. Falls back to env on first read."""
    raw = _get_kv(db, _RT_KEY)
    if not raw:
        seeded = _seed_from_env(db)
        if not seeded:
            return None
        token = seeded
    else:
        try:
            token = json.loads(raw)
        except json.JSONDecodeError:
            return None
    expires_at = token.get("expiresAt")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp < datetime.now(exp.tzinfo):
                token["expired"] = True
        except ValueError:
            pass
    return token
