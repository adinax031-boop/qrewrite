"""QRewrite: small, auditable quantum circuit optimizer, Python 3.10+."""
from __future__ import annotations
import argparse
import cmath
from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
import sys
from typing import Any

@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    qubits: tuple[int, ...]
    angle: Fraction | None = None  # phase in units of pi

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or self.name not in {'h', 'x', 'z', 'p', 'cx'}:
            raise ValueError(f'unsupported gate: {self.name}')
        if not isinstance(self.qubits, tuple) or any(type(q) is not int or q < 0 for q in self.qubits):
            raise ValueError('qubits must be nonnegative integer tuple')
        arity = 2 if self.name == 'cx' else 1
        if len(self.qubits) != arity or len(set(self.qubits)) != arity:
            raise ValueError(f'{self.name} requires {arity} distinct qubit(s)')
        if (self.name == 'p') != isinstance(self.angle, Fraction):
            raise ValueError('p requires a Fraction angle; other gates take no angle')

@dataclass(frozen=True, slots=True)
class Circuit:
    qubits: int
    gates: tuple[Gate, ...]

    def __post_init__(self) -> None:
        if type(self.qubits) is not int or not 1 <= self.qubits <= 64:
            raise ValueError('qubits must be an integer from 1 to 64')
        if not isinstance(self.gates, tuple) or any(not isinstance(g, Gate) for g in self.gates):
            raise ValueError('gates must be a tuple of Gate objects')
        if any(q >= self.qubits for g in self.gates for q in g.qubits):
            raise ValueError('gate qubit outside circuit')

    @classmethod
    def from_dict(cls, obj: Any) -> Circuit:
        if not isinstance(obj, dict) or set(obj) != {'qubits', 'gates'} or not isinstance(obj['gates'], list):
            raise ValueError('expected object with exactly qubits and gates')
        gates = []
        for item in obj['gates']:
            if not isinstance(item, dict) or 'name' not in item or 'qubits' not in item:
                raise ValueError('gate must contain name and qubits')
            if set(item) - {'name', 'qubits', 'angle'} or not isinstance(item['qubits'], list):
                raise ValueError('invalid gate fields')
            angle = item.get('angle')
            if angle is not None:
                if not isinstance(angle, str) or len(angle) > 100:
                    raise ValueError('angle must be a rational string of at most 100 characters')
                try: angle = Fraction(angle)
                except (ValueError, ZeroDivisionError) as e: raise ValueError('invalid rational angle') from e
            gates.append(Gate(item['name'], tuple(item['qubits']), angle))
        return cls(obj['qubits'], tuple(gates))

    def to_dict(self) -> dict[str, Any]:
        return {'qubits': self.qubits, 'gates': [
            {'name': g.name, 'qubits': list(g.qubits), **({'angle': str(g.angle)} if g.angle is not None else {})}
            for g in self.gates]}

def metrics(circuit: Circuit) -> dict[str, int]:
    depths = [0] * circuit.qubits
    for g in circuit.gates:
        d = 1 + max(depths[q] for q in g.qubits)
        for q in g.qubits: depths[q] = d
    return {'gates': len(circuit.gates), 'two_qubit': sum(len(g.qubits) == 2 for g in circuit.gates),
            'phase': sum(g.name == 'p' for g in circuit.gates), 'depth': max(depths)}

def _phase(g: Gate) -> Fraction | None:
    if g.name == 'z': return Fraction(1)
    if g.name == 'p': return g.angle
    return None

def _phase_gate(q: int, value: Fraction) -> Gate | None:
    value %= 2
    if value == 0: return None
    if value == 1: return Gate('z', (q,))
    return Gate('p', (q,), value)

def optimize(circuit: Circuit) -> tuple[Circuit, dict[str, int]]:
    """Reduce gate count by local rewrites across operations on disjoint wires.

    Each rewrite reduces gate count or normalizes an angle. We never reorder
    overlapping gates; scanning stops at the first overlapping operation.
    """
    out: list[Gate] = []
    stats = {'cancelled_pairs': 0, 'phase_fusions': 0, 'zero_phases': 0, 'basis_rewrites': 0}
    from collections import deque
    pending = deque(circuit.gates)
    while pending:
        g = pending.popleft()
        if g.name == 'p':
            norm = _phase_gate(g.qubits[0], g.angle)  # type: ignore[arg-type]
            if norm is None:
                stats['zero_phases'] += 1
                continue
            g = norm
        support = set(g.qubits)
        i = len(out) - 1
        while i >= 0 and support.isdisjoint(out[i].qubits): i -= 1
        if i >= 0:
            prev = out[i]
            if prev == g and g.name in {'h', 'x', 'z', 'cx'}:
                out.pop(i); stats['cancelled_pairs'] += 1; continue
            a, b = _phase(prev), _phase(g)
            if a is not None and b is not None and prev.qubits == g.qubits:
                out.pop(i)
                fused = _phase_gate(g.qubits[0], a + b)
                if fused is not None: out.append(fused)
                stats['phase_fusions'] += 1
                continue
            # H X H = Z and H Z H = X, allowing disjoint operations between.
            if g.name == 'h' and prev.name in {'x', 'z'} and prev.qubits == g.qubits:
                j = i - 1
                while j >= 0 and support.isdisjoint(out[j].qubits): j -= 1
                if j >= 0 and out[j] == g:
                    out.pop(i); out.pop(j)
                    replacement = Gate('z' if prev.name == 'x' else 'x', g.qubits)
                    pending.appendleft(replacement)
                    stats['basis_rewrites'] += 1
                    continue
        out.append(g)
    return Circuit(circuit.qubits, tuple(out)), stats

def _simulate(circuit: Circuit, basis: int) -> list[complex]:
    n = 1 << circuit.qubits
    state = [0j] * n
    state[basis] = 1+0j
    root = 1 / math.sqrt(2)
    for g in circuit.gates:
        q = g.qubits[0]; bit = 1 << q
        if g.name == 'cx':
            target = 1 << g.qubits[1]
            for i in range(n):
                if i & bit and not i & target: state[i], state[i | target] = state[i | target], state[i]
        elif g.name == 'p' or g.name == 'z':
            phase = -1 if g.name == 'z' else cmath.exp(1j * math.pi * float(g.angle % 2))
            for i in range(n):
                if i & bit: state[i] *= phase
        else:
            for i in range(n):
                if not i & bit:
                    a, b = state[i], state[i | bit]
                    if g.name == 'h': state[i], state[i | bit] = (a+b)*root, (a-b)*root
                    else: state[i], state[i | bit] = b, a
    return state

def equivalent(a: Circuit, b: Circuit, *, max_qubits: int = 6, tolerance: float = 1e-9) -> bool:
    """Numerically compare every unitary column using ONE shared global phase.

    This is exponential and floating-point verification, not a formal proof.
    """
    if a.qubits != b.qubits: return False
    if type(max_qubits) is not int or max_qubits < 1 or max_qubits > 10:
        raise ValueError('max_qubits must be between 1 and 10')
    if a.qubits > max_qubits: raise ValueError('verification qubit limit exceeded')
    if not math.isfinite(tolerance) or tolerance <= 0: raise ValueError('tolerance must be positive and finite')
    phase = None
    for basis in range(1 << a.qubits):
        x, y = _simulate(a, basis), _simulate(b, basis)
        for u, v in zip(x, y):
            if phase is None and abs(v) > tolerance and abs(u) > tolerance:
                ratio = u / v
                phase = ratio / abs(ratio)
            if phase is not None and abs(u - phase * v) > tolerance: return False
            if phase is None and (abs(u) > tolerance or abs(v) > tolerance): return False
    return True

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Optimize small quantum circuits in JSON format')
    parser.add_argument('input', type=Path)
    parser.add_argument('-o', '--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true', help='numerically verify full unitary (max 6 qubits)')
    args = parser.parse_args(argv)
    try:
        circuit = Circuit.from_dict(json.loads(args.input.read_text(encoding='utf-8')))
        result, stats = optimize(circuit)
        verified = equivalent(circuit, result) if args.verify else None
        if verified is False: raise ValueError('verification failed; output not written')
        # Write only after validation; atomic replacement prevents partial files.
        import os, tempfile
        data = json.dumps(result.to_dict(), indent=2) + '\n'
        fd, temp = tempfile.mkstemp(prefix='.qrewrite-', dir=args.output.parent, text=True)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f: f.write(data)
            os.replace(temp, args.output)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        print(json.dumps({'before': metrics(circuit), 'after': metrics(result), 'rewrites': stats, 'verified': verified}))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, RecursionError) as e:
        print(f'qrewrite: {e}', file=sys.stderr); return 2

if __name__ == '__main__': raise SystemExit(main())
