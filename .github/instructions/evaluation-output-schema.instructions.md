---
applyTo: "src/**,scripts/**"
---

<!-- GENERATED — DO NOT EDIT. Source: AGENTS.md via tools/sync_agent_rules.py
     The 'Evaluation output schema' section. Copilot's cloud agent and code review
     read the whole of AGENTS.md; this copy is for Copilot Chat, which
     reads only .github/. -->

## Evaluation output schema

`tigas.evaluation.run_evaluation` writes to a run directory (default
`outputs/evaluation`, or whatever `--output-dir` / the wrapper scripts
pass — sweep runs commonly use a `outputs/evaluation_<label>/` naming
convention, e.g. `outputs/evaluation_bola/`):

- Per-config subdirectory (timestamp + config-encoded name, e.g.
  `20260323_140237_full_960x540_s300000_q8/`): `frames/frame_*.ppm`,
  `frame_metrics.csv` (per-frame `ssim_vs_full`, `coverage`, ...),
  `summary.json`, `headless_render.mp4` (requires `ffmpeg` on `PATH`).
- Run-level `evaluation_report.json` and the headline artifacts:
  `tradeoff_curve.csv` / `tradeoff_curve.md`, one row per config with
  resolution, ABR policy, LOD, sparsity, quant bits, point count, `SSIM vs
  full`, coverage, render mean ms, and FPS.

`SSIM vs full` (quality proxy against the same scene rendered at
`sparsity=1.0, quant_bits=8`) is the headline quality metric; render
mean ms / FPS are the headline cost metrics. This is the schema the
generic `results-report` skill should read against when summarizing a
`outputs/evaluation_*` run — no TIGAS-specific skill wrapper exists or is
needed beyond this section.

A 32-config sweep (the default sparsity × resolution × quant-bits matrix)
currently completes in well under 10 minutes on this host — these are not
multi-hour GPU training jobs, so `run_evaluation_sweep.sh` and
`run_abr_comparison.sh` are fine as ordinary foreground/backgrounded
`Bash` calls; there was no case for wiring in the `gpu-job-runner` agent
here. If a future sweep (more configs, `gsplat_cuda` at scale, a real
interactive/MoQ benchmark) grows past that, apply the host-wide
hourly-checkpoint rule below rather than assuming current run times hold.
