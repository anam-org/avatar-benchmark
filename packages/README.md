# Shared packages

Study-agnostic code, installable so imports do not depend on `sys.path`:

    pip install -e packages/stimulus -e packages/mabyduck

- `stimulus` — capability tokens and dataset assembly. Loads no config.
- `mabyduck` — Mabyduck API client: datasets, experiments, jobs.

## The token contract

`stimulus.core_payload` binds the six fields that decide what a rater
sees, in a fixed order, and `stimulus.bind` appends study-specific
segments after them. A study can add bound parameters and nothing else:
dropping or reordering a core field takes an edit to `core_payload`
itself.

`src/lib/token.ts` is the worker mirror. `tools/check_token_parity.sh`
asserts the two produce identical payloads and fails if they drift.
