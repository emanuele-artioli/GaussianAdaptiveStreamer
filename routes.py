import asyncio, os
from models import list_models
from experiments import export_experiment_data, metrics_predict_logic, save_movement, HandlerResult
from logger import logger
from render import render_backend, render_image_raw, save_render_bytes, using_preview_fallback
from models import get_model, ensure_started
from concurrent.futures import ThreadPoolExecutor
from encoding import encode_jpeg, encode_png
from statics import EXPERIMENTS_DIR, DASH_DIR
from dash_streamer import STREAMER


RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=1)

from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse, Response, PlainTextResponse

async def models_page(request: Request):
    await ensure_started()
    return FileResponse("templates/models.html")

async def player_page(request: Request):
    await ensure_started()
    return FileResponse("templates/player.html")

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
        if using_preview_fallback():
            logger.info("Skipping tensor load for %s because preview fallback backend is active", model_id)
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
from pathlib import Path
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