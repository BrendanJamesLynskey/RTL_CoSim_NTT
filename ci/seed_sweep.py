"""Nightly sweep: butterfly campaigns over many seeds and both stimulus kinds, to sweep.csv.

Each row is one regression run; the CSV shows how often each stimulus closes the
observable-correction crosses (and so would catch the seeded bug) across seeds.

    python ci/seed_sweep.py [seeds] [transactions]
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

from ntt_cosim import rtl
from ntt_cosim.primes import PRIMES

seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 8
n = int(sys.argv[2]) if len(sys.argv) > 2 else 4000
tmp = Path(tempfile.mkdtemp())
with open("sweep.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["prime", "stimulus", "seed", "transactions", "tests", "failures", "coverage_percent",
                "corr_2", "observable_corr_2"])
    for prime in ("near", "mid"):
        for stim in ("uniform", "constrained"):
            for seed in range(1, seeds + 1):
                js = tmp / "bf.json"
                env = {"BF_PRIME": hex(PRIMES[prime]), "BF_STIM": stim, "BF_N": str(n), "BF_SEED": str(seed),
                       "BF_OUT": str(js)}
                tests, fails, _ = rtl.run("ntt_butterfly", env=env, testcase="butterfly_random",
                                          results_xml=tmp / "bf.xml")
                c = json.loads(js.read_text())["coverage"]
                b = c["bins"]
                w.writerow([prime, stim, seed, n, tests, fails, f"{c['percent']:.1f}", b["corr_2"],
                            b["corr_2_x_sum_wraps"] + b["corr_2_x_diff_borrows"]])
                if fails:
                    sys.exit(f"{prime}/{stim}/seed {seed}: {fails} failures")
print(Path("sweep.csv").read_text())
