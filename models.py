from pydantic import BaseModel
from typing import Optional, Dict, Any

class LicenseActivationRequest(BaseModel):
    licenseKey: str

class LicenseActivationResponse(BaseModel):
    success: bool
    license: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    error: Optional[str] = None

class LicenseStatusResponse(BaseModel):
    hasLicense: bool
    status: str
    licenseKey: Optional[str] = None
    tier: Optional[str] = None
    expiresAt: Optional[str] = None
    maxCameras: Optional[int] = None
    maxUsers: Optional[int] = None
    inGracePeriod: Optional[bool] = False
    gracePeriodExpires: Optional[str] = None
    lastValidated: Optional[str] = None
    message: Optional[str] = None
    reason: Optional[str] = None

class LicenseValidationResponse(BaseModel):
    valid: bool
    license: Optional[Dict[str, Any]] = None
    inGracePeriod: Optional[bool] = False
    validUntil: Optional[str] = None
    gracePeriodExpires: Optional[str] = None
    reason: Optional[str] = None
    message: Optional[str] = None

class FeatureCheckRequest(BaseModel):
    featureKey: str

class FeatureCheckResponse(BaseModel):
    featureKey: str
    available: bool

class HeartbeatResponse(BaseModel):
    success: bool
    message: str

class HealthCheckResponse(BaseModel):
    status: str
    service: str
    version: str
    installationId: Optional[str] = None
    hardwareId: Optional[str] = None
