---
paths:
  - "tests/**"
  - "pytest.ini"
  - "scripts/compute_metrics.py"
---

<!-- GENERATED — DO NOT EDIT. Source: AGENTS.md via tools/sync_agent_rules.py
     The 'Testing' section. Scoped so it costs no context until
     Claude reads a file it actually governs. -->

## Testing

There is **no** `tests/` tree or pytest gate on `main` yet. Offline quality
checks go through `scripts/compute_metrics.py` against exported frame
folders (see Evaluation / metrics below). When adding automated tests, use
the generic `test-design` skill and keep them honest — no coverage padding.
