#!/usr/bin/env python3
"""
Measure Low-Latency DASH/CMAF interactive latency for the TIGAS baseline.

Records version-aware pose -> ffmpeg-write latency from DashStreamer (avoids
keepalive false hits), then forms a playback lower bound with the configured
dash.js liveDelay and MPD minBufferTime.

Bounds before trusting:
  input_to_write_ms: best ~10-200, worst ~500-2000; alarm if mean <1 or >10000
  est_playback_ms (= input_to_write + liveDelay + minBuffer): best ~300-600,
    worst ~800-5000; alarm if mean <100 or >30000; need max >= 500 for claim
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("FFMPEG", "/opt/local/bin/ffmpeg")

from dash_streamer import STREAMER, CameraState
from models import get_model, init_model_registry
from render import render_image_raw


LIVE_DELAY_MS = 100.0
MIN_BUFFER_MS = 200.0  # ffmpeg MPD minBufferTime PT0.2S under these flags
WINDOW = 8  # stock dash_streamer.py setting


write_events: list[dict] = []


def summarize(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    vals = sorted(vals)
    n = len(vals)
    return {
        "n": n,
        "mean_ms": statistics.mean(vals),
        "median_ms": statistics.median(vals),
        "p95_ms": vals[min(n - 1, int(0.95 * (n - 1)))],
        "max_ms": max(vals),
        "min_ms": min(vals),
    }


async def instrumented_render_loop() -> None:
    assert STREAMER._proc is not None and STREAMER._proc.stdin is not None
    stdin = STREAMER._proc.stdin
    loop = asyncio.get_running_loop()
    last_sent_version = -1
    last_keepalive_at = 0.0

    while STREAMER._running:
        try:
            try:
                await asyncio.wait_for(STREAMER._state_event.wait(), timeout=1.0 / STREAMER.fps)
            except asyncio.TimeoutError:
                pass

            while STREAMER._running:
                async with STREAMER._lock:
                    s = CameraState(**STREAMER._state.__dict__)
                    version = STREAMER._state_version
                    STREAMER._state_event.clear()

                if not s.modelId:
                    await asyncio.sleep(0.05)
                    break

                now = time.perf_counter()
                force_keepalive = (now - last_keepalive_at) >= (1.0 / STREAMER.fps)
                if version == last_sent_version and not force_keepalive:
                    break

                is_keepalive = version == last_sent_version
                t0 = time.perf_counter()
                model = get_model(s.modelId)
                img, render_ms = await loop.run_in_executor(
                    STREAMER._render_executor,
                    render_image_raw,
                    s.angle, s.elevation, s.x, s.y, s.z,
                    s.fx, s.fy, s.cx, s.cy, s.width, s.height, s.profile, model,
                )

                async with STREAMER._lock:
                    if version < STREAMER._state_version:
                        continue

                try:
                    stdin.write(img.tobytes())
                    await stdin.drain()
                    last_sent_version = version
                    last_keepalive_at = time.perf_counter()
                    input_to_write = (time.time() - s.input_ts) * 1000.0 if s.input_ts else 0.0
                    write_events.append({
                        "t_wall_ms": int(time.time() * 1000),
                        "version": version,
                        "keepalive": is_keepalive,
                        "render_ms": render_ms,
                        "input_to_write_ms": input_to_write,
                        "total_ms": (time.perf_counter() - t0) * 1000.0,
                    })
                except Exception:
                    STREAMER._running = False
                    return

                async with STREAMER._lock:
                    if version == STREAMER._state_version:
                        break
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.05)


async def start_streamer() -> None:
    async with STREAMER._lock:
        if STREAMER.is_running():
            return
        STREAMER.out_dir.mkdir(parents=True, exist_ok=True)
        for p in STREAMER.out_dir.glob("*"):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        max_w = max(r["w"] for r in STREAMER.reps)
        max_h = max(r["h"] for r in STREAMER.reps)
        STREAMER._state.width = max_w
        STREAMER._state.height = max_h
        ffmpeg = os.environ["FFMPEG"]
        gop = 8
        filter_complex = f"[0:v]split=1[v0];[v0]scale={max_w}:{max_h}[v0o]"
        br = STREAMER.reps[0]["br"]
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "warning",
            "-progress", "pipe:2", "-nostats",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s:v", f"{max_w}x{max_h}", "-r", str(STREAMER.fps),
            "-i", "pipe:0", "-filter_complex", filter_complex,
            "-map", "[v0o]", "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p",
            "-preset", "p1", "-tune", "ll", "-rc", "cbr",
            "-b:v", br, "-maxrate", br, "-bufsize", "2M",
            "-g", str(gop), "-keyint_min", str(gop),
            "-f", "dash", "-use_template", "1", "-use_timeline", "1",
            "-window_size", str(WINDOW), "-extra_window_size", str(WINDOW),
            "-remove_at_exit", "0",
            "-seg_duration", "0.1", "-frag_type", "every_frame",
            "-ldash", "1", "-streaming", "1", "-target_latency", "0.1",
            "-start_number", "1", "-adaptation_sets", "id=0,streams=0",
            "-init_seg_name", "init-$RepresentationID$.mp4",
            "-media_seg_name", "chunk-$RepresentationID$-$Number%05d$.m4s",
            str(STREAMER.mpd_path),
        ]
        from logger import logger
        logger.info("Starting ffmpeg DASH probe: %s", " ".join(cmd))
        STREAMER._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        STREAMER._running = True
        STREAMER._state_event.set()
        STREAMER._task = asyncio.create_task(instrumented_render_loop())
        STREAMER._ffmpeg_log_task = asyncio.create_task(STREAMER._read_ffmpeg_logs())


async def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    init_model_registry()
    get_model(args.model).load()
    poses = json.loads(Path(args.trace).read_text())

    await start_streamer()
    p0 = poses[0]
    await STREAMER.update_state(
        modelId=args.model,
        angle=float(p0["angle"]), elevation=float(p0["elevation"]),
        x=float(p0["x"]), y=float(p0["y"]), z=float(p0["z"]),
        fx=float(p0["fx"]), fy=float(p0["fy"]),
        cx=float(p0["cx"]), cy=float(p0["cy"]),
    )
    # warmup until first non-keepalive write
    for _ in range(200):
        if any(not e["keepalive"] for e in write_events):
            break
        await asyncio.sleep(0.05)

    t0 = time.perf_counter()
    i = 0
    while time.perf_counter() - t0 < args.duration and i < len(poses):
        pose = poses[i]
        target = pose.get("tMs", i * 33) / 1000.0
        now = time.perf_counter() - t0
        if now < target:
            await asyncio.sleep(min(0.05, target - now))
        await STREAMER.update_state(
            modelId=args.model,
            angle=float(pose["angle"]), elevation=float(pose["elevation"]),
            x=float(pose["x"]), y=float(pose["y"]), z=float(pose["z"]),
            fx=float(pose["fx"]), fy=float(pose["fy"]),
            cx=float(pose["cx"]), cy=float(pose["cy"]),
        )
        i += 1
        while i < len(poses) and poses[i].get("tMs", 0) / 1000.0 < (time.perf_counter() - t0):
            i += 1

    await asyncio.sleep(1.0)
    await STREAMER.stop()

    # Drop warm-up: first 2s of writes
    if write_events:
        t_start = write_events[0]["t_wall_ms"]
        useful = [
            e for e in write_events
            if (not e["keepalive"]) and (e["t_wall_ms"] - t_start) >= 2000
        ]
    else:
        useful = []

    itw = [float(e["input_to_write_ms"]) for e in useful if e["input_to_write_ms"] > 0]
    est = [v + LIVE_DELAY_MS + MIN_BUFFER_MS for v in itw]
    # Also report without minBuffer (liveDelay only) for transparency
    est_delay_only = [v + LIVE_DELAY_MS for v in itw]

    itw_sum = summarize(itw)
    est_sum = summarize(est)
    delay_only_sum = summarize(est_delay_only)

    alarms: list[str] = []
    if itw_sum.get("n", 0) < 20:
        alarms.append(f"too few input_to_write samples ({itw_sum.get('n', 0)})")
    else:
        if itw_sum["mean_ms"] < 1 or itw_sum["mean_ms"] > 10000:
            alarms.append(f"input_to_write mean {itw_sum['mean_ms']:.1f} out of bounds")
    if est_sum.get("n", 0) == 0:
        alarms.append("no est_playback samples")
    else:
        if est_sum["mean_ms"] < 100 or est_sum["mean_ms"] > 30000:
            alarms.append(f"est_playback mean {est_sum['mean_ms']:.1f} out of bounds")
        if est_sum["max_ms"] < 500:
            alarms.append(f"est_playback max {est_sum['max_ms']:.1f} never exceeds 500 ms")

    summary = {
        "bounds_before_run": {
            "input_to_write_ms": {"best": "10-200", "worst": "500-2000", "alarm": "<1 or >10000"},
            "est_playback_ms": {
                "best": "300-600",
                "worst": "800-5000",
                "alarm": "<100 or >30000; max must be >=500",
            },
        },
        "metric_definition": {
            "input_to_write_ms": (
                "wall clock from pose update to ffmpeg stdin write for that "
                "camera-state version (keepalive frames excluded)"
            ),
            "est_playback_ms": (
                f"input_to_write_ms + liveDelay ({LIVE_DELAY_MS:.0f} ms) + "
                f"MPD minBufferTime ({MIN_BUFFER_MS:.0f} ms); lower bound on "
                "dash.js playback delay under /player-dash settings"
            ),
            "est_playback_liveDelay_only_ms": (
                f"input_to_write_ms + liveDelay ({LIVE_DELAY_MS:.0f} ms) only"
            ),
        },
        "config": {
            "model": args.model,
            "trace": args.trace,
            "duration_s": args.duration,
            "ffmpeg": os.environ.get("FFMPEG"),
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "python": sys.executable,
            "dash_flags": {
                "seg_duration": 0.1,
                "target_latency": 0.1,
                "ldash": 1,
                "liveDelay_player": LIVE_DELAY_MS / 1000.0,
                "minBufferTime_s": MIN_BUFFER_MS / 1000.0,
                "window_size": WINDOW,
                "codec": "h264_nvenc",
            },
        },
        "input_to_write_ms": itw_sum,
        "est_playback_ms": est_sum,
        "est_playback_liveDelay_only_ms": delay_only_sum,
        "alarms": alarms,
        "citable": len(alarms) == 0 and est_sum.get("n", 0) >= 20,
        "n_write_events_total": len(write_events),
        "n_pose_writes_used": len(useful),
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with (out_dir / "write_events.csv").open("w", newline="") as f:
        fields = ["t_wall_ms", "version", "keepalive", "render_ms", "input_to_write_ms", "total_ms"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for e in write_events:
            w.writerow(e)

    print(json.dumps(summary, indent=2))
    return 0 if summary["citable"] else 2


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="train")
    ap.add_argument("--trace", default="TestMovements/NTHU/train/user3_train.json")
    ap.add_argument("--duration", type=float, default=45.0)
    ap.add_argument("--out", default="experiment/dash_cmaf_20260804")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
