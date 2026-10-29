"""QASM 2 frontend and topology-aware compilation for QRewrite.

Deliberately accepts a small explicit QASM 2 subset; unsupported constructs fail
rather than being silently ignored. Routing restores logical-to-physical mapping
at the end of every remote CNOT.
"""
from __future__ import annotations
import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from qrewrite import Circuit, Gate, equivalent, metrics, optimize

_QUBIT = r'q\[(\d+)\]'
_SINGLE = re.compile(rf'(h|x|z|s|sdg|t|tdg)\s+{_QUBIT}\s*', re.I)
_TWO = re.compile(rf'cx\s+{_QUBIT}\s*,\s*{_QUBIT}\s*', re.I)
_PHASE = re.compile(rf'(?:p|u1)\s*\(\s*([^()]*)\s*\)\s*{_QUBIT}\s*', re.I)
_QREG = re.compile(r'qreg\s+q\[(\d+)\]\s*', re.I)
# Accepted angle syntax: [sign] [integer *] pi [/ positive-integer], or 0.
_ANGLE = re.compile(r'([+-]?)(?:(\d+)\s*\*\s*)?pi(?:\s*/\s*(\d+))?', re.I)

def _angle(text: str) -> Fraction:
    if text.strip() == '0': return Fraction(0)
    m = _ANGLE.fullmatch(text.strip())
    if not m: raise ValueError(f'unsupported angle expression: {text!r}')
    denominator = int(m.group(3) or 1)
    if denominator == 0: raise ValueError('zero angle denominator')
    value = Fraction(int(m.group(2) or 1), denominator)
    return -value if m.group(1) == '-' else value

def parse_qasm(text: str) -> Circuit:
    if len(text) > 10_000_000: raise ValueError('QASM input exceeds 10 MB')
    text = re.sub(r'//[^\n]*', '', text)
    if text.strip() and not text.rstrip().endswith(';'): raise ValueError('missing semicolon')
    statements = [s.strip() for s in text.split(';') if s.strip()]
    if not statements or statements.pop(0).lower() != 'openqasm 2.0':
        raise ValueError('expected OPENQASM 2.0 header')
    if statements and statements[0].lower() == 'include "qelib1.inc"': statements.pop(0)
    if not statements or not (m := _QREG.fullmatch(statements.pop(0))):
        raise ValueError('expected a single qreg named q')
    n = int(m.group(1)); gates = []
    phases = {'s': Fraction(1,2), 'sdg': Fraction(-1,2), 't': Fraction(1,4), 'tdg': Fraction(-1,4)}
    for statement in statements:
        if m := _SINGLE.fullmatch(statement):
            name, q = m.group(1).lower(), int(m.group(2))
            gates.append(Gate('p',(q,), phases[name]) if name in phases else Gate(name,(q,)))
        elif m := _TWO.fullmatch(statement): gates.append(Gate('cx',(int(m.group(1)),int(m.group(2)))))
        elif m := _PHASE.fullmatch(statement): gates.append(Gate('p',(int(m.group(2)),),_angle(m.group(1))))
        else: raise ValueError(f'unsupported QASM statement: {statement[:100]!r}')
    return Circuit(n, tuple(gates))

def emit_qasm(circuit: Circuit) -> str:
    lines = ['OPENQASM 2.0;', 'include "qelib1.inc";', f'qreg q[{circuit.qubits}];']
    for g in circuit.gates:
        if g.name == 'cx': lines.append(f'cx q[{g.qubits[0]}],q[{g.qubits[1]}];')
        elif g.name == 'p':
            a = g.angle
            sign = '-' if a < 0 else ''
            a = abs(a)
            term = ('pi' if a.numerator == 1 else f'{a.numerator}*pi')
            if a.denominator != 1: term += f'/{a.denominator}'
            if a == 0: term = '0'
            lines.append(f'u1({sign}{term}) q[{g.qubits[0]}];')
        else: lines.append(f'{g.name} q[{g.qubits[0]}];')
    return '\n'.join(lines) + '\n'

def route_line(circuit: Circuit) -> Circuit:
    """Compile CNOTs to a bidirectional linear nearest-neighbor topology.

    Swap one endpoint along the unique path, perform the CNOT, and reverse the
    swaps. This preserves the physical identity of all logical wires, including
    entangled states; it is intentionally conservative rather than optimal.
    """
    out: list[Gate] = []
    def cx(a: int, b: int) -> None: out.append(Gate('cx',(a,b)))
    def swap(a: int, b: int) -> None:
        cx(a,b); cx(b,a); cx(a,b)
    for g in circuit.gates:
        if g.name != 'cx' or abs(g.qubits[0] - g.qubits[1]) == 1:
            out.append(g); continue
        control, target = g.qubits
        step = 1 if target > control else -1
        edges = [(q, q + step) for q in range(control, target - step, step)]
        for a,b in edges: swap(a,b)
        cx(target-step, target)
        for a,b in reversed(edges): swap(a,b)
    return Circuit(circuit.qubits, tuple(out))

def compile_circuit(circuit: Circuit, *, topology: str = 'all', verify: bool = False) -> tuple[Circuit, dict]:
    if topology not in {'all', 'line'}: raise ValueError('unsupported topology')
    first, stats = optimize(circuit)
    routed = route_line(first) if topology == 'line' else first
    result, post_stats = optimize(routed)
    checked = equivalent(circuit, result) if verify else None
    if checked is False: raise ValueError('verification failed')
    if topology == 'line' and any(g.name == 'cx' and abs(g.qubits[0]-g.qubits[1]) != 1 for g in result.gates):
        raise AssertionError('routing invariant violated')
    return result, {'input': metrics(circuit), 'optimized': metrics(first), 'output': metrics(result),
                    'pre_route_rewrites': stats, 'post_route_rewrites': post_stats,
                    'topology': topology, 'verification': 'passed' if checked else 'not-run'}

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Compile an OpenQASM 2 subset to QASM 2')
    parser.add_argument('input',type=Path); parser.add_argument('-o','--output',type=Path,required=True)
    parser.add_argument('--topology',choices=['all','line'],default='all')
    parser.add_argument('--verify',action='store_true')
    args = parser.parse_args(argv)
    try:
        circuit = parse_qasm(args.input.read_text(encoding='utf-8'))
        result, report = compile_circuit(circuit,topology=args.topology,verify=args.verify)
        content = emit_qasm(result)
        fd, path = tempfile.mkstemp(prefix='.qrewrite-',dir=args.output.parent,text=True)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(content)
            os.replace(path,args.output)
        finally:
            if os.path.exists(path): os.unlink(path)
        print(json.dumps(report,sort_keys=True)); return 0
    except (ValueError, OSError) as e:
        print(f'qrewrite-compile: {e}',file=sys.stderr); return 2

if __name__ == '__main__': raise SystemExit(main())
