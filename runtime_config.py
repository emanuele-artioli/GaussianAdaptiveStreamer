import os
import platform
import shutil
import subprocess
from typing import Optional

import torch

from logger import logger


def _mps_available() -> bool:
    return bool(
        hasattr(torch.backends, "mps")
        and torch.backends.mps.is_available()
    )


def pick_torch_device(explicit: Optional[torch.device] = None) -> torch.device:
    """
    Choose the best available torch device for the current machine.

    Order is: explicit > GS_DEVICE env > CUDA > MPS (Apple Silicon) > CPU.
    """
    if explicit is not None:
        return explicit

    requested = os.environ.get("GS_DEVICE", "").strip().lower()
    if requested:
        if requested == "cuda":
            if torch.cuda.is_available():
                return torch.device("cuda")
            logger.warning("GS_DEVICE=cuda requested but CUDA is unavailable; falling back")
        elif requested == "mps":
            if _mps_available():
                return torch.device("mps")
            logger.warning("GS_DEVICE=mps requested but MPS is unavailable; falling back")
        elif requested == "cpu":
            return torch.device("cpu")
        else:
            logger.warning("Unknown GS_DEVICE=%s; falling back to auto device selection", requested)

    if torch.cuda.is_available():
        return torch.device("cuda")
    if _mps_available():
        return torch.device("mps")
    return torch.device("cpu")


def synchronize_device(device: torch.device) -> None:
    if device.type == "cuda" and torch.cuda.is_available():
        torch.cuda.synchronize()
        return

    if device.type == "mps" and _mps_available() and hasattr(torch.mps, "synchronize"):
        torch.mps.synchronize()


def clear_device_cache(device: Optional[torch.device]) -> None:
    if device is None:
        return

    if device.type == "cuda" and torch.cuda.is_available():
        torch.cuda.empty_cache()
        return

    if device.type == "mps" and _mps_available() and hasattr(torch.mps, "empty_cache"):
        torch.mps.empty_cache()


def memory_allocated_gb(device: torch.device) -> Optional[float]:
    if device.type == "cuda" and torch.cuda.is_available():
        return float(torch.cuda.memory_allocated()) / (1024 ** 3)

    if device.type == "mps" and _mps_available() and hasattr(torch.mps, "current_allocated_memory"):
        return float(torch.mps.current_allocated_memory()) / (1024 ** 3)

    return None


def resolve_ffmpeg_binary(env_value: str) -> Optional[str]:
    ffmpeg = shutil.which(env_value) if os.path.sep not in env_value else env_value
    if not ffmpeg:
        return None
    if not os.path.exists(ffmpeg):
        return None
    return ffmpeg


def ffmpeg_has_encoder(ffmpeg: str, encoder: str) -> bool:
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return f" {encoder}" in out or out.strip().endswith(encoder)
    except Exception:
        logger.exception("Failed to probe ffmpeg encoders")
        return False


def pick_video_encoder(ffmpeg: str) -> str:
    """
    Select a low-latency H.264 encoder available in the local ffmpeg build.

    Priority:
      1) FFMPEG_VIDEO_ENCODER environment variable
      2) h264_videotoolbox on macOS
      3) h264_nvenc on other systems
      4) libx264 as a universal fallback
    """
    env_encoder = os.environ.get("FFMPEG_VIDEO_ENCODER", "").strip()
    if env_encoder:
        if ffmpeg_has_encoder(ffmpeg, env_encoder):
            return env_encoder
        logger.warning("FFMPEG_VIDEO_ENCODER=%s not supported by this ffmpeg; falling back", env_encoder)

    candidates = []
    if platform.system() == "Darwin":
        candidates.extend(["h264_videotoolbox", "libx264"])
    else:
        candidates.extend(["h264_nvenc", "libx264"])

    for enc in candidates:
        if ffmpeg_has_encoder(ffmpeg, enc):
            return enc

    return "libx264"


def encoder_tuning_args(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-preset", "p1", "-tune", "ll", "-rc", "cbr"]
    if encoder == "libx264":
        return ["-preset", "veryfast", "-tune", "zerolatency"]
    return []
