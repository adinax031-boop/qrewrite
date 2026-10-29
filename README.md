# QRewrite

A compact Python quantum circuit optimizer with an auditable rewrite engine and an independent statevector execution path. QRewrite is designed for studying how a compiler turns algebraic identities into smaller circuits, and for checking those transformations before trusting them.

**Status:** research prototype, version 0.1. This is not a complete quantum compiler or a Kaggle submission. It has not been benchmarked on the Google Quantum AI competition workloads and makes no claim of improving their score.

## Quick start

Python 3.10+; no runtime dependencies.

```sh
python -m unittest -v
python -m qrewrite example.json -o optimized.json --verify
python benchmark.py
# Optional install:
python -m pip install .
qrewrite example.json -o optimized.json --verify
```

The command emits metrics and rewrite counts as JSON on stdout. Invalid input and verification failures exit with status 2 and do not overwrite the destination. Output is written to a temporary file in the destination directory and atomically replaced.

## Circuit format

```json
{"qubits": 2, "gates": [
  {"name": "h", "qubits": [0]},
  {"name": "cx", "qubits": [0, 1]},
  {"name": "p", "qubits": [1], "angle": "1/4"}
]}
```

Qubit 0 is the least significant bit. `cx` lists control then target. Supported gates: `h`, `x`, `z`, `cx`, and `p`, where `p(a) = diag(1, exp(i*pi*a))`. Angles are exact rational strings, in units of π. Gates are applied in listed order. Measurement, noise, classical control, hardware connectivity, and arbitrary gate sets are not supported.

## Design

The optimizer scans backward over gates on disjoint qubits, stopping at the first dependency. It cancels identical self-inverse gates, combines diagonal phase gates using rational arithmetic modulo 2π, and applies H-X-H = Z and H-Z-H = X. It never moves a gate past a gate sharing a qubit. The work queue reprocesses generated gates; all reducing rewrites strictly decrease gate count. Worst-case scanning is quadratic in gate count, so large circuits need profiling and a dependency-indexed implementation.

Verification simulates every computational-basis input and compares all output amplitudes with a **single shared global phase**. This checks the full unitary numerically, including superpositions by linearity. It uses floating-point arithmetic and a tolerance, so it is not a formal proof. The default verifier is limited to six qubits; memory and runtime grow exponentially. Circuit rewriting itself permits up to 64 qubits, but larger circuits are not verified by the CLI.

```python
from fractions import Fraction
from qrewrite import Circuit, Gate, optimize, equivalent
c = Circuit(1, (Gate('p', (0,), Fraction(1, 4)), Gate('p', (0,), Fraction(3, 4))))
result, changes = optimize(c)
assert equivalent(c, result)
```

## Testing and measurements

Tests cover fixed identities, dependency blockers, exact phase fusion, global-phase consistency, a Bell-state example, bad inputs, CLI output safety, and 480 seeded random circuits of 1–4 qubits. Each random circuit is checked using full-unitary simulation, and optimizer idempotence is tested. This is evidence for the covered cases, not proof of correctness for all circuits.

`benchmark.py` creates a deliberately rewrite-friendly synthetic set and verifies all results. Its reduction is **not representative** of realistic or competition circuits. The experiment reports optimizer wall time separately from verification time.

## Limitations and next steps

This is a small gate-count optimizer, not a hardware-aware synthesis system: it lacks OpenQASM/Cirq integration, ancilla handling, routing, realistic cost models, formal verification, and an official competition harness. Useful next steps are independently checking against a second simulator, benchmarking real circuits, and connecting to the official competition verifier before drawing performance conclusions.

MIT license.
