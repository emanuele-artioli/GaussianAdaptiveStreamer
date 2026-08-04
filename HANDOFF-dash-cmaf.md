# Handoff: DASH/CMAF empirical results for TIGAS camera-ready

**Status (2026-08-04):** Measurement **done and folded into** `68c9198c30495c4e43526835/main.tex` (Motivating Example table). Sync/push Overleaf when ready; watch 8-page limit after Overleaf rebuild.

---

## Results (citable)

Run dir (gitignored): `experiment/dash_cmaf_20260804/` (`summary.json`, `write_events.csv`, `NOTES.md`).

| Metric | Mean | p95 | Max |
|--------|------|-----|-----|
| Pose→encode (ms) | 6.2 | 6.8 | 477.7 |
| Playback lower bound (ms) = write + liveDelay 100 + minBuffer 200 | 306.2 | 306.8 | **777.7** |

LiveDelay-only max is **577.7 ms** (also >500). Bounds were stated before the run; no hard alarms.

Setup: GPU1, conda `tigas`, `train` PLY from `Datasets/3DGS`, EyeNavGS `NTHU/user3`, 45 s, stock LL-DASH ffmpeg flags. Headless Chromium MSE stalled; numbers are version-aware pose→ffmpeg-write plus configured player delay/buffer (lower bound).

Script: `scripts/measure_dash_latency.py`.

---

## Remaining for human / Overleaf

1. Pull/edit on Overleaf; confirm still ≤8 pages with the new table.
2. Optional cosmetic: Fig.1 legend Streamer→TIGAS (separate item).
3. Do not reopen WebRTC quantitative study.
