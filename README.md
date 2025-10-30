# Foxsight License Client Service

On-premise license management service for Foxsight Central Command VMS. This service manages license activation, validation, feature gating, and provides offline operation with grace period support.

## Overview

The License Client Service communicates with the cloud-based Foxsight License Server to:
- Activate and validate licenses
- Cache license data locally for offline operation
- Enforce feature gating based on license tier
- Send periodic heartbeats with usage metrics
- Support 72-hour grace period when offline

## Features

- ✅ **License Activation** - Activate licenses with hardware binding
- ✅ **Online Validation** - Periodic validation with cloud service
- ✅ **Offline Operation** - 72-hour grace period when disconnected
- ✅ **Feature Gating** - Control access to licensed features
- ✅ **Local Caching** - PostgreSQL-based local license cache
- ✅ **Hardware Fingerprinting** - System-based license binding
- ✅ **Usage Metrics** - Automatic heartbeat with usage data
- ✅ **RESTful API** - FastAPI-based endpoints

## Technology Stack

- **Framework**: FastAPI (latest with standard dependencies)
- **Database**: PostgreSQL (SQLAlchemy 2.0.44)
- **HTTP Client**: httpx 0.28.1
- **Validation**: Pydantic 2.12.3
- **Scheduling**: APScheduler 3.11.0
- **System Info**: psutil 7.1.2

## Installation

### Prerequisites

- Python 3.11+
- PostgreSQL database
- Access to Foxsight License Server

### Setup

1. **Install dependencies**:
```bash
pip install -r requirements.txt
```

2. **Configure environment**:
```bash
cp .env.template .env
# Edit .env with your configuration
```

3. **Run the service**:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Docker Deployment

```bash
# Build image
docker build -t vms-license-client:standalone .

# Run container
docker run -d \
  --name license-client \
  -p 8010:8000 \
  -e DATABASE_URL=postgresql://user:pass@postgres:5432/vms_db \
  -e LICENSE_API_URL=http://license-server:4000/api \
  vms-license-client:standalone
```

## API Endpoints

### License Management

#### Activate License
```http
POST /api/license/activate
Content-Type: application/json

{
  "licenseKey": "FXSGHT-XXXX-XXXX-XXXX-XXXX"
}
```

#### Get License Status
```http
GET /api/license/status
```

#### Validate License
```http
POST /api/license/validate
```

### Feature Gating

#### Check Feature Availability
```http
POST /api/license/feature/check
Content-Type: application/json

{
  "featureKey": "module.analytics_advanced"
}
```

### System

#### Send Heartbeat
```http
POST /api/license/heartbeat
```

#### Health Check
```http
GET /health
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LICENSE_API_URL` | License Server API URL | `http://localhost:4000/api` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://vms_user:vms_secure_password@localhost:5432/vms_db` |
| `HEARTBEAT_INTERVAL_HOURS` | Heartbeat frequency | `4` |
| `OFFLINE_GRACE_PERIOD_HOURS` | Grace period duration | `72` |
| `ALLOW_UNLICENSED_CORE_FEATURES` | Allow core features without license | `true` |

## Database Schema

The service creates and manages 4 tables:

1. **local_license_cache** - Cached license data from cloud
2. **local_license_validation_attempts** - Validation attempt logs
3. **local_feature_flags** - Feature availability flags
4. **system_config** - System configuration (installation_id)

## Feature Keys

### Core Features (Always Available)
- `module.camera_management`
- `module.live_view`
- `module.recording_basic`
- `module.playback`
- `module.user_management`

### Licensed Features
- `module.map_view`
- `module.analytics_advanced`
- `module.health_monitoring`
- `module.alarm_advanced`
- `module.storage_optimization`

## Architecture

```
┌────────────────────────────────────┐
│    Foxsight License Server         │
│    (Cloud - Node.js)               │
│    localhost:4000                  │
└────────────────┬───────────────────┘
                 │ HTTPS REST API
                 │
┌────────────────▼───────────────────┐
│    License Client Service          │
│    (On-Prem - Python/FastAPI)      │
│    Port: 8010                      │
│                                     │
│  ┌──────────────────────────────┐ │
│  │  License Client Logic        │ │
│  │  - Activation                │ │
│  │  - Validation                │ │
│  │  - Grace Period              │ │
│  └──────────┬───────────────────┘ │
│             │                      │
│  ┌──────────▼───────────────────┐ │
│  │  Local Cache (PostgreSQL)    │ │
│  │  - License data              │ │
│  │  - Feature flags             │ │
│  │  - Validation logs           │ │
│  └──────────────────────────────┘ │
└────────────────────────────────────┘
```

## Grace Period Behavior

1. **Online**: License validates successfully with cloud
2. **First Offline**: Grace period starts (72 hours)
3. **Still Offline**: System continues to operate in grace period
4. **Grace Expired**: Features become unavailable
5. **Back Online**: Grace period resets, normal operation resumes

## Development

### Running Tests

```bash
pytest
```

### Code Style

```bash
black .
flake8 .
```

### Type Checking

```bash
mypy .
```

## Integration with Central Command

This service is integrated with Foxsight Central Command VMS as a microservice. The API Gateway proxies license endpoints and enforces feature gating.

### API Gateway Integration

```python
# Feature gating middleware
@app.middleware("http")
async def check_feature_access(request, call_next):
    # Check if route requires license
    if requires_license(request.url.path):
        response = await httpx.post(
            "http://license-client:8010/api/license/feature/check",
            json={"featureKey": get_feature_key(request.url.path)}
        )
        if not response.json()["available"]:
            return JSONResponse(
                status_code=403,
                content={"error": "Feature not licensed"}
            )
    return await call_next(request)
```

## Troubleshooting

### License Activation Fails
- Verify License Server is accessible
- Check `LICENSE_API_URL` configuration
- Review logs for network errors

### Database Connection Issues
- Verify PostgreSQL is running
- Check `DATABASE_URL` configuration
- Ensure database user has proper permissions

### Grace Period Not Working
- Check `OFFLINE_GRACE_PERIOD_HOURS` setting
- Verify license was successfully cached
- Review validation attempt logs in database

## Support

For issues and questions:
- GitHub Issues: [foxsight-license-client](https://github.com/Chetanbansiwal/foxsight-license-client)
- Documentation: [Foxsight VMS Docs](https://docs.foxsight.io)

## License

Proprietary - Foxsight Technology Inc.

---

**Version**: 1.0.0
**Last Updated**: October 30, 2025
**Maintained by**: Foxsight Development Team
