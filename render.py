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
from models import load_gs_ply
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
_SOFTWARE_CACHE: dict[str, dict[str, np.ndarray]] = {}

_SOFTWARE_SH_C0 = 0.28209479177387814
_SOFTWARE_SPLAT_KERNEL: tuple[tuple[int, int, float], ...] = (
    (0, 0, 1.0),
    (-1, 0, 0.38),
    (1, 0, 0.38),
    (0, -1, 0.38),
    (0, 1, 0.38),
    (-1, -1, 0.18),
    (1, -1, 0.18),
    (-1, 1, 0.18),
    (1, 1, 0.18),
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%s, using default=%s", name, raw, default)
        return default


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
      - "software": use CPU software point-splat fallback
      - "preview": return model preview images as a compatibility fallback
    """
    forced = _env_backend()

    if forced in {"preview", "fallback", "image"}:
        return "preview", "forced by GS_RENDER_BACKEND"

    if forced in {"software", "cpu", "point", "splat-cpu"}:
        return "software", "forced by GS_RENDER_BACKEND"

    if forced in {"gsplat", "native"}:
        if gsplat_rasterization is None:
            return "software", f"GS_RENDER_BACKEND=gsplat but gsplat import failed: {GSPLAT_IMPORT_ERROR}"
        return "gsplat", "forced by GS_RENDER_BACKEND"

    if _RUNTIME_BACKEND_OVERRIDE is not None:
        return _RUNTIME_BACKEND_OVERRIDE, _RUNTIME_BACKEND_REASON or "runtime override"

    if gsplat_rasterization is None:
        return "software", f"gsplat import failed: {GSPLAT_IMPORT_ERROR}"

    if not torch.cuda.is_available():
        return "software", "CUDA unavailable on this machine"

    return "gsplat", "CUDA + gsplat available"


def using_preview_fallback() -> bool:
    backend, _ = render_backend()
    return backend == "preview"


def using_software_fallback() -> bool:
    backend, _ = render_backend()
    return backend == "software"


def requires_tensor_model_load() -> bool:
    backend, _ = render_backend()
    return backend == "gsplat"


def _software_model_data(model) -> dict[str, np.ndarray]:
    key = str(model.model_path)
    with _BACKEND_LOCK:
        cached = _SOFTWARE_CACHE.get(key)
    if cached is not None:
        return cached

    means_np, _quats_np, scales_np, opacities_np, shs_np = load_gs_ply(model.model_path)

    rgb = np.clip(0.5 + (_SOFTWARE_SH_C0 * shs_np[:, 0, :]), 0.0, 1.0).astype(np.float32) * 255.0
    opacity = np.clip(opacities_np.astype(np.float32), 0.02, 1.0)
    radius = np.clip(scales_np.mean(axis=1).astype(np.float32), 0.25, 8.0)

    out = {
        "xyz": means_np.astype(np.float32, copy=False),
        "rgb": rgb,
        "opacity": opacity,
        "radius": radius,
    }

    with _BACKEND_LOCK:
        _SOFTWARE_CACHE[key] = out

    return out


def _render_software_fallback(
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
    t0 = time.perf_counter()

    p = max(0, min(3, int(profile)))
    factor = 1 << p
    out_w = max(1, int(width) // factor)
    out_h = max(1, int(height) // factor)

    fx_s = float(fx) / factor
    fy_s = float(fy) / factor
    cx_s = float(cx) / factor
    cy_s = float(cy) / factor

    model_data = _software_model_data(model)
    xyz = model_data["xyz"]
    rgb = model_data["rgb"]
    opacity = model_data["opacity"]
    radius = model_data["radius"]

    if xyz.size == 0:
        return np.zeros((out_h, out_w, 3), dtype=np.uint8), (time.perf_counter() - t0) * 1000.0

    max_points = max(2000, _env_int("GS_SOFTWARE_MAX_POINTS", 120_000))
    if xyz.shape[0] > max_points:
        stride = (xyz.shape[0] + max_points - 1) // max_points
        xyz = xyz[::stride]
        rgb = rgb[::stride]
        opacity = opacity[::stride]
        radius = radius[::stride]

    yaw = np.deg2rad(np.float32(azimuth_deg))
    pitch = np.deg2rad(np.float32(elevation_deg))
    cos_yaw = np.cos(yaw)
    sin_yaw = np.sin(yaw)
    cos_pitch = np.cos(pitch)
    sin_pitch = np.sin(pitch)

    tx = xyz[:, 0] - np.float32(x)
    ty = xyz[:, 1] - np.float32(y)
    tz = xyz[:, 2] - np.float32(z)

    xz_x = (cos_yaw * tx) - (sin_yaw * tz)
    xz_z = (sin_yaw * tx) + (cos_yaw * tz)
    yz_y = (cos_pitch * ty) - (sin_pitch * xz_z)
    yz_z = (sin_pitch * ty) + (cos_pitch * xz_z)

    valid = yz_z > 0.01
    if not np.any(valid):
        return np.zeros((out_h, out_w, 3), dtype=np.uint8), (time.perf_counter() - t0) * 1000.0

    xz_x = xz_x[valid]
    yz_y = yz_y[valid]
    yz_z = yz_z[valid]
    rgb = rgb[valid]
    opacity = opacity[valid]
    radius = radius[valid]

    px = np.floor(cx_s + (xz_x / yz_z) * fx_s * 0.5).astype(np.int32)
    py = np.floor(cy_s - (yz_y / yz_z) * fy_s * 0.5).astype(np.int32)

    in_bounds = (px > 0) & (py > 0) & (px < out_w - 1) & (py < out_h - 1)
    if not np.any(in_bounds):
        return np.zeros((out_h, out_w, 3), dtype=np.uint8), (time.perf_counter() - t0) * 1000.0

    px = px[in_bounds]
    py = py[in_bounds]
    yz_z = yz_z[in_bounds]
    rgb = rgb[in_bounds]
    opacity = opacity[in_bounds]
    radius = radius[in_bounds]

    depth_weight = np.clip(2.0 / (1.0 + yz_z * yz_z), 0.15, 1.0).astype(np.float32)
    screen_radius = np.clip((radius * fx_s / np.maximum(yz_z, 0.05)) * 0.05, 1.0, 9.0).astype(np.float32)
    alpha = np.clip(opacity * depth_weight * np.clip(screen_radius / 3.0, 0.35, 1.0), 0.0, 1.0).astype(np.float32)

    accum_a = np.zeros((out_h, out_w), dtype=np.float32)
    accum_rgb = np.zeros((out_h, out_w, 3), dtype=np.float32)

    for dx, dy, kernel_w in _SOFTWARE_SPLAT_KERNEL:
        sx = px + dx
        sy = py + dy
        ok = (sx >= 0) & (sy >= 0) & (sx < out_w) & (sy < out_h)
        if not np.any(ok):
            continue

        sx = sx[ok]
        sy = sy[ok]
        w = alpha[ok] * np.float32(kernel_w)
        colors = rgb[ok]

        np.add.at(accum_a, (sy, sx), w)
        np.add.at(accum_rgb[:, :, 0], (sy, sx), colors[:, 0] * w)
        np.add.at(accum_rgb[:, :, 1], (sy, sx), colors[:, 1] * w)
        np.add.at(accum_rgb[:, :, 2], (sy, sx), colors[:, 2] * w)

    out = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    mask = accum_a > 1e-6
    if np.any(mask):
        rgb_frame = np.zeros_like(accum_rgb)
        rgb_frame[mask] = accum_rgb[mask] / accum_a[mask, None]
        out = np.clip(rgb_frame, 0.0, 255.0).astype(np.uint8)

    render_ms = (time.perf_counter() - t0) * 1000.0
    return out, render_ms


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

    if backend == "software":
        try:
            return _render_software_fallback(
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
            if _env_backend() in {"software", "cpu", "point", "splat-cpu"}:
                raise

            _set_runtime_backend("preview", f"software render failed ({type(exc).__name__}); using preview fallback")
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

        _set_runtime_backend("software", f"gsplat render failed ({type(exc).__name__}); using software fallback")
        try:
            return _render_software_fallback(
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
        except Exception as fallback_exc:
            _set_runtime_backend(
                "preview",
                f"software fallback failed ({type(fallback_exc).__name__}); using preview fallback",
            )
            return _render_preview_fallback(width=width, height=height, profile=profile, model=model)
