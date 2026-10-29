import cmath
from fractions import Fraction
import math
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
from compiler import parse_qasm, emit_qasm, route_line, compile_circuit
from qrewrite import Circuit, Gate, equivalent

# Independently constructs each gate matrix directly from its action on basis
# vectors, and multiplies dense matrices. This does not use the statevector code.
def matrix(c):
    size=1<<c.qubits
    total=np.eye(size,dtype=complex)
    for gate in c.gates:
        m=np.zeros((size,size),complex); q=gate.qubits[0]
        for col in range(size):
            bit=(col>>q)&1
            if gate.name=='h':
                m[col & ~(1<<q),col]=1/math.sqrt(2)
                m[col | (1<<q),col]=(-1 if bit else 1)/math.sqrt(2)
            elif gate.name=='x': m[col ^ (1<<q),col]=1
            elif gate.name=='cx': m[col ^ ((1<<gate.qubits[1]) if bit else 0),col]=1
            elif gate.name=='z': m[col,col]=-1 if bit else 1
            elif gate.name=='p': m[col,col]=cmath.exp(1j*math.pi*float(gate.angle)) if bit else 1
        total=m @ total
    return total

class CompilerTests(unittest.TestCase):
    def test_qasm_roundtrip(self):
        source='OPENQASM 2.0; include "qelib1.inc"; qreg q[3]; h q[0]; cx q[0],q[2]; s q[1]; tdg q[2]; u1(-3*pi/8) q[0];'
        c=parse_qasm(source)
        self.assertEqual(parse_qasm(emit_qasm(c)),c)
    def test_reject_unsupported(self):
        for text in ['OPENQASM 2.0; qreg q[2]; measure q[0] -> c[0];', 'OPENQASM 2.0; qreg q[1]; u1(pi/0) q[0];', 'OPENQASM 2.0; qreg q[1]; h q[0]']:
            with self.subTest(text=text), self.assertRaises(ValueError): parse_qasm(text)
    def test_line_routes_all_pairs(self):
        for n in range(2,6):
            for a in range(n):
                for b in range(n):
                    if a==b: continue
                    c=Circuit(n,(Gate('cx',(a,b)),))
                    routed=route_line(c)
                    self.assertTrue(all(abs(g.qubits[0]-g.qubits[1])==1 for g in routed.gates))
                    np.testing.assert_allclose(matrix(c),matrix(routed),atol=1e-12)
    def test_independent_random_oracle(self):
        rng=random.Random(9472)
        for _ in range(120):
            n=rng.randint(2,4); ops=[]
            for _ in range(20):
                kind=rng.choice(['h','x','z','p','cx'])
                qs=tuple(rng.sample(range(n),2)) if kind=='cx' else (rng.randrange(n),)
                ops.append(Gate(kind,qs,Fraction(rng.randint(-4,4),4) if kind=='p' else None))
            c=Circuit(n,tuple(ops))
            for topology in ('all','line'):
                out, report=compile_circuit(c,topology=topology,verify=True)
                np.testing.assert_allclose(matrix(c),matrix(out),atol=1e-9)
                self.assertEqual(report['verification'],'passed')
                if topology=='line': self.assertTrue(all(abs(g.qubits[0]-g.qubits[1])==1 for g in out.gates if g.name=='cx'))
    def test_cli_does_not_replace_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'in.qasm'; dest=Path(d)/'out.qasm'
            source.write_text('OPENQASM 2.0; qreg q[8]; cx q[0],q[7];'); dest.write_text('untouched')
            r=subprocess.run([sys.executable,'-m','compiler',str(source),'-o',str(dest),'--verify'],capture_output=True,text=True)
            self.assertEqual(r.returncode,2); self.assertEqual(dest.read_text(),'untouched')

if __name__=='__main__': unittest.main()
