# Infra Foundation

This package is the first runnable slice of MRLC Infra Core.

It currently provides:

- stable prefixed identities;
- project and task registries;
- SQLite append-only events with project-local sequence numbers;
- idempotent event append and replay;
- session persistence, runtime binding, snapshots, and project isolation.

The package uses only the Python standard library. Domain plugins, CCR adapters,
memory projections, and external worker integrations are intentionally deferred
to later PRD epics.

Run the foundation tests from the repository root with:

```bash
python3 -m pytest -q
```

