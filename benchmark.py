"""Deterministic synthetic benchmark. Not a competition benchmark."""
import random
import time
from fractions import Fraction
from qrewrite import Circuit, Gate, equivalent, metrics, optimize

def main():
    rng = random.Random(20261030)
    circuits = []
    for _ in range(100):
        g = []
        for _ in range(15):
            q = rng.randrange(4)
            kind = rng.choice(['h', 'x', 'z', 'p'])
            op = Gate(kind,(q,), Fraction(1,4) if kind == 'p' else None)
            g.extend([op,Gate('x',((q+1)%4,)),op])
        circuits.append(Circuit(4,tuple(g)))
    start=time.perf_counter(); outputs=[optimize(c)[0] for c in circuits]; elapsed=time.perf_counter()-start
    assert all(equivalent(a,b) for a,b in zip(circuits,outputs))
    before=sum(metrics(c)['gates'] for c in circuits); after=sum(metrics(c)['gates'] for c in outputs)
    print(f'circuits={len(circuits)} before={before} after={after} reduction={(before-after)/before:.1%} optimize_seconds={elapsed:.6f} verified=100/100')
if __name__=='__main__': main()
