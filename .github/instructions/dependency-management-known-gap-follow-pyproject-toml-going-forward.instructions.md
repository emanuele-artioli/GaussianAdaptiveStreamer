---
applyTo: "pyproject.toml,environment.yaml,setup.py"
---

<!-- GENERATED — DO NOT EDIT. Source: AGENTS.md via tools/sync_agent_rules.py
     The 'Dependency management — known gap, follow pyproject.toml going forward' section. Copilot's cloud agent and code review
     read the whole of AGENTS.md; this copy is for Copilot Chat, which
     reads only .github/. -->

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
