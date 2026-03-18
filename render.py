import os
import threading
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from scipy.spatial.transform import Rotation as R

from logger import logger
from runtime_config import memory_allocated_gb, synchronize_device
from statics import CAPTURES_DIR, PREVIEW_CANDIDATES

try:
    from gsplat import rasterization as gsplat_rasterization
    GSPLAT_IMPORT_ERROR = None
except Exception as exc:
    gsplat_rasterization = None
    GSPLAT_IMPORT_ERROR = exc


_BACKEND_LOCK = threading.Lock()
_RUNTIME_BACKEND_OVERRIDE: str | None = None
_RUNTIME_BACKEND_REASON: str | None = None
_PREVIEW_CACHE: dict[str, np.ndarray] = {}


def _env_backend() -> str:
    return os.environ.get("GS_RENDER_BACKEND", "").strip().lower()


def _set_runtime_backend(backend: str, reason: str) -> None:
    global _RUNTIME_BACKEND_OVERRIDE, _RUNTIME_BACKEND_REASON
    with _BACKEND_LOCK:
        if _RUNTIME_BACKEND_OVERRIDE == backend:
            return
        _RUNTIME_BACKEND_OVERRIDE = backend
        _RUNTIME_BACKEND_REASON = reason
        logger.warning("Render backend switched to %s: %s", backend, reason)


def render_backend() -> tuple[str, str]:
    """
    Returns (backend, reason).

    backends:
      - "gsplat": use gsplat rasterization path
      - "preview": return model preview images as a compatibility fallback
    """
    forced = _env_backend()

    if forced in {"preview", "fallback", "image"}:
        return "preview", "forced by GS_RENDER_BACKEND"

    if forced in {"gsplat", "native"}:
        if gsplat_rasterization is None:
            return "preview", f"GS_RENDER_BACKEND=gsplat but gsplat import failed: {GSPLAT_IMPORT_ERROR}"
        return "gsplat", "forced by GS_RENDER_BACKEND"

    if _RUNTIME_BACKEND_OVERRIDE is not None:
        return _RUNTIME_BACKEND_OVERRIDE, _RUNTIME_BACKEND_REASON or "runtime override"

    if gsplat_rasterization is None:
        return "preview", f"gsplat import failed: {GSPLAT_IMPORT_ERROR}"

    if not torch.cuda.is_available():
        return "preview", "CUDA unavailable on this machine"

    return "gsplat", "CUDA + gsplat available"


def using_preview_fallback() -> bool:
    backend, _ = render_backend()
    return backend == "preview"


def _get_preview_path(model) -> Path | None:
    base = Path(model.model_path).parent
    for name in PREVIEW_CANDIDATES:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def _get_preview_base_image(model) -> np.ndarray:
    path = _get_preview_path(model)
    if path is None:
        return np.full((480, 640, 3), fill_value=(12, 24, 38), dtype=np.uint8)

    key = str(path)
    with _BACKEND_LOCK:
        cached = _PREVIEW_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        with Image.open(path) as img:
            base = np.asarray(img.convert("RGB"), dtype=np.uint8)
    except Exception:
        logger.exception("Failed to open preview image: %s", path)
        base = np.full((480, 640, 3), fill_value=(12, 24, 38), dtype=np.uint8)

    with _BACKEND_LOCK:
        _PREVIEW_CACHE[key] = base
    return base


