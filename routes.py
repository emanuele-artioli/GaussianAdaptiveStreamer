import asyncio, os
from pathlib import Path
import threading
from urllib.parse import quote
from models import list_models
from experiments import export_experiment_data, metrics_predict_logic, save_movement, HandlerResult
from logger import logger
from render import render_backend, render_image_raw, requires_tensor_model_load, save_render_bytes
from models import get_model, ensure_started
from concurrent.futures import ThreadPoolExecutor
from encoding import encode_jpeg, encode_png
from statics import (
    EXPERIMENTS_DIR,
    DASH_DIR,
    WEB_SPLAT_PUBLIC_DIR,
    WEB_SPLAT_REQUIRED_FILES,
    WEB_SPLAT_CACHE_DIR,
)
from dash_streamer import STREAMER


RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=1)

from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse, Response, PlainTextResponse, RedirectResponse


_WEB_SPLAT_PREPARE_LOCK = threading.Lock()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%s, using default=%s", name, raw, default)
        return default


def _web_splat_ply_limits() -> tuple[int, int]:
    # Keep browser-side parsing and GPU upload tractable for large scenes.
    max_bytes = max(500_000, _env_int("WEB_SPLAT_MAX_PLY_BYTES", 1_000_000))
    max_points = max(5_000, _env_int("WEB_SPLAT_MAX_POINTS", 10_000))
    return max_bytes, max_points


