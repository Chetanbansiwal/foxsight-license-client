import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

import activation
from database import get_db, SessionLocal
from license_client import LicenseClient
from hardware_fingerprint import get_hardware_fingerprint
from config import settings
from models import (
    LicenseActivationRequest,
    LicenseActivationResponse,
    LicenseStatusResponse,
    LicenseValidationResponse,
    FeatureCheckRequest,
    FeatureCheckResponse,
    CheckLimitRequest,
    CheckLimitResponse,
    HeartbeatResponse,
    HealthCheckResponse
)

async def _keep_licence_current():
    """Revalidate and heartbeat on a timer, from startup.

    The activation gate counts time since the licence server last accepted
    this box, so something has to keep asking — including on a box nobody
    opens the UI of. (A scheduler used to be started by activation, inside a
    per-request object, so it never survived a restart.)
    """
    # Soon after startup: a box updated from before RA-3 may have no recent
    # contact on record, and stays locked until the first validation lands.
    await asyncio.sleep(10)
    while True:
        db = SessionLocal()
        delay = settings.HEARTBEAT_INTERVAL_HOURS * 3600
        try:
            client = LicenseClient(db)
            if client._get_cached_license():
                await client.validate_license()
                await client.send_heartbeat()
                # No lease yet (a box updated from before leases, or the
                # licence server was unreachable): the box stays locked until
                # one arrives, so ask again soon.
                if not activation.current_lease(db):
                    delay = 60
        except Exception as e:
            print(f"Background licence check failed: {e}")
            delay = 60
        finally:
            db.close()
        await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_keep_licence_current())
    yield
    task.cancel()


