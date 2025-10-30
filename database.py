from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from config import settings

engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class LocalLicenseCache(Base):
    __tablename__ = "local_license_cache"

    id = Column(Integer, primary_key=True, index=True)
    license_key = Column(String(255), unique=True, nullable=False, index=True)
    license_data = Column(JSON, nullable=False)  # Full license object from cloud

    # Validation
    cached_at = Column(DateTime, default=datetime.utcnow)
    valid_until = Column(DateTime, nullable=False)
    last_validated_at = Column(DateTime)

    # Status
    is_valid = Column(Boolean, default=True)
    validation_error = Column(Text)

    # Grace Period
    in_grace_period = Column(Boolean, default=False)
    grace_period_started_at = Column(DateTime)
    grace_period_expires_at = Column(DateTime)

    # Signature
    license_signature = Column(Text, nullable=False)

class LocalLicenseValidationAttempt(Base):
    __tablename__ = "local_license_validation_attempts"

    id = Column(Integer, primary_key=True, index=True)
    license_key = Column(String(255))

    # Attempt Result
    result = Column(String(20), nullable=False)  # success, failed, offline
    error_message = Column(Text)

    # Context
    hardware_id = Column(String(255))
    attempted_at = Column(DateTime, default=datetime.utcnow, index=True)

class LocalFeatureFlag(Base):
    __tablename__ = "local_feature_flags"

    id = Column(Integer, primary_key=True, index=True)
    feature_key = Column(String(100), unique=True, nullable=False, index=True)
    enabled = Column(Boolean, default=False)

    # From cloud license
    licensed = Column(Boolean, default=False)

    # Local override (admin can disable licensed features)
    system_enabled = Column(Boolean, default=True)

    # Metadata
    synced_at = Column(DateTime, default=datetime.utcnow)

class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Create tables
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
