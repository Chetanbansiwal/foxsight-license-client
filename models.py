from pydantic import BaseModel
from typing import Optional, Dict, Any, List

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
    # NULL = unlimited (per license_tiers.max_organizations semantics).
    maxOrganizations: Optional[int] = None
    # Feature keys enabled on the active license tier. Source of truth for
    # the central-command web-client's feature-gating layer (router meta,
    # sidebar lock icons, useCan composable). Empty list means a license is
    # active but the tier has no features — that's still distinct from
    # `enabledFeatures = None`, which means we don't yet know (no license
    # cached / status load failed).
    enabledFeatures: Optional[List[str]] = None
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

class CheckLimitRequest(BaseModel):
    limitType: str  # "cameras" or "users"
    currentCount: int

class CheckLimitResponse(BaseModel):
    allowed: bool
    limitType: str
    currentCount: int
    maxAllowed: Optional[int] = None
    message: Optional[str] = None

class HealthCheckResponse(BaseModel):
    status: str
    service: str
    version: str
    installationId: Optional[str] = None
    hardwareId: Optional[str] = None
