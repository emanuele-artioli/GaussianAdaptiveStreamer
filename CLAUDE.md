# TIGAS

Modular 3DGS/4DGS remote-rendering research testbed: server-side Gaussian
Splatting renders a scene, streams it to a client over an ABR-controlled
transport, and an offline evaluation component sweeps quality/bitrate
tradeoffs. Six runtime stages (per `README.md` / `docs/ARCHITECTURE.md`):
input/control uplink, intelligence (pose prediction + ABR), rendering
wrapper, media coding/CMAF packaging, QUIC/MoQ transport + browser client,
and offline evaluation. Everything is contract-first and mockable so any
stage can be swapped or ablated independently — see
`docs/MODULE_IO_CONTRACTS.md` for the exact interfaces and
`docs/DATAFLOW.md` for the three execution modes (interactive, headless
replay, offline evaluation).

**Current reality (check before assuming a stage works):** the validated
path is headless runtime rendering (CPU and `gsplat_cuda` backends) plus
offline evaluation sweeps and ABR-profile comparison. The interactive
browser/WebTransport/MoQ path and the `docker/` per-module containers are
still scaffold-level — the Dockerfiles and `docker/compose.ablation.yml`
each just print a `TODO` placeholder, not a working service. Don't assume
either is runnable without checking first. `docs/REENTRY.md` has the
current up-to-date snapshot and open-work list; read it before planning new
work here.

**This file is the only rule file to edit by hand.** `AGENTS.md`,
`.agents/rules/tigas.md` (Antigravity) and
`.github/instructions/tigas.instructions.md` (Copilot) are *generated* from
it by `tools/sync_agent_rules.py`, which also inlines the host-wide
`~/.claude/CLAUDE.md` that only Claude Code loads automatically. Edit
`CLAUDE.md`, then re-run `python3 tools/sync_agent_rules.py` — a stale
generated file is detectable via `python3 tools/sync_agent_rules.py --check`
(exit 1), though nothing currently enforces that check in CI or a
pre-commit hook (see Testing/CI below).

## Entry points

Everything runs inside the `tigas` conda env
(`/home/itec/emanuele/.conda/envs/tigas`), with `src/` on `PYTHONPATH`
(the package is `tigas`, laid out under `src/tigas/`):

```
cd /home/itec/emanuele/TIGAS
conda activate tigas
PYTHONPATH=src python -m tigas.orchestration.run_headless \
  --ply-path "/path/to/scene.ply" --renderer-backend cpu --num-frames 120
PYTHONPATH=src python -m tigas.evaluation.run_evaluation \
  --ply-path "/path/to/scene.ply" --output-dir outputs/evaluation --num-frames 120
```

Prefer the wrapper scripts over hand-building the CLI — they set
`PYTHONPATH` and fill in the standard flags:

- `scripts/run_headless_ablation.sh <PLY> [MOVEMENT_TRACE] [NETWORK_TRACE] [BACKEND] [QUANT_BITS] [ABR_PROFILE] [TC_INTERFACE]`
  — runtime-only timing pass, no evaluation artifacts.
- `scripts/run_evaluation_sweep.sh <PLY> [MOVEMENT_TRACE] [NETWORK_TRACE] [OUTPUT_DIR] [ABR_PROFILE]`
  — full sparsity × resolution × quant-bits matrix, writes to `outputs/`.
- `scripts/run_abr_comparison.sh <PLY> [MOVEMENT_TRACE] [NETWORK_TRACE] [OUTPUT_DIR]`
  — runs the same traces through `throughput`/`bola`/`robustmpc` for a
  side-by-side comparison.
- `scripts/shape_network.sh` / `scripts/attach_ebpf.sh` — Linux `tc`
  shaping and eBPF packet-timestamp hooks; both are best-effort and need
  host privileges this box may not grant (`tc` failures are expected and
  non-fatal).

Standardized inputs are selected by file path *or* by bare name from a
repo-tracked directory (all small, checked into git — not gitignored, not
regenerated, safe to read/copy freely):

- Movement traces: `movement_traces/*.json` (`Circular`, `Linear`, `Random`)
- Network traces: `network_traces/*.csv` (`lte`, `lte_steps`, `lte_cascading`)
- ABR profiles: `abr_profiles/*.json` (`throughput`, `bola`, `robustmpc`)

