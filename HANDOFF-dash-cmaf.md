# Handoff: DASH/CMAF empirical results for TIGAS camera-ready

**Status (2026-08-04):** Camera-ready baseline table pushed to Overleaf (`e91017c`). ULL stress-test **failed** the contribution gate (composed p95 ≈ 124 ms &gt; 100 ms). Do not claim ULL DASH as a paper contribution.

---

## Pushed

| Where | Commit | What |
|-------|--------|------|
| Overleaf paper | `e91017c` | Motivating Example `tab:dash_cmaf_latency` |
| GitHub `emanuele-artioli/TIGAS` | `ffd0b31` (+ follow-up for ULL packager) | measure script + handoff; then PRT/UTC + env knobs |

Confirm ≤8 pages on Overleaf after rebuild.

---

## Baseline results (table in paper)

`experiment/dash_cmaf_20260804/` — pose→encode mean 6.2 / max 478; playback lower bound mean 306 / max 778.

## ULL tune (no contribution)

`experiment/dash_cmaf_ull_20260804/` — seg 33 ms, GOP 2, liveDelay 50 ms, PRT+UTC on. Composed playback p95 **123.9 ms** (gate was &lt;100). Notes in that dir.

Code improvements kept: packager honors `target_latency` via UTC/PRT; `DASH_*` env knobs; player `?liveDelay=`.
