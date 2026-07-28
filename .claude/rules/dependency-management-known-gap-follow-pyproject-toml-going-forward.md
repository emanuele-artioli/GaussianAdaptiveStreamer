---
paths:
  - "requirements.txt"
  - "readme.md"
---

<!-- GENERATED — DO NOT EDIT. Source: AGENTS.md via tools/sync_agent_rules.py
     The 'Dependency management — known gap, follow pyproject.toml going forward' section. Scoped so it costs no context until
     Claude reads a file it actually governs. -->

## Dependency management — known gap, follow pyproject.toml going forward

Host policy is `pyproject.toml` as the single source of truth for Python
deps, never a `requirements.txt` (see host-wide rules below). TIGAS
currently violates this: there is no project `pyproject.toml`, and
`requirements.txt` (CUDA 11.8 PyTorch wheels) is what `readme.md` installs
from into the `render` conda env. Don't compound the gap — when adding a
new dependency, introduce or update a `[project.dependencies]` list in a
new `pyproject.toml` and mirror the same pin into `requirements.txt` until
install docs are switched over, rather than adding a bare `pip install`
step anywhere. Never install into the shared `render` env from an unrelated
project.
