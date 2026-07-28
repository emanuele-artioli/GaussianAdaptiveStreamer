---
applyTo: "tests/**,pytest.ini,scripts/compute_metrics.py"
---

<!-- GENERATED — DO NOT EDIT. Source: AGENTS.md via tools/sync_agent_rules.py
     The 'Testing' section. Copilot's cloud agent and code review
     read the whole of AGENTS.md; this copy is for Copilot Chat, which
     reads only .github/. -->

## Testing

There is **no** `tests/` tree or pytest gate on `main` yet. Offline quality
checks go through `scripts/compute_metrics.py` against exported frame
folders (see Evaluation / metrics below). When adding automated tests, use
the generic `test-design` skill and keep them honest — no coverage padding.
