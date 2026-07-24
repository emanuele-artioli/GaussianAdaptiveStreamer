---
applyTo: '**'
---

<!-- GENERATED from CLAUDE.md by tools/sync_agent_rules.py — DO NOT EDIT.
     Edit CLAUDE.md and re-run the script; a pre-commit hook checks this. -->

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

---

# Host-wide rules

These apply to every project on this host. Claude Code loads them
automatically; they are inlined here for agents that do not.

## Global environment notes

These apply across all projects/sessions on this host, not just one repo's
CLAUDE.md. **This file is the register of things that have gone wrong more
than once** — if a mistake happens twice, it belongs here, phrased as the rule
that prevents it rather than the story of the failure.

## Shared agent rules — single source of truth

Imported by reference (`@` syntax) from each coding agent's own rules file —
currently `~/.claude/CLAUDE.md` and `~/.gemini/GEMINI.md`. Edit **only this
file** for anything that should apply to every agent on this host. Put
agent-specific mechanics (tool names, invocation syntax, that agent's own
conventions) in the importing file instead, not here — this file stays
tool-agnostic so every importer can use it as-is.

### The host

Shared remote Linux **GPU server, no root/sudo/apt**, headless. Home is
`/home/itec/emanuele`. Install extra tooling with conda (Miniconda at
`/usr/local/miniconda3`) into a *separate* env — never into a project's
pinned env, because several forked third-party models are version-sensitive
and a stray `pip install` silently breaks them. Being headless, save media
and plots to disk; `cv2.imshow()`/`plt.show()` never works here.

### Python dependency management

Manage Python packages through `pyproject.toml`, not ad-hoc `pip install` in
the terminal. `environment.yaml` is reserved for bootstrapping heavy
CUDA/GPU binaries only (drivers, PyTorch wheels, compiled packages) — never
fall back to a `requirements.txt` file.

### GitHub CLI (gh)

`gh` is installed at `~/emanuele/bin/gh` (on `PATH` in every shell on this
host) and authenticated as `emanuele-artioli` via `gh auth login`
(credentials in `~/.config/gh/hosts.yml`, not tied to any one project).
Available in every project on this host — install/auth doesn't need
repeating.

**Use it proactively after every push to a repo with GitHub Actions (or
any CI):** don't assume a push landed cleanly or guess at failures from
job/step names alone.

