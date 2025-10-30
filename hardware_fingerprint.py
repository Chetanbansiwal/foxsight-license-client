import uuid
import hashlib
import platform
import psutil

def get_hardware_fingerprint() -> str:
    """
    Generate a unique hardware fingerprint for this system.
    Combines multiple system identifiers to create a stable ID.
    """
    # Get MAC address (most stable identifier)
    mac = ':'.join(['{:02x}'.format((uuid.getnode() >> elements) & 0xff)
                    for elements in range(0, 2*6, 2)][::-1])

    # Get CPU info
    cpu_count = str(psutil.cpu_count(logical=True))

    # Get system info
    system = platform.system()
    machine = platform.machine()

    # Create fingerprint
    fingerprint_data = f"{mac}|{cpu_count}|{system}|{machine}"
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
