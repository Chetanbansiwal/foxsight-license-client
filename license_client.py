import httpx
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import LocalLicenseCache, LocalLicenseValidationAttempt, LocalFeatureFlag, SystemConfig
from hardware_fingerprint import get_hardware_fingerprint, get_system_info
from config import settings

class LicenseClient:
    def __init__(self, db: Session):
        self.db = db
        self.cloud_api_url = settings.LICENSE_API_URL
        self.installation_id = self._get_or_create_installation_id()
        self.hardware_id = get_hardware_fingerprint()
        self.scheduler = AsyncIOScheduler()

    def _get_or_create_installation_id(self) -> str:
        """Get or generate unique installation ID."""
        config = self.db.query(SystemConfig).filter(
            SystemConfig.key == "installation_id"
        ).first()

        if config:
            return config.value

        # Generate new UUID
        new_id = str(uuid.uuid4())
        config = SystemConfig(key="installation_id", value=new_id)
        self.db.add(config)
        self.db.commit()

        return new_id

    async def activate_license(self, license_key: str) -> Dict[str, Any]:
        """
        Activate license with cloud service.
        """
        try:
            async with httpx.AsyncClient(timeout=settings.LICENSE_API_TIMEOUT) as client:
                response = await client.post(
                    f"{self.cloud_api_url}/licenses/activate",
                    json={
                        "licenseKey": license_key,
                        "hardwareId": self.hardware_id,
                        "installationId": self.installation_id,
                        "installationName": settings.INSTALLATION_NAME,
                        "installationVersion": settings.APP_VERSION
                    },
                    headers={
                        "Content-Type": "application/json",
                        "X-Installation-ID": self.installation_id
                    }
                )

                response.raise_for_status()
                data = response.json()

                if data.get("success"):
                    # Store in local cache
                    await self._store_license_cache(data["data"])

                    # Sync feature flags
                    await self._sync_feature_flags(data["data"])

                    # Start heartbeat
                    self._start_heartbeat()

                    # Log success
                    self._log_validation_attempt(license_key, "success", None)

                    return {
                        "success": True,
                        "license": data["data"],
                        "message": "License activated successfully"
                    }
                else:
                    return {
                        "success": False,
                        "error": data.get("error", {}).get("message", "Activation failed")
                    }

        except httpx.HTTPError as e:
            error_msg = f"HTTP error during activation: {str(e)}"
            self._log_validation_attempt(license_key, "failed", error_msg)
            return {
                "success": False,
                "error": error_msg
            }
        except Exception as e:
            error_msg = f"Activation failed: {str(e)}"
            self._log_validation_attempt(license_key, "failed", error_msg)
            return {
                "success": False,
                "error": error_msg
            }

    async def validate_license(self) -> Dict[str, Any]:
        """
        Validate license with cloud service.
        """
        cached_license = self._get_cached_license()

        if not cached_license:
            return {"valid": False, "reason": "no_license"}

        try:
            async with httpx.AsyncClient(timeout=settings.LICENSE_API_TIMEOUT) as client:
                response = await client.post(
                    f"{self.cloud_api_url}/licenses/validate",
                    json={
                        "licenseKey": cached_license.license_key,
                        "hardwareId": self.hardware_id
                    },
                    headers={
                        "Content-Type": "application/json",
                        "X-Installation-ID": self.installation_id
                    }
                )

                response.raise_for_status()
                data = response.json()

                if data.get("success") and data["data"].get("isValid"):
                    # Update cache
                    await self._update_license_cache(data["data"])

                    # Log success
                    self._log_validation_attempt(cached_license.license_key, "success", None)

                    return {
                        "valid": True,
                        "license": data["data"]["license"],
                        "inGracePeriod": False
                    }
                else:
                    # Validation failed - check grace period
                    return await self._check_grace_period(cached_license)

        except httpx.HTTPError as e:
            self._log_validation_attempt(cached_license.license_key, "offline", str(e))
            # Network error - check grace period
            return await self._check_grace_period(cached_license)
        except Exception as e:
            self._log_validation_attempt(cached_license.license_key, "failed", str(e))
            return await self._check_grace_period(cached_license)

    async def _check_grace_period(self, cached_license: LocalLicenseCache) -> Dict[str, Any]:
        """
        Check if system can continue operating in grace period.
        """
        now = datetime.utcnow()

        # Check if cached license is still valid
        if cached_license.valid_until and cached_license.valid_until > now:
            return {
                "valid": True,
                "inGracePeriod": True,
                "validUntil": cached_license.valid_until.isoformat(),
                "license": cached_license.license_data
            }

        # Check if grace period has started
        if not cached_license.in_grace_period:
            # Start grace period
            grace_period_expires = now + timedelta(hours=settings.OFFLINE_GRACE_PERIOD_HOURS)

            cached_license.in_grace_period = True
            cached_license.grace_period_started_at = now
            cached_license.grace_period_expires_at = grace_period_expires
            self.db.commit()

            return {
                "valid": True,
                "inGracePeriod": True,
                "gracePeriodExpires": grace_period_expires.isoformat(),
                "license": cached_license.license_data
            }

        # Check if still in grace period
        if cached_license.grace_period_expires_at and cached_license.grace_period_expires_at > now:
            return {
                "valid": True,
                "inGracePeriod": True,
                "gracePeriodExpires": cached_license.grace_period_expires_at.isoformat(),
                "license": cached_license.license_data
            }

        # Grace period expired
        return {
            "valid": False,
            "reason": "grace_period_expired",
            "message": "License validation failed and grace period has expired"
        }

    async def send_heartbeat(self):
        """
        Send heartbeat to cloud service.
        """
        cached_license = self._get_cached_license()
        if not cached_license:
            return

        try:
            # Collect usage metrics
            usage_metrics = await self._collect_usage_metrics()
            system_info = get_system_info()

            async with httpx.AsyncClient(timeout=settings.LICENSE_API_TIMEOUT) as client:
                await client.post(
                    f"{self.cloud_api_url}/licenses/heartbeat",
                    json={
                        "licenseKey": cached_license.license_key,
                        "hardwareId": self.hardware_id,
                        "installationId": self.installation_id,
                        "usageMetrics": usage_metrics,
                        "systemInfo": system_info
                    },
                    headers={
                        "Content-Type": "application/json",
                        "X-Installation-ID": self.installation_id
                    }
                )

                # Update last validation time
                cached_license.last_validated_at = datetime.utcnow()
                self.db.commit()

                print(f"Heartbeat sent successfully at {datetime.utcnow()}")

        except Exception as e:
            print(f"Heartbeat failed: {str(e)}")

    def _start_heartbeat(self):
        """
        Start periodic heartbeat scheduler.
        """
        if not self.scheduler.running:
            self.scheduler.add_job(
                self.send_heartbeat,
                'interval',
                hours=settings.HEARTBEAT_INTERVAL_HOURS,
                id='license_heartbeat'
            )
            self.scheduler.start()

    async def is_feature_available(self, feature_key: str) -> bool:
        """
        Check if a specific feature is available based on license.
        """
        feature = self.db.query(LocalFeatureFlag).filter(
            LocalFeatureFlag.feature_key == feature_key
        ).first()

        if not feature:
            # Feature not found - check if it's a core feature
            if settings.ALLOW_UNLICENSED_CORE_FEATURES and self._is_core_feature(feature_key):
                return True
            return False

        return feature.licensed and feature.system_enabled

    def _is_core_feature(self, feature_key: str) -> bool:
        """
        Check if feature is a core feature (always available).
        """
        core_features = [
            "module.camera_management",
            "module.live_view",
            "module.recording_basic",
            "module.playback",
            "module.user_management"
        ]
        return feature_key in core_features

    async def _store_license_cache(self, license_data: Dict[str, Any]):
        """
        Store license data in local cache.
        """
        # Check if license already exists
        existing = self.db.query(LocalLicenseCache).filter(
            LocalLicenseCache.license_key == license_data["licenseKey"]
        ).first()

        valid_until = datetime.utcnow() + timedelta(days=30)  # Default 30 days

        if existing:
            existing.license_data = license_data
            existing.cached_at = datetime.utcnow()
            existing.valid_until = valid_until
            existing.is_valid = True
            existing.license_signature = license_data.get("signature", "")
        else:
            cached_license = LocalLicenseCache(
                license_key=license_data["licenseKey"],
                license_data=license_data,
                valid_until=valid_until,
                license_signature=license_data.get("signature", "")
            )
            self.db.add(cached_license)

        self.db.commit()

    async def _update_license_cache(self, validation_data: Dict[str, Any]):
        """
        Update license cache with validation data.
        """
        cached_license = self._get_cached_license()
        if not cached_license:
            return

        cached_license.last_validated_at = datetime.utcnow()
        cached_license.is_valid = validation_data.get("isValid", False)
        cached_license.in_grace_period = False
        self.db.commit()

    async def _sync_feature_flags(self, license_data: Dict[str, Any]):
        """
        Sync feature flags from license data.
        """
        # First, reset all features to unlicensed
        self.db.query(LocalFeatureFlag).update({"licensed": False})

        # Then enable licensed features
        enabled_features = license_data.get("enabledFeatures", [])

        for feature_key in enabled_features:
            feature = self.db.query(LocalFeatureFlag).filter(
                LocalFeatureFlag.feature_key == feature_key
            ).first()

            if feature:
                feature.licensed = True
                feature.synced_at = datetime.utcnow()
            else:
                new_feature = LocalFeatureFlag(
                    feature_key=feature_key,
                    enabled=True,
                    licensed=True,
                    synced_at=datetime.utcnow()
                )
                self.db.add(new_feature)

        self.db.commit()

    def _get_cached_license(self) -> Optional[LocalLicenseCache]:
        """
        Get cached license from local database.
        """
        return self.db.query(LocalLicenseCache).first()

    def _log_validation_attempt(self, license_key: str, result: str, error_message: Optional[str]):
        """
        Log validation attempt.
        """
        log_entry = LocalLicenseValidationAttempt(
            license_key=license_key,
            result=result,
            error_message=error_message,
            hardware_id=self.hardware_id
        )
        self.db.add(log_entry)
        self.db.commit()

    async def _collect_usage_metrics(self) -> Dict[str, Any]:
        """
        Collect usage metrics for reporting to cloud.
        """
        # Query actual usage from database
        # This is placeholder - implement based on your schema
        return {
            "camerasInUse": 0,  # TODO: Query from cameras table
            "usersActive": 0,    # TODO: Query from users table
            "storageUsedGb": 0   # TODO: Query from storage
        }

    async def get_license_status(self) -> Dict[str, Any]:
        """
        Get current license status for UI display.
        """
        cached_license = self._get_cached_license()

        if not cached_license:
            return {
                "hasLicense": False,
                "status": "no_license",
                "message": "No license activated"
            }

        validation = await self.validate_license()

        if validation["valid"]:
            license_data = cached_license.license_data
            return {
                "hasLicense": True,
                "status": license_data.get("status", "active"),
                "licenseKey": cached_license.license_key,
                "tier": license_data.get("tier", ""),
                "expiresAt": license_data.get("expiresAt"),
                "maxCameras": license_data.get("maxCameras"),
                "maxUsers": license_data.get("maxUsers"),
                "inGracePeriod": validation.get("inGracePeriod", False),
                "gracePeriodExpires": validation.get("gracePeriodExpires"),
                "lastValidated": cached_license.last_validated_at.isoformat() if cached_license.last_validated_at else None
            }
        else:
            return {
                "hasLicense": True,
                "status": "invalid",
                "licenseKey": cached_license.license_key,
                "message": validation.get("message", "License validation failed"),
                "reason": validation.get("reason")
            }
