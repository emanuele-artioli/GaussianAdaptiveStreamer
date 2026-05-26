import asyncio, os
from urllib.parse import unquote
from models import list_models
from experiments import export_experiment_data, metrics_predict_logic, save_movement, HandlerResult
from logger import logger
from render import render_image_raw, save_render_bytes
from models import get_model, ensure_started
from concurrent.futures import ThreadPoolExecutor
from encoding import encode_jpeg, encode_png
from statics import EXPERIMENTS_DIR, DASH_DIR
from dash_streamer import STREAMER


RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=1)

from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse, Response, PlainTextResponse

async def models_page(request: Request):
    logger.info("Get models page.")
    await ensure_started()
    return FileResponse("templates/models.html")

async def player_page(request: Request):
    logger.info("Get jpeg player page.")
    await ensure_started()
    return FileResponse("templates/player.html")

async def player_wt_page(request: Request):
    logger.info("Get webtransport player page.")
    await ensure_started()
    return FileResponse("templates/player_wt.html")

async def player_dash_page(request: Request):
    logger.info("Get dash player page.")
    await ensure_started()
    p = Path("templates/player_dash.html")
    logger.info("CWD=%s exists=%s abs=%s", os.getcwd(), p.exists(), p.resolve())
    return FileResponse("templates/player_dash.html")

async def get_list_of_all_available_models(request: Request):
    logger.info("Get list of models.")
    await ensure_started()
    models = await list_models()  
    return JSONResponse(models)

