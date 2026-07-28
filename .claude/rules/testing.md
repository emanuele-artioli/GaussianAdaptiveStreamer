---
paths:
  - "tests/**"
  - "pytest.ini"
---

<!-- GENERATED — DO NOT EDIT. Source: AGENTS.md via tools/sync_agent_rules.py
     The 'Testing' section. Scoped so it costs no context until
     Claude reads a file it actually governs. -->

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

There is no `.pre-commit-config.yaml` in this repo. Ablation CI now runs
`tools/sync_agent_rules.py --check` so generated agent-rule files stay in
sync with `AGENTS.md`. If pre-commit gets adopted later, add a matching
`sync-agent-rules` hook entry.

For new tests, use the generic `test-design` skill (no TIGAS-specific
tiers/markers exist to layer on top of it — plain `pytest -q` is the whole
story right now).
