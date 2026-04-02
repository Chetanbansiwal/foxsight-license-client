import hashlib
import platform
import psutil
from pathlib import Path

def get_hardware_fingerprint() -> str:
    """
    Generate a unique hardware fingerprint for the HOST system.
    Uses /etc/machine-id (mounted from host) as the primary stable identifier,
    combined with CPU and architecture info. This avoids Docker container MAC
    addresses which change on container recreation.
    """
    # Use host machine-id (stable across reboots, unique per host)
    # Must be mounted into the container: -v /etc/machine-id:/etc/machine-id:ro
    machine_id = ""
    for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
        try:
            machine_id = Path(path).read_text().strip()
            if machine_id:
                break
        except (FileNotFoundError, PermissionError):
            continue

    # Get CPU info
    cpu_count = str(psutil.cpu_count(logical=True))

    # Get system info
    system = platform.system()
    machine = platform.machine()

    # Create fingerprint from host-level identifiers
    fingerprint_data = f"{machine_id}|{cpu_count}|{system}|{machine}"
    hardware_id = hashlib.sha256(fingerprint_data.encode()).hexdigest()

    return hardware_id

def get_system_info() -> dict:
    """
    Collect system information for reporting to cloud.
    """
    return {
        "os_platform": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "cpu_count": psutil.cpu_count(logical=True),
        "total_memory_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "hostname": platform.node(),
        "architecture": platform.machine()
    }