def _web_splat_force_sh0() -> bool:
    raw = os.environ.get("WEB_SPLAT_FORCE_SH0", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _strip_vertex_dtype_to_sh0(sampled):
    names = sampled.dtype.names or ()

    required_core = (
        "x",
        "y",
        "z",
        "nx",
        "ny",
        "nz",
        "f_dc_0",
        "f_dc_1",
        "f_dc_2",
        "opacity",
        "scale_0",
        "scale_1",
        "scale_2",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
    )

    if not all(field in names for field in required_core):
        return sampled, False

    keep_fields = list(required_core)
    new_dtype = [(field, sampled.dtype.fields[field][0]) for field in keep_fields]

    import numpy as np

    stripped = np.empty(sampled.shape[0], dtype=new_dtype)
    for field in keep_fields:
        stripped[field] = sampled[field]

    return stripped, True


def _web_splat_cached_ply_path(model_id: str, source_path: Path, max_points: int, force_sh0: bool) -> Path:
    stat = source_path.stat()
    safe_id = quote(model_id, safe="")
    sh_tag = "sh0" if force_sh0 else "shn"
    name = f"{safe_id}-m{stat.st_mtime_ns}-s{stat.st_size}-p{max_points}-{sh_tag}.ply"
    return Path(WEB_SPLAT_CACHE_DIR) / name


def _prepare_web_splat_model_file(model_id: str, source_path: Path) -> tuple[Path, str]:
    max_bytes, max_points = _web_splat_ply_limits()
    force_sh0 = _web_splat_force_sh0()

    try:
        source_size = source_path.stat().st_size
    except OSError:
        return source_path, "stat-failed"

    if source_size <= max_bytes:
        return source_path, "full"

    cache_path = _web_splat_cached_ply_path(
        model_id=model_id,
        source_path=source_path,
        max_points=max_points,
        force_sh0=force_sh0,
    )
    if cache_path.is_file():
        return cache_path, "cached-downsample"

    with _WEB_SPLAT_PREPARE_LOCK:
        if cache_path.is_file():
            return cache_path, "cached-downsample"

        try:
            from plyfile import PlyData, PlyElement

            logger.warning(
                "Preparing browser-sized web-splat PLY for model=%s (source=%s bytes, limit=%s bytes, max_points=%s)",
                model_id,
                source_size,
                max_bytes,
                max_points,
            )

            ply = PlyData.read(str(source_path), mmap="r")
            vertices = ply["vertex"].data
            num_points = int(len(vertices))

            if num_points <= max_points:
                return source_path, "full"

            stride = max(1, (num_points + max_points - 1) // max_points)
            sampled = vertices[::stride].copy()

            sh_mode = "sh-native"
            if force_sh0:
                sampled, stripped = _strip_vertex_dtype_to_sh0(sampled)
                sh_mode = "sh0" if stripped else "sh-native-missing-fields"

            out_vertex = PlyElement.describe(sampled, "vertex")
            out_ply = PlyData(
                [out_vertex],
                text=False,
                byte_order=ply.byte_order,
                comments=list(ply.comments),
                obj_info=list(ply.obj_info),
            )

            tmp_path = cache_path.with_suffix(".tmp")
            if tmp_path.exists():
                tmp_path.unlink()
            out_ply.write(str(tmp_path))
            os.replace(tmp_path, cache_path)

            logger.warning(
                "web-splat downsample ready for model=%s: %s -> %s points (source=%s bytes, output=%s bytes)",
                model_id,
                num_points,
                len(sampled),
                source_size,
                cache_path.stat().st_size,
            )
            return cache_path, f"downsampled-{sh_mode}"
        except Exception as exc:
            try:
                if "tmp_path" in locals() and tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            logger.exception("Failed to prepare downsampled web-splat PLY for model=%s: %s", model_id, exc)
            return source_path, "downsample-failed"


def _player_prefers_web_splat() -> bool:
    mode = os.environ.get("GS_PLAYER_BACKEND", "web-splat-gui").strip().lower()
    if mode in {"legacy", "server", "render"}:
        return False
    if mode in {"", "web-splat", "websplat", "auto", "web-splat-gui", "gui"}:
        return True
    if mode in {"web-splat-standalone", "standalone"}:
        return True
    logger.warning("Unknown GS_PLAYER_BACKEND=%s, defaulting to web-splat-gui player", mode)
    return True


def _player_standalone_web_splat() -> bool:
    mode = os.environ.get("GS_PLAYER_BACKEND", "web-splat-gui").strip().lower()
    return mode in {"web-splat-standalone", "standalone"}


def _web_splat_ready() -> tuple[bool, str]:
    base = Path(WEB_SPLAT_PUBLIC_DIR)
    if not base.is_dir():
        return False, f"missing directory: {base}"

    missing = [name for name in WEB_SPLAT_REQUIRED_FILES if not (base / name).is_file()]
    if missing:
        return False, f"missing build artifacts: {', '.join(missing)}"

    return True, "ok"


def _build_web_splat_target(model_id: str, include_scene: bool) -> str:
    encoded_model = quote(model_id, safe="")
    file_url = f"/web-splat-model/{encoded_model}"
    target = f"/web-splat/?file={quote(file_url, safe='/:')}"

    if include_scene:
        scene_url = f"/web-splat-scene/{encoded_model}"
        target += f"&scene={quote(scene_url, safe='/:')}"

    return target

async def models_page(request: Request):
    await ensure_started()
    return FileResponse("templates/models.html")

async def player_page(request: Request):
    await ensure_started()
    model_id = (request.query_params.get("modelId") or "").strip()
    if not model_id:
        return RedirectResponse("/models-ui", status_code=307)

    try:
        model = get_model(model_id=model_id)
    except KeyError:
        return JSONResponse({"error": f"unknown modelId={model_id}"}, status_code=404)

    if not _player_prefers_web_splat():
        return FileResponse("templates/player.html")

    ready, reason = _web_splat_ready()
    if not ready:
        logger.warning("web-splat unavailable (%s), falling back to legacy player", reason)
        return FileResponse("templates/player.html")

    if not _player_standalone_web_splat():
        return FileResponse("templates/player.html")

    scene_path = Path(model.model_path).parent / "cameras.json"
    target = _build_web_splat_target(model_id=model_id, include_scene=scene_path.is_file())
    return RedirectResponse(target, status_code=307)


async def player_legacy_page(request: Request):
    await ensure_started()
    return FileResponse("templates/player.html")


async def web_splat_model_file(request: Request):
    await ensure_started()
    model_id = request.path_params["model_id"]

    try:
        model = get_model(model_id=model_id)
    except KeyError:
        return PlainTextResponse("unknown model", status_code=404)

    model_path = Path(model.model_path)
    if not model_path.is_file():
        return PlainTextResponse("model file not found", status_code=404)

    served_path, source_mode = _prepare_web_splat_model_file(model_id=model_id, source_path=model_path)

    return FileResponse(
        str(served_path),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-WebSplat-Model-Source": source_mode,
        },
    )


async def web_splat_scene_file(request: Request):
    await ensure_started()
    model_id = request.path_params["model_id"]

    try:
        model = get_model(model_id=model_id)
    except KeyError:
        return PlainTextResponse("unknown model", status_code=404)

    scene_path = Path(model.model_path).parent / "cameras.json"
    if not scene_path.is_file():
        return PlainTextResponse("scene file not found", status_code=404)

    return FileResponse(
        str(scene_path),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )

async def player_dash_page(request: Request):
    await ensure_started()
    p = Path("templates/player_dash.html")
    logger.info("CWD=%s exists=%s abs=%s", os.getcwd(), p.exists(), p.resolve())
    return FileResponse("templates/player_dash.html")

async def get_list_of_all_available_models(request: Request):
    await ensure_started()
    models = await list_models()  
    return JSONResponse(models)

async def render_handler(request: Request):
    await ensure_started()

    data = await request.json()

    azimuth = float(data.get("angle", 180))
    elevation = float(data.get("elevation", 0))
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    z = float(data.get("z", 5.0))
    fx = float(data.get("fx", 1300.0))
    fy = float(data.get("fy", 800.0))
    cx = float(data.get("cx", 400.0))
    cy = float(data.get("cy", 300.0))
    width = int(data.get("width", 800))
    height = int(data.get("height", 600))
    profile = int(data.get("profile", 0))
    model = get_model(data.get("modelId"))

    loop = asyncio.get_running_loop()

    # 1) render raw in executor
    img_stream, render_ms = await loop.run_in_executor(
        RENDER_EXECUTOR,
        render_image_raw,
        azimuth, elevation, x, y, z, fx, fy, cx, cy, width, height, profile, model
    )

    # 2) encode OUTSIDE render_image_raw
    factor = 1 << max(0, min(3, int(profile)))
    stream_quality = max(50, 70 - (factor * 5))

    jpeg_bytes = await loop.run_in_executor(
        RENDER_EXECUTOR,
        lambda: encode_jpeg(img_stream, quality=stream_quality)
    )

    backend_name, _ = render_backend()

    return Response(
        jpeg_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Render-Time-Ms": f"{render_ms:.2f}",
            "X-Render-Backend": backend_name,
        },
    )

    