- `gh run list --branch <branch> --limit 3` — find the run a push triggered
- `gh run view <run-id> --json status,conclusion -q '.status'` (poll) or
  `gh run watch <run-id>` — wait for it to finish (`gh run watch` can
  itself flake with a transient "Bad credentials" on the annotations
  call; a `gh run view <run-id>` after that still shows the real job
  status, so don't treat a `run watch` crash as the run having failed)
- `gh run view <run-id> --log-failed` — **the real fix for CI debugging.**
  The unauthenticated GitHub REST API only exposes job/step names and
  conclusions, never log content (log downloads 403 "Must have admin
  rights" even on public repos without an authenticated token) — that
  API alone means guessing at root causes from symptoms. Authenticated
  `gh` gives the exact failing line immediately.

Also usable the same way for `gh pr view`, `gh issue view`, `gh pr create`,
etc. wherever a GitHub-authenticated operation is needed — this isn't
CI-specific.

### Git — never destroy work you have not read

These repos get worked on by several agents at once (Claude sessions,
Antigravity, Codex, Copilot), and unmerged work has genuinely been lost here
before: a complete HNeRV baseline once sat in a forgotten worktree.

- **Read a branch before deleting it.** `git log main..<branch>` and
  `git diff main...<branch> --stat`. If it is not empty,
  `git tag archive/<branch> <branch>` and push the tag *before* deleting.
  Tags are free and make a triage mistake recoverable.
- **A worktree with uncommitted changes never gets `--force`d away.**
  Commit the changes onto that worktree's own branch, tag it, then remove
  the worktree. `git worktree remove` refusing is a warning, not an obstacle
  to route around.
- **"Superseded" needs proof, not a guess.** Compare with `git patch-id`, or
  diff the files against `main` — a branch whose commit message matches one
  on main may still hold changes main never got.
- **A branch alone does not isolate a session** — two agents in one checkout
  share one HEAD. Isolation needs a worktree *and* a branch.

### Research code — tests are a failsafe, not a formality

Cover envisioned behavior and plausible misuse of code we own. Skip tests for
unreachable branches, third-party library behavior, and errors a caller
cannot produce; this is research code and boilerplate slows the iteration
that actually matters. **A test that exists only to raise a coverage number
is a defect** — it makes the gate lie about what is verified. If deleting
padding drops the gate, lower the gate to the honest number and ratchet it
back up as real tests land.

The tests that pay for themselves here are the ones that check *the paper's
claim*, not just that the code runs: an experiment whose result violates the
thing the paper asserts should fail loudly and be marked uncitable, rather
than being caught later by a careful human reading a table.

### Long jobs must checkpoint at least hourly

SSH to this host drops a couple of times a day. Any job expected to run over
an hour checkpoints at least every 60 minutes of wall clock — independent of
its epoch/step cadence — and its resume path is verified *before* it is
relied on. Long scripts also append a progress line to their log at least
every 10 minutes, so a silent hang is visible in minutes rather than hours.
Launch detached; never attached to a shell an SSH drop takes with it.

### Plan mode: split complex plans into parallel-agent waves

When a plan has multiple pieces of work that don't share state, don't execute
it as one linear sequence. Split it into workstreams and hand each to a
subagent working in its own git worktree (a shared checkout with only a
different branch is not isolation — two sessions in one worktree share a
single HEAD). Group workstreams into **waves** ordered by dependency: a wave
starts only once every workstream it depends on has reported results back,
and every workstream within a wave launches together, not one at a time.

**Why:** validated on a multi-part refactor — this surfaced cross-workstream
issues at each wave boundary instead of at the end, and kept parallel agents
from clobbering each other's changes.

**How to apply:** worth it for genuinely multi-part, multi-file tasks where
pieces are largely independent. Skip it for small or sequential tasks — one
file, one clear order of steps — where waves are pure coordination overhead.

### Waiting for long-running commands — never hand-roll a waiter

⛔ **Never write `until ! pgrep -f <pattern>; do sleep N; done` (or any
self-written poll loop) to wait for a job.** The harness runs the loop via
`bash -c "<the whole command string>"`, and that string *contains* the
pattern — so `pgrep -f` matches the watcher's own process and the condition
can never become true. The job finishes, the watcher spins until timeout, and
the completion goes unnoticed. This has already burned >1h of wall clock.
Escaping tricks (`[p]attern`, `pgrep -P`) technically work but are still the
wrong answer: the harness already reports completion, so there is nothing to
poll for.

Pick by duration, not by habit:

- **Finishes in < 10 min** → foreground `Bash` with an explicit `timeout`
  (ms, max 600000). Output arrives in one piece and the harness kills it at
  the deadline, so it cannot hang forever.
- **Longer than that** (GPU restoration, full evaluation passes, big
  backfills) → `Bash` with `run_in_background: true`. It detaches, survives
  across turns, and **re-invokes Claude on exit** with the path to its
  output file. Read that file; do not poll for it.
- **Need progress while it runs** → `Monitor`, with a filter that matches
  failure signatures too (`Traceback|Error|FAILED|Killed|OOM`), not just the
  success marker — a success-only filter stays silent through a crash, and
  silence is indistinguishable from "still running."

`conda run -n <env> …` is not a solution to this. It is still a foreground
command subject to the same 10-minute cap, and without
`--no-capture-output` it buffers all output until exit — so on a long job it
shows nothing and then gets killed. Use it for env activation if convenient,
never as a completion-waiting strategy.

Note: `Monitor`'s progress-matching depends on the logging cadence described
in the shared "Long jobs must checkpoint" rule above — a job that goes quiet
for more than ~10 minutes gives Monitor nothing fresh to match, which looks
identical to a hang.

Same trap, different tool: **`ScheduleWakeup` is not a wait-for-completion
mechanism.** It exists solely to self-pace `/loop` dynamic-mode iterations.
A background agent or background `Bash` job already triggers a notification
the moment it finishes — there is nothing to poll for. Don't call
`ScheduleWakeup` "just to wait" for one; it also fails outright when used
this way (it requires a `prompt` unless `stop: true`), so the mistake
surfaces immediately rather than silently wasting a turn — still worth not
repeating.