def _render_preview_fallback(width: int, height: int, profile: int, model) -> tuple[np.ndarray, float]:
    t0 = time.perf_counter()

    p = max(0, min(3, int(profile)))
    factor = 1 << p
    out_w = max(1, int(width) // factor)
    out_h = max(1, int(height) // factor)

    base = _get_preview_base_image(model)
    img = Image.fromarray(base, mode="RGB").resize((out_w, out_h), Image.Resampling.BILINEAR)
    out = np.asarray(img, dtype=np.uint8)

    render_ms = (time.perf_counter() - t0) * 1000.0
    return out, render_ms



def save_render_bytes(bytes: bytes, profile: int, base_name: str | None = None, type: str = 'jpg') -> str:
    os.makedirs(f"{CAPTURES_DIR}/{base_name}/{type}", exist_ok=True)
    ts_ms = int(time.time() * 1000)
    fileName = f"{base_name}/{type}/frame-{ts_ms}_{profile}_.{type}"

    out_path = os.path.join(CAPTURES_DIR, fileName)
    print(out_path)
    with open(out_path, "wb") as f:
        f.write(bytes)

    return out_path


def create_viewmat(azimuth_deg, elevation_deg, x, y, z):
    rot = R.from_euler("xyz", [elevation_deg, azimuth_deg, 0], degrees=True).as_matrix()
    trans = np.array([x, y, z])
    c2w = np.eye(4)
    c2w[:3, :3] = rot
    c2w[:3, 3] = trans
    w2c = np.linalg.inv(c2w)
    return torch.tensor(w2c, dtype=torch.float32)


def _render_with_gsplat(
    azimuth_deg,
    elevation_deg,
    x,
    y,
    z,
    fx,
    fy,
    cx,
    cy,
    width,
    height,
    profile,
    model,
) -> tuple[np.ndarray, float]:
    if gsplat_rasterization is None:
        raise RuntimeError("gsplat rasterization is unavailable")

    p = max(0, min(3, int(profile)))
    factor = 1 << p

    w = int(width)
    h = int(height)

    device, means, quats, scales, opacities, shs = model.acquire()
    mem_before = memory_allocated_gb(device)
    if mem_before is not None:
        logger.debug("Device memory before render (%s): %.2f GB", device.type, mem_before)

    viewmat = create_viewmat(azimuth_deg, elevation_deg, x, y, z).to(device).unsqueeze(0)
    k_mat = torch.tensor(
        [[fx, 0, cx], [0, fy, cy], [0, 0, 1]],
        dtype=torch.float32,
        device=device,
    ).unsqueeze(0)

    synchronize_device(device)
    t0 = time.perf_counter()

    try:
        with torch.no_grad():
            colors_rendered, _alphas, _ = gsplat_rasterization(
                means=means,
                quats=quats,
                scales=scales,
                opacities=opacities,
                colors=shs,
                viewmats=viewmat,
                Ks=k_mat,
                width=w,
                height=h,
                packed=False,
                sh_degree=0,
                backgrounds=None,
                render_mode="RGB",
            )
    except Exception:
        logger.exception("Rasterization failed")
        raise
    finally:
        model.release()

    synchronize_device(device)
    t_render = time.perf_counter()

    img_full_gpu_uint8 = (colors_rendered[0].clamp(0, 1) * 255).byte()

    if factor > 1:
        low_h = max(1, h // factor)
        low_w = max(1, w // factor)

        img = img_full_gpu_uint8.permute(2, 0, 1).unsqueeze(0).float() / 255.0
        img = F.interpolate(img, size=(low_h, low_w), mode="area")
        img_stream_gpu_uint8 = (img.squeeze(0).permute(1, 2, 0) * 255).byte()

        synchronize_device(device)
        t_downsample = time.perf_counter()
    else:
        img_stream_gpu_uint8 = img_full_gpu_uint8
        t_downsample = t_render

    img_stream = img_stream_gpu_uint8.cpu().numpy()
    t_transfer = time.perf_counter()

    render_ms = (t_transfer - t0) * 1000.0

    logger.info(
        "[Render] total(no-encode)=%.2fms (raster=%.2fms, gpu_downsample=%.2fms, transfer=%.2fms)",
        render_ms,
        (t_render - t0) * 1000,
        (t_downsample - t_render) * 1000,
        (t_transfer - t_downsample) * 1000,
    )

    mem_after = memory_allocated_gb(device)
    if mem_after is not None:
        logger.debug("Device memory after render (%s): %.2f GB", device.type, mem_after)

    return img_stream, render_ms



def render_image_raw(
    azimuth_deg, elevation_deg, x, y, z,
    fx, fy, cx, cy, width, height, profile, model
) -> tuple[np.ndarray, float]:
    """
    Returns:
      img_stream: np.uint8 HxWx3 RGB (CPU)
      render_ms: total render+downsample+transfer time in ms (no encode)
    """
    backend, reason = render_backend()
    if backend == "preview":
        return _render_preview_fallback(width=width, height=height, profile=profile, model=model)

    try:
        return _render_with_gsplat(
            azimuth_deg=azimuth_deg,
            elevation_deg=elevation_deg,
            x=x,
            y=y,
            z=z,
            fx=fx,
            fy=fy,
            cx=cx,
            cy=cy,
            width=width,
            height=height,
            profile=profile,
            model=model,
        )
    except Exception as exc:
        if _env_backend() in {"gsplat", "native"}:
            raise

        _set_runtime_backend("preview", f"gsplat render failed ({type(exc).__name__}); using preview fallback")
        return _render_preview_fallback(width=width, height=height, profile=profile, model=model)