def to_response(result: HandlerResult):
    headers = result.headers or {}

    if result.payload is not None:
        return JSONResponse(result.payload, status_code=result.status, headers=headers)

    if result.text is not None:
        return PlainTextResponse(result.text, status_code=result.status, headers=headers)

    return Response(b"", status_code=result.status, headers=headers)
    
    
# POST /metrics/predict
async def metrics_predict(request):
    await ensure_started()
    try:
        body = await request.json()
    except Exception as e:
        return PlainTextResponse(f"Invalid JSON: {e}", status_code=400)

    result = await metrics_predict_logic(body)
    
    return to_response(result)


# POST /metrics/predict
async def save_movements(request):
    await ensure_started()
    try:
        body = await request.json()
    except Exception as e:
        return PlainTextResponse(f"Invalid JSON: {e}", status_code=400)

    result = await save_movement(body)
    
    return to_response(result)


async def export_experiment(request : Request):
    await ensure_started()
    file_name = request.path_params["file_name"]

    result = await export_experiment_data(file_name)
    
    if result.status != 200 or result.content is None:
        return Response(b"", status_code=result.status)

    return Response(
        result.content,
        status_code=result.status,
        media_type=result.media_type,
        headers=result.headers or {},
    )


def model_to_json(model) -> dict: 
    # Return ONLY metadata / status. 
    # Do NOT return tensors. 
    return { 
        "id": model.id, 
        "name": getattr(model, "name", model.id), 
        "isLoaded": bool(getattr(model, "is_loaded", False)) or bool(getattr(model, "loaded", False))
    }

MODEL_LOAD_LOCK = asyncio.Lock()

async def load_model(request: Request):
    await ensure_started()
    data = await request.json()
    model_id = data.get("modelId")
    if not model_id:
        return JSONResponse({"error": "modelId missing"}, status_code=400)

    model = get_model(model_id=model_id)
    if not model:
        return JSONResponse({"error": f"unknown modelId={model_id}"}, status_code=404)

    async with MODEL_LOAD_LOCK:
        backend_name, _ = render_backend()
        if not requires_tensor_model_load():
            logger.info(
                "Skipping tensor load for %s because backend=%s does not need tensor cache",
                model_id,
                backend_name,
            )
        else:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, model.load)

    return JSONResponse(model_to_json(model))