async def render_handler(request: Request):
    logger.debug("Render image")
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

    return Response(
        jpeg_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Render-Time-Ms": f"{render_ms:.2f}"},
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
    logger.debug("Get metrics.")
    await ensure_started()
    try:
        body = await request.json()
    except Exception as e:
        return PlainTextResponse(f"Invalid JSON: {e}", status_code=400)

    result = await metrics_predict_logic(body)
    
    return to_response(result)


# POST /metrics/predict
async def save_movements(request):
    logger.debug("Save movement.")
    await ensure_started()
    try:
        body = await request.json()
    except Exception as e:
        return PlainTextResponse(f"Invalid JSON: {e}", status_code=400)

    result = await save_movement(body)
    
    return to_response(result)


async def export_experiment(request : Request):
    logger.info("Export experiment.")
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
    logger.info("Load model.")
    await ensure_started()
    data = await request.json()
    model_id = data.get("modelId")
    if not model_id:
        return JSONResponse({"error": "modelId missing"}, status_code=400)

    model = get_model(model_id=model_id)
    if not model:
        return JSONResponse({"error": f"unknown modelId={model_id}"}, status_code=404)

    async with MODEL_LOAD_LOCK:
        loop = asyncio.get_running_loop()

        await loop.run_in_executor(None, model.load)

    return JSONResponse(model_to_json(model))


async def save_images(request: Request):
    logger.info("Save image")
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
    logger.info("Load movement")
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

    logger.debug("Loaded %d movement records from %s", len(items), path)
    return items


# POST /control
async def control(request: Request):
    logger.info("Update control")
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
    logger.info("Get status")
    await ensure_started()
    return JSONResponse({
        "running": STREAMER.is_running(),
        "mpdExists": STREAMER.mpd_path.exists(),
        "mpd": "/static/dash/live.mpd",
    })

# POST /dash/stop
async def dash_stop(request: Request):
    logger.info("Stop")
    await ensure_started()
    await STREAMER.stop()
    return JSONResponse({"ok": True})


async def dash_file(request: Request):
    logger.info("Get dash file")
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


def parse_capture_filename(header_name: str) -> tuple[str, str, int]:
    if not header_name:
        raise ValueError("Missing file-name header")

    # Preferred format:
    #   exp=<encoded>__run=<encoded>__frame=<id>.png
    # Legacy fallback:
    #   <experiment>-<run>-<frame>.png
    if header_name.startswith("exp=") and "__run=" in header_name and "__frame=" in header_name:
        stem = header_name[:-4] if header_name.lower().endswith(".png") else header_name
        parts = stem.split("__")
        mapping = {}
        for part in parts:
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            mapping[k] = v

        exp_name = unquote(mapping.get("exp", "")).strip()
        run_name = unquote(mapping.get("run", "")).strip()
        frame_raw = mapping.get("frame", "").strip()
        if not exp_name or not run_name or frame_raw == "":
            raise ValueError(f"Invalid file-name header: {header_name}")
        return exp_name, run_name, int(frame_raw)

    legacy = header_name.split("-")
    if len(legacy) < 3:
        raise ValueError(f"Invalid file-name header: {header_name}")

    exp_name = legacy[0].strip()
    run_name = legacy[1].strip()
    frame_part = legacy[2].strip()
    if frame_part.lower().endswith(".png"):
        frame_part = frame_part[:-4]

    return exp_name, run_name, int(frame_part)


def load_movement_for_frame(exp_name: str, run_name: str, frame_id: int) -> dict[str, Any]:
    path = Path(EXPERIMENTS_DIR) / exp_name / run_name / "movements.ndjson"
    if not path.exists():
        raise FileNotFoundError(f"Missing movements file: {path}")

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in {path} at line {line_no}") from e

            if int(item.get("frameid", -1)) == int(frame_id):
                return item

    raise LookupError(f"frameid={frame_id} not found in {path}")


def write_experiment_frame_bytes(exp_name: str, run_name: str, category: str, frame_id: int, ext: str, payload: bytes) -> str:
    base_dir = Path(EXPERIMENTS_DIR) / exp_name / run_name / category
    base_dir.mkdir(parents=True, exist_ok=True)
    out_path = base_dir / f"frame-{int(frame_id):06d}.{ext}"
    with out_path.open("wb") as f:
        f.write(payload)
    return str(out_path)


def iter_movements(exp_name: str, run_name: str):
    path = Path(EXPERIMENTS_DIR) / exp_name / run_name / "movements.ndjson"
    if not path.exists():
        raise FileNotFoundError(f"Missing movements file: {path}")

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in {path} at line {line_no}") from e
            yield item


async def render_original_and_gt_for_movement(movement: dict[str, Any]) -> tuple[bytes, bytes]:
    model = get_model(movement["modelId"])
    loop = asyncio.get_running_loop()

    img_original, _ = await loop.run_in_executor(
        RENDER_EXECUTOR,
        render_image_raw,
        float(movement["angle"]),
        float(movement["elevation"]),
        float(movement["x"]),
        float(movement["y"]),
        float(movement["z"]),
        float(movement["fx"]),
        float(movement["fy"]),
        float(movement["cx"]),
        float(movement["cy"]),
        int(movement["width"]),
        int(movement["height"]),
        int(movement["profile"]),
        model,
    )

    factor = 1 << max(0, min(3, int(movement["profile"])))
    stream_quality = max(50, 70 - (factor * 5))
    original_jpg = await loop.run_in_executor(
        RENDER_EXECUTOR,
        lambda: encode_jpeg(img_original, quality=stream_quality)
    )

    img_gt, _ = await loop.run_in_executor(
        RENDER_EXECUTOR,
        render_image_raw,
        float(movement["angle"]),
        float(movement["elevation"]),
        float(movement["x"]),
        float(movement["y"]),
        float(movement["z"]),
        float(movement["fx"]),
        float(movement["fy"]),
        float(movement["cx"]),
        float(movement["cy"]),
        int(movement["width"]),
        int(movement["height"]),
        0,
        model,
    )
    gt_png = await loop.run_in_executor(
        RENDER_EXECUTOR,
        lambda: encode_png(img_gt)
    )

    return original_jpg, gt_png

async def receive_sr(request: Request):
    logger.info("Receive sampled SR image")
    await ensure_started()
    img = await request.body()

    try:
        exp_name, run_name, frame_id = parse_capture_filename(request.headers.get("file-name", ""))
    except Exception as e:
        return PlainTextResponse(f"Invalid file-name header: {e}", status_code=400)

    try:
        # Validate frame exists in movement log so SR files are always aligned with experiment metadata.
        _ = load_movement_for_frame(exp_name, run_name, frame_id)
        sr_path = write_experiment_frame_bytes(exp_name, run_name, "sr_png", frame_id, "png", img)

        logger.info(
            "Saved sampled SR frame exp=%s run=%s frame=%s sr=%s",
            exp_name, run_name, frame_id, sr_path
        )

        return JSONResponse(
            {
                "ok": True,
                "experiment": exp_name,
                "run": run_name,
                "frameid": frame_id,
                "paths": {"sr": sr_path},
            },
            status_code=200,
            headers={"Cache-Control": "no-store"},
        )
    except Exception as e:
        logger.exception("Failed saving sampled frame for /save_sr_image")
        return PlainTextResponse(f"Failed saving sampled frame: {e}", status_code=500)


async def materialize_sampled_frames(request: Request):
    logger.info("Materialize sampled original/gt frames")
    await ensure_started()

    try:
        body = await request.json()
    except Exception as e:
        return PlainTextResponse(f"Invalid JSON: {e}", status_code=400)

    exp_name = (body.get("originFolderName") or "").strip()
    run_name = (body.get("runFolderName") or "").strip()
    stride = int(body.get("stride") or 12)

    if not exp_name or not run_name:
        return PlainTextResponse("originFolderName and runFolderName are required", status_code=400)
    if stride <= 0:
        return PlainTextResponse("stride must be > 0", status_code=400)

    try:
        total_sampled = 0
        original_written = 0
        gt_written = 0

        for movement in iter_movements(exp_name, run_name):
            frame_id = int(movement.get("frameid", -1))
            if frame_id < 0 or (frame_id % stride) != 0:
                continue

            total_sampled += 1
            original_path = Path(EXPERIMENTS_DIR) / exp_name / run_name / "original_jpeg" / f"frame-{frame_id:06d}.jpg"
            gt_path = Path(EXPERIMENTS_DIR) / exp_name / run_name / "gt_png" / f"frame-{frame_id:06d}.png"

            # Idempotent: if both already exist, skip this frame.
            if original_path.exists() and gt_path.exists():
                continue

            original_jpg, gt_png = await render_original_and_gt_for_movement(movement)

            if not original_path.exists():
                write_experiment_frame_bytes(exp_name, run_name, "original_jpeg", frame_id, "jpg", original_jpg)
                original_written += 1
            if not gt_path.exists():
                write_experiment_frame_bytes(exp_name, run_name, "gt_png", frame_id, "png", gt_png)
                gt_written += 1

        logger.info(
            "Materialized sampled frames exp=%s run=%s stride=%s sampled=%s original_written=%s gt_written=%s",
            exp_name, run_name, stride, total_sampled, original_written, gt_written
        )

        return JSONResponse(
            {
                "ok": True,
                "experiment": exp_name,
                "run": run_name,
                "stride": stride,
                "sampled_frames": total_sampled,
                "original_written": original_written,
                "gt_written": gt_written,
            },
            status_code=200,
            headers={"Cache-Control": "no-store"},
        )
    except Exception as e:
        logger.exception("Failed materializing sampled frames")
        return PlainTextResponse(f"Failed materializing sampled frames: {e}", status_code=500)
    
async def receive_experiment_data(request: Request):
    await ensure_started()
    try:
        body = await request.json()
    except Exception as e:
        return PlainTextResponse(f"Invalid JSON: {e}", status_code=400)

    name = body.get("expName")
    fps = body.get("fps")
    latency = body.get("latency")

    f = Path(EXPERIMENTS_DIR) / name / "experiment_data.ndjson"
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as out:
        out.write("{\"fps\": %s,\"latency\": %s}" % (fps, latency))
        out.write("\n")
    return JSONResponse({"ok": True}, status_code=200)