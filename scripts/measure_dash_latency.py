#!/usr/bin/env python3
"""
Measure Low-Latency DASH/CMAF interactive latency for the TIGAS baseline.

Records version-aware pose -> ffmpeg-write latency from DashStreamer (avoids
keepalive false hits), then forms a playback lower bound with the configured
dash.js liveDelay and MPD minBufferTime.

Profiles:
  baseline — stock 100 ms segments / liveDelay (camera-ready table)
  ull      — ultra-LL: ~33 ms segments, GOP 2, PRT+UTC, liveDelay 50 ms

Bounds before trusting (ull):
  input_to_write_ms: best ~5-50, worst ~200-1000; alarm if mean <1 or >10000
  est_playback_ms: best ~80-150, worst ~300-2000; alarm if mean <50 (bug)
  contribution gate: est_playback p95 < 100 ms
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
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


PROFILES = {
    "baseline": {
        "DASH_SEG_DURATION": "0.1",
        "DASH_TARGET_LATENCY": "0.1",
        "DASH_GOP": "8",
        "DASH_WINDOW_SIZE": "8",
        "live_delay_ms": 100.0,
        "min_buffer_fallback_ms": 200.0,
    },
    "ull": {
        "DASH_SEG_DURATION": "0.033",
        "DASH_TARGET_LATENCY": "0.05",
        "DASH_GOP": "2",
        "DASH_WINDOW_SIZE": "16",
        "live_delay_ms": 50.0,
        "min_buffer_fallback_ms": 66.0,  # ~2× seg if MPD omits
    },
}

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


def parse_min_buffer_ms(mpd_text: str, fallback_ms: float) -> float:
    m = re.search(r'minBufferTime="PT([0-9.]+)S"', mpd_text)
    if m:
        val = float(m.group(1)) * 1000.0
        # ldash often writes PT0.0S; that is not a usable interactive buffer floor.
        if val > 0:
            return val
    return fallback_ms


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
    # Refresh knobs from env (profile applied in main before import... set on STREAMER).
    STREAMER.gop = int(os.environ.get("DASH_GOP", str(STREAMER.gop)))
    STREAMER.seg_duration = float(os.environ.get("DASH_SEG_DURATION", str(STREAMER.seg_duration)))
    STREAMER.target_latency = float(
        os.environ.get("DASH_TARGET_LATENCY", str(STREAMER.target_latency))
    )
    STREAMER.window_size = int(os.environ.get("DASH_WINDOW_SIZE", str(STREAMER.window_size)))

    await STREAMER.ensure_started()
    # Swap in instrumented loop.
    if STREAMER._task and not STREAMER._task.done():
        STREAMER._task.cancel()
        try:
            await STREAMER._task
        except asyncio.CancelledError:
            pass
    STREAMER._task = asyncio.create_task(instrumented_render_loop())


async def run(args: argparse.Namespace) -> int:
    profile = PROFILES[args.profile]
    for k, v in profile.items():
        if k.startswith("DASH_"):
            os.environ[k] = str(v)

    live_delay_ms = float(profile["live_delay_ms"])
    min_buffer_fallback_ms = float(profile["min_buffer_fallback_ms"])

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
    for _ in range(200):
        if any(not e["keepalive"] for e in write_events):
            break
        await asyncio.sleep(0.05)

    mpd_text = STREAMER.mpd_path.read_text() if STREAMER.mpd_path.exists() else ""
    min_buffer_ms = parse_min_buffer_ms(mpd_text, min_buffer_fallback_ms)
    has_prft = "ProducerReferenceTime" in mpd_text or "UTCTiming" in mpd_text

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
    # Re-read MPD before stop (still dynamic).
    if STREAMER.mpd_path.exists():
        mpd_text = STREAMER.mpd_path.read_text()
        min_buffer_ms = parse_min_buffer_ms(mpd_text, min_buffer_fallback_ms)
        has_prft = "ProducerReferenceTime" in mpd_text or "UTCTiming" in mpd_text
    await STREAMER.stop()

    if write_events:
        t_start = write_events[0]["t_wall_ms"]
        useful = [
            e for e in write_events
            if (not e["keepalive"]) and (e["t_wall_ms"] - t_start) >= 2000
        ]
    else:
        useful = []

    itw = [float(e["input_to_write_ms"]) for e in useful if e["input_to_write_ms"] > 0]
    est = [v + live_delay_ms + min_buffer_ms for v in itw]
    est_delay_only = [v + live_delay_ms for v in itw]

    itw_sum = summarize(itw)
    est_sum = summarize(est)
    delay_only_sum = summarize(est_delay_only)

    alarms: list[str] = []
    if itw_sum.get("n", 0) < 20:
        alarms.append(f"too few input_to_write samples ({itw_sum.get('n', 0)})")
    elif itw_sum["mean_ms"] < 1 or itw_sum["mean_ms"] > 10000:
        alarms.append(f"input_to_write mean {itw_sum['mean_ms']:.1f} out of bounds")
    if est_sum.get("n", 0) == 0:
        alarms.append("no est_playback samples")
    elif est_sum["mean_ms"] < 50:
        alarms.append(f"est_playback mean {est_sum['mean_ms']:.1f} < 50 ms (likely bug)")
    elif est_sum["mean_ms"] > 30000:
        alarms.append(f"est_playback mean {est_sum['mean_ms']:.1f} > 30 s")

    # Contribution gate (ull profile): p95 playback under 100 ms interactive budget.
    contribution_ok = (
        args.profile == "ull"
        and est_sum.get("n", 0) >= 20
        and est_sum.get("p95_ms", 1e9) < 100.0
        and not any(a.startswith("too few") or "bug" in a or "> 30" in a for a in alarms)
    )

    summary = {
        "profile": args.profile,
        "bounds_before_run": {
            "input_to_write_ms": {"best": "5-50 (ull) / 10-200 (baseline)", "alarm": "<1 or >10000"},
            "est_playback_ms": {
                "best": "80-150 (ull)",
                "worst": "300-2000",
                "contribution_gate": "p95 < 100 ms",
            },
        },
        "metric_definition": {
            "input_to_write_ms": (
                "wall clock from pose update to ffmpeg stdin write for that "
                "camera-state version (keepalive frames excluded)"
            ),
            "est_playback_ms": (
                f"input_to_write_ms + liveDelay ({live_delay_ms:.0f} ms) + "
                f"MPD minBufferTime ({min_buffer_ms:.0f} ms)"
            ),
            "est_playback_liveDelay_only_ms": (
                f"input_to_write_ms + liveDelay ({live_delay_ms:.0f} ms) only"
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
                "seg_duration": float(os.environ["DASH_SEG_DURATION"]),
                "target_latency": float(os.environ["DASH_TARGET_LATENCY"]),
                "gop": int(os.environ["DASH_GOP"]),
                "window_size": int(os.environ["DASH_WINDOW_SIZE"]),
                "ldash": 1,
                "write_prft": 1,
                "utc_timing_url": os.environ.get(
                    "DASH_UTC_TIMING_URL", "https://time.akamai.com/?iso"
                ),
                "liveDelay_player_ms": live_delay_ms,
                "minBufferTime_ms": min_buffer_ms,
                "mpd_has_utc_or_prft": has_prft,
                "codec": "h264_nvenc",
            },
        },
        "input_to_write_ms": itw_sum,
        "est_playback_ms": est_sum,
        "est_playback_liveDelay_only_ms": delay_only_sum,
        "alarms": alarms,
        "contribution_ok": contribution_ok,
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
    ap.add_argument("--profile", choices=sorted(PROFILES), default="baseline")
    ap.add_argument("--out", default="experiment/dash_cmaf_20260804")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