app = FastAPI(
    title="Foxsight License Client Service",
    description="On-premise license management for Foxsight Central Command VMS",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Endpoints
@app.post("/api/license/activate", response_model=LicenseActivationResponse)
async def activate_license(
    request: LicenseActivationRequest,
    db: Session = Depends(get_db)
):
    """
    Activate license with cloud service.

    This endpoint:
    1. Sends activation request to cloud License Server
    2. Stores license data in local cache
    3. Syncs feature flags
    4. Starts heartbeat scheduler
    """
    client = LicenseClient(db)
    result = await client.activate_license(request.licenseKey)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    return result

@app.get("/api/license/status", response_model=LicenseStatusResponse)
async def get_license_status(db: Session = Depends(get_db)):
    """
    Get current license status.

    Returns license information, validity status, grace period info,
    and feature limits (cameras, users, etc.).
    """
    client = LicenseClient(db)
    status = await client.get_license_status()
    # The gateway's verdict, so the UI shows the activation screen exactly
    # when the API refuses.
    state = activation.activation_state(db)
    status["activated"] = state["activated"]
    status["activationReason"] = state.get("reason") or state.get("graceReason")
    status["locksAt"] = state.get("locksAt") or state.get("lockedAt")
    return status


@app.get("/api/license/state")
def get_activation_state(db: Session = Depends(get_db)):
    """Whether this box may be used (WS-RA RA-3). Local only, no cloud call:
    the API gateway asks on every request. Carries the signed lease, which the
    gateway and the recording service verify themselves rather than trusting
    this answer."""
    state = activation.activation_state(db)
    lease = activation.current_lease(db)
    if lease:
        state["lease"] = lease
    return state


class EmailActivationRequest(BaseModel):
    email: str


def _activation_http_error(e: activation.ActivationError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.message)


@app.post("/api/license/activation")
async def start_email_activation(request: EmailActivationRequest, db: Session = Depends(get_db)):
    """Activate with an email: the licence server emails a confirmation link,
    then GET /api/license/activation completes the activation."""
    try:
        return await activation.start_email_activation(db, request.email)
    except activation.ActivationError as e:
        raise _activation_http_error(e)


@app.get("/api/license/activation")
async def poll_email_activation(db: Session = Depends(get_db)):
    """Where the email activation stands; activates once the link is clicked."""
    client = LicenseClient(db)
    try:
        return await activation.poll_email_activation(db, client.activate_license)
    except activation.ActivationError as e:
        raise _activation_http_error(e)


@app.delete("/api/license/activation")
def cancel_email_activation(db: Session = Depends(get_db)):
    activation.cancel_email_activation(db)
    return {"state": "none"}

@app.post("/api/license/validate", response_model=LicenseValidationResponse)
async def validate_license(db: Session = Depends(get_db)):
    """
    Validate license with cloud service.

    Attempts to validate with cloud. If offline, checks grace period.
    Returns validation status and grace period information.
    """
    client = LicenseClient(db)
    result = await client.validate_license()
    return result

@app.post("/api/license/feature/check", response_model=FeatureCheckResponse)
async def check_feature(
    request: FeatureCheckRequest,
    db: Session = Depends(get_db)
):
    """
    Check if a feature is available based on license.

    Used by API Gateway to enforce feature gating.
    Core features are always available even without license.
    """
    client = LicenseClient(db)
    available = await client.is_feature_available(request.featureKey)
    return {"featureKey": request.featureKey, "available": available}

@app.post("/api/license/check-limit", response_model=CheckLimitResponse)
async def check_limit(
    request: CheckLimitRequest,
    db: Session = Depends(get_db)
):
    """
    Check if adding one more resource would exceed license limits.

    Used by camera-management and user-management services before creating resources.
    Accepts limitType ("cameras" or "users") and currentCount.
    """
    client = LicenseClient(db)
    result = client.check_limit(request.limitType, request.currentCount)

    if not result["allowed"]:
        raise HTTPException(status_code=403, detail=result["message"])

    return result

@app.post("/api/license/heartbeat", response_model=HeartbeatResponse)
async def send_heartbeat(db: Session = Depends(get_db)):
    """
    Manually trigger heartbeat to cloud service.

    Heartbeats are sent automatically every 4 hours, but can be
    triggered manually for testing or immediate sync.
    """
    client = LicenseClient(db)
    # The licence server's verdict, not just "sent" (it used to report success
    # for heartbeats the server refused).
    return await client.send_heartbeat()

# ---------------------------------------------------------------------------
# Registry-token accessor.
#
# install.sh runs the device-code activation directly against the cloud (no
# license-client involved), seeds REGISTRY_TOKEN/USERNAME/EXPIRES_AT into our
# env via .env, and we hand them out to update.sh on demand. Keeping this in
# one place means update.sh doesn't need to know where the credentials live.
# ---------------------------------------------------------------------------

import device_code_client


@app.get("/api/license/registry-token")
async def device_code_registry_token(force: bool = False, db: Session = Depends(get_db)):
    """Returns the cached registry credentials update.sh needs to docker-login
    and pull updated images.

    Self-healing: if the cached token is missing, expired, or the caller passes
    ?force=1 (e.g. after a docker-login failure), we re-fetch a fresh token
    from the cloud so a REGISTRY_DEPLOY_TOKEN rotation on the license-server
    propagates here automatically. On a cloud error we fall back to whatever is
    cached rather than hard-failing.
    """
    token = device_code_client.get_registry_token(db)
    if force or token is None or token.get("expired"):
        try:
            fresh = await LicenseClient(db).refresh_registry_token()
        except Exception:
            fresh = None
        if fresh and fresh.get("token"):
            token = fresh
    if not token:
        raise HTTPException(status_code=404, detail="No registry token cached")
    return token


@app.get("/health", response_model=HealthCheckResponse)
async def health_check(db: Session = Depends(get_db)):
    """
    Health check endpoint for container orchestration.

    Returns service status and system information.
    """
    client = LicenseClient(db)
    return {
        "status": "healthy",
        "service": "license-client",
        "version": "1.0.0",
        "installationId": client.installation_id,
        "hardwareId": client.hardware_id
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
