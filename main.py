from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database import get_db
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
    HeartbeatResponse,
    HealthCheckResponse
)

app = FastAPI(
    title="Foxsight License Client Service",
    description="On-premise license management for Foxsight Central Command VMS",
    version="1.0.0"
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
    return status

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

@app.post("/api/license/heartbeat", response_model=HeartbeatResponse)
async def send_heartbeat(db: Session = Depends(get_db)):
    """
    Manually trigger heartbeat to cloud service.

    Heartbeats are sent automatically every 4 hours, but can be
    triggered manually for testing or immediate sync.
    """
    client = LicenseClient(db)
    await client.send_heartbeat()
    return {"success": True, "message": "Heartbeat sent"}

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
