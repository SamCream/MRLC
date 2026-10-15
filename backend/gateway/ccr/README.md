# CCR Gateway Boundary

`gateway/ccr/` is the MRLC ownership boundary for the model gateway. It is not
the Infra Core state store: Project, Session, Task, Memory, Bank, and Workflow
state remain owned by `infra/`.

## Current source and runtime

- Source repository: `Router/claude-code-router/`.
- The currently observed listener on `127.0.0.1:3456` is running from the
  separate Toolkit checkout at
  `/home/samera2022/Projects/Toolkit/Router/claude-code-router`.
- MRLC's `Router/claude-code-router/` is a separate checkout and must not be
  assumed to be the process serving the local port.

## Migration boundary

CCR remains responsible for provider catalog, routing, invocation, streaming,
fallback, budget, usage, and provider error normalization. It must not become a
writer for Infra Project, Task, Session, Memory, Bank, or Workflow state.

The MRLC `gateway/ccr/` tree currently contains the target boundary skeleton;
the implementation remains in the source repository until an explicit
adapter/cutover is completed and verified against the running process.
