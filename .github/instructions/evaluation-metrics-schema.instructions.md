---
applyTo: "scripts/**,experiments.py,routes.py"
---

<!-- GENERATED — DO NOT EDIT. Source: AGENTS.md via tools/sync_agent_rules.py
     The 'Evaluation / metrics schema' section. Copilot's cloud agent and code review
     read the whole of AGENTS.md; this copy is for Copilot Chat, which
     reads only .github/. -->

## Evaluation / metrics schema

Interactive sessions write under gitignored runtime dirs:

- `experiment/<run>/` — per-session exports (zipped via `/export` /
  `experiments.export_experiment_data`)
- `captures/` — saved frames / SR images from the player UI
- `dash/` — live DASH segment output when that path is used

Offline metrics (`scripts/compute_metrics.py`) expect:

- A **test** folder of frames (e.g. historically `Metrics/"L2A Cascading Trace"`)
- A **ref** folder (e.g. `Metrics/Original`)
- Optional ffmpeg with libvmaf (`--ffmpeg`)

Headline columns: PSNR, SSIM, and VMAF when libvmaf is available. Output is
a CSV (`--out_csv`). `scripts/run_all_metrics.sh` walks ABR×trace folders;
note that committed `Metrics/` / `Results/` trees are **not** in this repo —
operators create them locally from exported experiments. Do not invent a
parallel `outputs/evaluation_*` layout from the modular archive branch.