async def save_images(request: Request):
    await ensure_started()

    data = await request.json()

    experiment_name = data.get("experimentName")
    
    items = load_movements(experiment_name)
    
    for item in items:
        angle = item["angle"]
        elevation = item["elevation"] 
        x = item["x"]
        y = item["y"] 
        z = item["z"] 
        fx = item["fx"] 
        fy = item["fy"] 
        cx = item["cx"] 
        cy = item["cy"] 
        width = item["width"] 
        height = item["height"] 
        profile = item["profile"] 
        modelId = item["modelId"]
        model = get_model(modelId)

        loop = asyncio.get_running_loop()

        # 1) render raw in executor
        img_stream, render_ms = await loop.run_in_executor(
            RENDER_EXECUTOR,
            render_image_raw,
            angle, elevation, x, y, z, fx, fy, cx, cy, width, height, profile, model
        )

        # 2) encode OUTSIDE render_image_raw
        factor = 1 << max(0, min(3, int(profile)))
        stream_quality = max(50, 70 - (factor * 5))

        jpeg_bytes = await loop.run_in_executor(
            RENDER_EXECUTOR,
            lambda: encode_jpeg(img_stream, quality=stream_quality)
        )
        
        save_render_bytes(jpeg_bytes, str(modelId), base_name=experiment_name, type="jpg")

        png_bytes = await loop.run_in_executor(
            RENDER_EXECUTOR,
            lambda: encode_png(img_stream)
        )
        
        save_render_bytes(png_bytes, str(modelId), base_name=experiment_name, type= "png")

    return Response(
        headers={"Cache-Control": "no-store"},
    )
    
import json
from typing import Any

def load_movements(path: str | Path) -> list[dict[str, Any]]:
    """
    Load movement items from an NDJSON file.

    Each line must be a valid JSON object.
    Returns a list of dicts with the parameters defined in the file.
    """
    items: list[dict[str, Any]] = []

    path = Path(f"{EXPERIMENTS_DIR}/{path}/movements.ndjson")

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Invalid JSON in {path} at line {line_no}"
                ) from e

    print(items)
    return items


# POST /control
async def control(request: Request):
    await ensure_started()
    body = await request.json()

    # Start streamer lazily on first control message
    await STREAMER.ensure_started()

    # Accept camera + model updates
    await STREAMER.update_state(
        modelId=str(body.get("modelId", "")),
        angle=float(body.get("angle", 180)),
        elevation=float(body.get("elevation", 0)),
        x=float(body.get("x", 0)),
        y=float(body.get("y", 0)),
        z=float(body.get("z", 5.0)),
        fx=float(body.get("fx", 1300.0)),
        fy=float(body.get("fy", 800.0)),
        cx=float(body.get("cx", 400.0)),
        cy=float(body.get("cy", 300.0)),
    )

    return JSONResponse({
        "ok": True,
        "running": STREAMER.is_running(),
        "mpd": "/dash/live.mpd",
    })

# GET /dash/status
async def dash_status(request: Request):
    await ensure_started()
    return JSONResponse({
        "running": STREAMER.is_running(),
        "mpdExists": STREAMER.mpd_path.exists(),
        "mpd": "/static/dash/live.mpd",
    })

# POST /dash/stop
async def dash_stop(request: Request):
    await ensure_started()
    await STREAMER.stop()
    return JSONResponse({"ok": True})


async def dash_file(request: Request):
    rel = request.path_params["path"]

    # Prevent path traversal
    base = os.path.realpath(DASH_DIR)
    p = os.path.realpath(os.path.join(DASH_DIR, rel))
    if not (p == base or p.startswith(base + os.sep)):
        return PlainTextResponse("bad path", status_code=400)

    if not os.path.isfile(p):
        return PlainTextResponse("not found", status_code=404)

    resp = FileResponse(p)
    if p.endswith(".mpd"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    elif p.endswith(".m4s") or p.endswith(".mp4"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp