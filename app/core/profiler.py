import os
import platform
import subprocess
import shutil
import logging

logger = logging.getLogger(__name__)

def get_system_ram_gb() -> float:
    """Detect total system RAM in GB with multiple fallback options."""
    # Try importing psutil
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        pass

    # Platform-specific CLI fallbacks
    system = platform.system().lower()
    if system == "darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], stderr=subprocess.DEVNULL)
            return int(out.strip()) / (1024 ** 3)
        except Exception:
            pass
    elif system == "linux":
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if "MemTotal" in line:
                        parts = line.split()
                        return int(parts[1]) / (1024 ** 2)
        except Exception:
            pass
    elif system == "windows":
        try:
            out = subprocess.check_output(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"], stderr=subprocess.DEVNULL)
            # Output format:
            # TotalPhysicalMemory
            # 17179869184
            lines = out.decode().strip().split("\n")
            if len(lines) > 1:
                return int(lines[1].strip()) / (1024 ** 3)
        except Exception:
            pass

    # Default fallback: assume 16.0 GB if all else fails
    return 16.0

def detect_gpu() -> tuple[bool, str]:
    """Detect presence and type of GPU (nvidia, apple_silicon, none)."""
    # 1. Nvidia CUDA check via nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                return True, "nvidia"
        except Exception:
            pass

    # 2. Apple Silicon check (macOS arm64)
    if platform.system().lower() == "darwin":
        try:
            if platform.machine().lower() == "arm64" or "arm" in platform.processor().lower():
                return True, "apple_silicon"
        except Exception:
            pass

    return False, "none"

def detect_hardware() -> dict:
    """Classifies client machine capabilities and recommends optimal models/workers."""
    ram_gb = get_system_ram_gb()
    cpu_cores = os.cpu_count() or 2
    has_gpu, gpu_vendor = detect_gpu()

    # Define Hardware Tier
    if ram_gb < 12.0:
        # Low specs: e.g. 8GB RAM machines (like base MacBook, thin clients)
        tier = "low"
        llm = "phi3:mini"
        embed = "all-mpnet-base-v2"  # 768-dim
        max_workers = 1
    elif ram_gb < 24.0 or not has_gpu:
        # Medium specs: e.g. 16GB RAM machines, or high RAM CPU-only servers
        tier = "medium"
        llm = "phi3:mini"
        embed = "all-mpnet-base-v2"  # 768-dim
        max_workers = 1 if not has_gpu else max(1, cpu_cores // 2)
    else:
        # High specs: e.g. 24GB/32GB+ RAM workstations with dedicated GPUs
        tier = "high"
        llm = "llama3.1:8b"
        embed = "all-mpnet-base-v2"  # 768-dim
        max_workers = max(2, cpu_cores - 1)

    profile = {
        "ram_gb": round(ram_gb, 2),
        "cpu_cores": cpu_cores,
        "has_gpu": has_gpu,
        "gpu_vendor": gpu_vendor,
        "tier": tier,
        "llm_model": llm,
        "embed_model": embed,
        "max_workers": max_workers
    }

    logger.info(f"🖥️ Hardware Profile Detected: {profile}")
    return profile
