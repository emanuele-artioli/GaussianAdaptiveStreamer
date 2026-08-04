import asyncio
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from logger import logger
from models import get_model
from render import render_image_raw
from statics import DASH_DIR

FFMPEG = os.environ.get("FFMPEG", "/usr/local/bin/ffmpeg")
# Akamai ISO clock; required so ffmpeg honors write_prft / target_latency.
UTC_TIMING_URL = os.environ.get("DASH_UTC_TIMING_URL", "https://time.akamai.com/?iso")


@dataclass
class CameraState:
    modelId: str = ""
    angle: float = 180.0
    elevation: float = 0.0
    x: float = 0.0
    y: float = 0.0
    z: float = 5.0
    fx: float = 1300.0
    fy: float = 800.0
    cx: float = 400.0
    cy: float = 300.0
    width: int = 1280
    height: int = 720
    profile: int = 0
    input_ts: float = 0.0


class DashStreamer:
    """
    One ffmpeg process.
    Render loop pushes raw RGB frames (WxHx3) to ffmpeg stdin.
    ffmpeg writes MPD + CMAF segments into DASH_DIR.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state = CameraState()
        self._task: Optional[asyncio.Task] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._ffmpeg_log_task: Optional[asyncio.Task] = None
        self._running = False

        self.out_dir = Path(DASH_DIR)
        self.mpd_path = self.out_dir / "live.mpd"

        self._state_version = 0
        self._state_event = asyncio.Event()
        self._render_executor = ThreadPoolExecutor(max_workers=1)

        self.fps = 30

        self.reps = [
            {"w": 800, "h": 600, "br": "1200k"},
        ]

        # Packager knobs (overridable via env before ensure_started).
        self.gop = int(os.environ.get("DASH_GOP", "8"))
        self.seg_duration = float(os.environ.get("DASH_SEG_DURATION", "0.1"))
        self.target_latency = float(os.environ.get("DASH_TARGET_LATENCY", "0.1"))
        self.window_size = int(os.environ.get("DASH_WINDOW_SIZE", "8"))
        self.utc_timing_url = os.environ.get("DASH_UTC_TIMING_URL", UTC_TIMING_URL)

    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def update_state(self, **kwargs) -> None:
        async with self._lock:
            changed = False
            for k, v in kwargs.items():
                if hasattr(self._state, k) and getattr(self._state, k) != v:
                    setattr(self._state, k, v)
                    changed = True

            if changed:
                self._state.input_ts = time.time()
                self._state_version += 1
                self._state_event.set()

    async def ensure_started(self) -> None:
        async with self._lock:
            if self.is_running():
                return

            self.out_dir.mkdir(parents=True, exist_ok=True)

            gop = max(1, int(self.gop))
            seg_duration = max(0.033, float(self.seg_duration))
            target_latency = max(0.033, float(self.target_latency))
            window_size = max(2, int(self.window_size))

            ffmpeg = shutil.which(FFMPEG) if os.path.sep not in FFMPEG else FFMPEG
            if not ffmpeg or not Path(ffmpeg).exists():
                raise RuntimeError(f"ffmpeg not found: {FFMPEG}")

            for p in self.out_dir.glob("*"):
                try:
                    p.unlink()
                except Exception:
                    pass

            max_w = max(r["w"] for r in self.reps)
            max_h = max(r["h"] for r in self.reps)
            self._state.width = max_w
            self._state.height = max_h

            split_n = len(self.reps)
            split_labels = "".join(f"[v{i}]" for i in range(split_n))
            filter_parts = [f"[0:v]split={split_n}{split_labels};"]
            for i, r in enumerate(self.reps):
                filter_parts.append(f"[v{i}]scale={r['w']}:{r['h']}[v{i}o];")
            filter_complex = "".join(filter_parts).rstrip(";")

            # Small encoder buffer for LL; scale lightly with bitrate.
            bufsize = "500k"

            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "warning",
                "-progress", "pipe:2",
                "-nostats",
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "-s:v", f"{max_w}x{max_h}",
                "-r", str(self.fps),
                "-i", "pipe:0",
                "-filter_complex", filter_complex,
            ]

            for i, r in enumerate(self.reps):
                cmd += [
                    "-map", f"[v{i}o]",
                    "-c:v", "h264_nvenc",
                    "-pix_fmt", "yuv420p",
                    "-preset", "p1",
                    "-tune", "ll",
                    "-rc", "cbr",
                    "-b:v", r["br"],
                    "-maxrate", r["br"],
                    "-bufsize", bufsize,
                    "-g", str(gop),
                    "-keyint_min", str(gop),
                    "-sc_threshold", "0",
                ]

            cmd += [
                "-f", "dash",
                "-use_template", "1",
                "-use_timeline", "1",
                "-window_size", str(window_size),
                "-extra_window_size", str(window_size),
                "-remove_at_exit", "0",
                "-seg_duration", str(seg_duration),
                "-frag_type", "every_frame",
                "-ldash", "1",
                "-streaming", "1",
                # PRT + UTC timing required for target_latency to take effect.
                "-utc_timing_url", self.utc_timing_url,
                "-write_prft", "1",
                "-target_latency", str(target_latency),
                "-start_number", "1",
                "-adaptation_sets", "id=0,streams=0",
                "-init_seg_name", "init-$RepresentationID$.mp4",
                "-media_seg_name", "chunk-$RepresentationID$-$Number%05d$.m4s",
                str(self.mpd_path),
            ]

            logger.info("Starting ffmpeg DASH: %s", " ".join(cmd))

            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )

            self._running = True
            self._state_event.set()
            self._task = asyncio.create_task(self._render_loop())
            self._ffmpeg_log_task = asyncio.create_task(self._read_ffmpeg_logs())

    async def stop(self) -> None:
        async with self._lock:
            self._running = False
            self._state_event.set()

            if self._task:
                self._task.cancel()

            if self._ffmpeg_log_task:
                self._ffmpeg_log_task.cancel()

            if self._proc and self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass

            if self._proc:
                try:
                    self._proc.terminate()
                except Exception:
                    pass

            self._proc = None
            self._task = None
            self._ffmpeg_log_task = None

    async def _render_loop(self) -> None:
        assert self._proc is not None and self._proc.stdin is not None

        stdin = self._proc.stdin
        loop = asyncio.get_running_loop()

        last_log = 0.0
        last_sent_version = -1
        last_keepalive_at = 0.0

        while self._running:
            try:
                try:
                    await asyncio.wait_for(self._state_event.wait(), timeout=1.0 / self.fps)
                except asyncio.TimeoutError:
                    pass

                while self._running:
                    async with self._lock:
                        s = CameraState(**self._state.__dict__)
                        version = self._state_version
                        self._state_event.clear()

                    if not s.modelId:
                        await asyncio.sleep(0.05)
                        break

                    now = time.perf_counter()
                    force_keepalive = (now - last_keepalive_at) >= (1.0 / self.fps)

                    if version == last_sent_version and not force_keepalive:
                        break

                    t0 = time.perf_counter()
                    model = get_model(s.modelId)

                    img, render_ms = await loop.run_in_executor(
                        self._render_executor,
                        render_image_raw,
                        s.angle,
                        s.elevation,
                        s.x,
                        s.y,
                        s.z,
                        s.fx,
                        s.fy,
                        s.cx,
                        s.cy,
                        s.width,
                        s.height,
                        s.profile,
                        model,
                    )

                    async with self._lock:
                        if version < self._state_version:
                            continue

                    try:
                        stdin.write(img.tobytes())
                        await stdin.drain()
                        last_sent_version = version
                        last_keepalive_at = time.perf_counter()
                    except Exception:
                        logger.exception("ffmpeg stdin write failed")
                        self._running = False
                        return

                    log_now = time.time()
                    if log_now - last_log > 2.0:
                        total_ms = (time.perf_counter() - t0) * 1000.0
                        input_lag_ms = (time.time() - s.input_ts) * 1000.0 if s.input_ts else 0.0
                        logger.info(
                            "DASH render %.2f ms total %.2f ms input-to-write %.2f ms version=%d mpd=%s",
                            render_ms,
                            total_ms,
                            input_lag_ms,
                            version,
                            self.mpd_path,
                        )
                        last_log = log_now

                    async with self._lock:
                        if version == self._state_version:
                            break

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("render loop failed")
                await asyncio.sleep(0.05)

    async def _read_ffmpeg_logs(self) -> None:
        if not self._proc or not self._proc.stderr:
            return

        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                logger.info("ffmpeg: %s", line.decode("utf-8", "replace").strip())
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("ffmpeg log reader failed")


STREAMER = DashStreamer()