`--renderer-backend gsplat_cuda` needs `torch`/`gsplat`/CUDA installed in
the `tigas` env in addition to the base deps — the `cpu` backend has no
such requirement and is the right default for a quick smoke run.

## Dependency management — known gap, follow pyproject.toml going forward

Host policy is `pyproject.toml` as the single source of truth for Python
deps, never a `requirements.txt` (see host-wide rules below). TIGAS
currently violates this: `pyproject.toml`'s `dependencies` list is empty,
and `requirements.txt` is what `README.md`, `docs/REENTRY.md`, and CI
(`.github/workflows/ablation-matrix.yml`) actually install from. Don't
compound the gap — when adding a new dependency, add it to
`pyproject.toml`'s `[project.dependencies]` (then `pip install -e .`) and
mirror the same pin into `requirements.txt` until CI is switched over,
rather than adding a bare `pip install` step anywhere.

## Testing

```
conda activate tigas
pytest -q               # or: PYTHONPATH=src pytest -q, from repo root
```

`pyproject.toml`'s `[tool.pytest.ini_options]` sets `pythonpath = ["src"]`
and `testpaths = ["tests"]` — no custom markers, no coverage gate, no
`conftest.py` fixtures defined yet. The suite is currently ~9 files / ~14
tests, all fast CPU-only contract and smoke checks (renderer/predictor/
media/ABR-profile/PLY-loader contracts, plus one ablation-runner smoke
test) — nothing needs a GPU or real assets to pass. CI
(`.github/workflows/ablation-matrix.yml`) runs `pytest -q` on every push/PR
across a small codec × predictor matrix, then a placeholder headless-run
step that only writes a manifest stub (not a real ablation run yet).

There is no `.pre-commit-config.yaml` in this repo and CI does not run
`tools/sync_agent_rules.py --check` — unlike pointstream/presley, nothing
currently blocks a commit or a CI run on stale generated agent-rule files.
If pre-commit gets adopted here later, add a `sync-agent-rules` hook
entry matching pointstream's `.pre-commit-config.yaml`.

For new tests, use the generic `test-design` skill (no TIGAS-specific
tiers/markers exist to layer on top of it — plain `pytest -q` is the whole
story right now).

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

## `outputs/` — deletion is unrecoverable

`outputs/` is gitignored (`.gitignore`) and currently holds several GB
across prior evaluation/ablation/headless runs — each one costs real
render + encode time to reproduce, and none of it is in git history. A
`~/.agent-rules/scripts/guard-rm.py` PreToolUse hook (configured in this
repo's `.claude/settings.json`) blocks `rm` against the whole `outputs/`
tree; deleting one specific `outputs/<run-name>/` subdirectory stays
allowed. `movement_traces/`, `network_traces/`, and `abr_profiles/` are
small and checked into git (not gitignored), so they don't need the same
protection — losing one is a `git checkout` away, not a re-render.

## Concurrent sessions & git hygiene

This checkout is shared across multiple agent sessions at once — several
`.claude/worktrees/agent-*` directories already coexist here, each an
isolated worktree with its own branch. A branch alone does not isolate a
session; two agents sharing one working directory share one HEAD. For any
substantive change, work in a dedicated worktree + branch
(`git worktree add ../wt-tigas/<slug> -b <type>/<slug>`) rather than
editing directly in the main checkout. Before deleting any branch, read it
first (`git log main..<branch>`) and tag-and-push
(`git tag archive/<branch>`) if it holds work `main` doesn't have — see the
host-wide git rules below for the full protocol.

## Where to look for more

- Architecture, module contracts, dataflow, ablation matrix design →
  `docs/ARCHITECTURE.md`, `docs/MODULE_IO_CONTRACTS.md`, `docs/DATAFLOW.md`,
  `docs/ABLATION_WORKFLOW.md`
- Resuming after a break, current open-work priority list → `docs/REENTRY.md`
- Original spec / success criteria → `docs/blueprint.md`
- Choosing and writing tests → `test-design` skill
- Summarizing/comparing `outputs/evaluation_*` runs → `results-report`
  skill (read the "Evaluation output schema" section above first)
