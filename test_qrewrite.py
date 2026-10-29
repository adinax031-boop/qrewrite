import json
import random
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from qrewrite import Circuit, Gate, equivalent, metrics, optimize

class OptimizerTests(unittest.TestCase):
    def test_disjoint_cancellation_and_blocker(self):
        c = Circuit(2, (Gate('h',(0,)), Gate('x',(1,)), Gate('h',(0,))))
        out, stats = optimize(c)
        self.assertEqual(out.gates, (Gate('x',(1,)),))
        self.assertEqual(stats['cancelled_pairs'], 1)
        blocked = Circuit(2, (Gate('h',(0,)), Gate('cx',(0,1)), Gate('h',(0,))))
        self.assertEqual(optimize(blocked)[0], blocked)
    def test_fusion_and_exact_angle(self):
        c = Circuit(1, (Gate('p',(0,),Fraction(1,4)),Gate('p',(0,),Fraction(3,4)), Gate('z',(0,))))
        self.assertEqual(optimize(c)[0].gates, ())
        self.assertTrue(equivalent(c, optimize(c)[0]))
    def test_basis_rewrite(self):
        c = Circuit(2, (Gate('h',(0,)),Gate('x',(1,)),Gate('z',(0,)),Gate('h',(0,))))
        o, s = optimize(c)
        self.assertTrue(equivalent(c,o)); self.assertEqual(s['basis_rewrites'],1)
        self.assertEqual(o.gates, (Gate('x',(1,)),Gate('x',(0,))))
    def test_random_differential(self):
        r = random.Random(90210)
        for n in range(1,5):
            for _ in range(120):
                gates=[]
                for _ in range(r.randrange(0,45)):
                    kind=r.choice(['h','x','z','p']+(['cx'] if n>1 else []))
                    qs=tuple(r.sample(range(n),2)) if kind=='cx' else (r.randrange(n),)
                    gates.append(Gate(kind,qs,Fraction(r.randrange(-8,9),4) if kind=='p' else None))
                c=Circuit(n,tuple(gates)); o,_=optimize(c)
                self.assertLessEqual(len(o.gates),len(c.gates))
                self.assertTrue(equivalent(c,o), (c,o))
                self.assertEqual(optimize(o)[0],o)
    def test_global_phase_is_shared_across_columns(self):
        empty=Circuit(1,())
        self.assertFalse(equivalent(empty,Circuit(1,(Gate('z',(0,)),))))
        self.assertTrue(equivalent(Circuit(1,(Gate('x',(0,)),Gate('z',(0,)))),Circuit(1,(Gate('z',(0,)),Gate('x',(0,))))))
    def test_known_states_and_metrics(self):
        from qrewrite import _simulate
        c=Circuit(2,(Gate('h',(0,)),Gate('cx',(0,1))))
        v=_simulate(c,0)
        self.assertAlmostEqual(abs(v[0])**2,.5);self.assertAlmostEqual(abs(v[3])**2,.5)
        self.assertEqual(metrics(c),{'gates':2,'two_qubit':1,'phase':0,'depth':2})
    def test_validation(self):
        bad=[{'qubits':1,'gates':[{'name':'cx','qubits':[0,0]}]}, {'qubits':1,'gates':[{'name':'p','qubits':[0],'angle':'1/0'}]}, {'qubits':True,'gates':[]}, {'qubits':1,'gates':[{'name':'x','qubits':[True]}]}]
        for x in bad:
            with self.subTest(x=x), self.assertRaises(ValueError): Circuit.from_dict(x)
        with self.assertRaises(ValueError): equivalent(Circuit(7,()),Circuit(7,()))
    def test_cli_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); inp=p/'in.json'; out=p/'out.json'
            inp.write_text(json.dumps({'qubits':1,'gates':[{'name':'h','qubits':[0]},{'name':'h','qubits':[0]}]}))
            result=subprocess.run([sys.executable,'-m','qrewrite',str(inp),'-o',str(out),'--verify'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['verified']); self.assertEqual(json.loads(out.read_text())['gates'],[])
            out.write_text('sentinel'); inp.write_text('{')
            result=subprocess.run([sys.executable,'-m','qrewrite',str(inp),'-o',str(out)],capture_output=True,text=True)
            self.assertEqual(result.returncode,2); self.assertEqual(out.read_text(),'sentinel')

if __name__ == '__main__': unittest.main()
