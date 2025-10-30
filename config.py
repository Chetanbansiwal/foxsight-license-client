from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # License Server Configuration
    LICENSE_API_URL: str = "http://localhost:4000/api"
    LICENSE_API_TIMEOUT: int = 30

    # Installation Info
    INSTALLATION_ID: str = ""  # Generated on first run
    INSTALLATION_NAME: str = "Foxsight Central Command VMS"
    APP_VERSION: str = "1.0.0"

    # Database
    DATABASE_URL: str = "postgresql://vms_user:vms_secure_password@localhost:5432/vms_db"

    # Heartbeat Configuration
    HEARTBEAT_INTERVAL_HOURS: int = 4
    VALIDATION_INTERVAL_HOURS: int = 24

    # Grace Period
    OFFLINE_GRACE_PERIOD_HOURS: int = 72

    # Feature Flags
    ALLOW_UNLICENSED_CORE_FEATURES: bool = True  # Allow basic VMS without license

    class Config:
        env_file = ".env"

settings = Settings()
