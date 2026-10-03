"""Every number quoted in the README and in deck SimEng 05.

    python examples/results.py            # runs the RTL campaigns, writes examples/results.md

Needs Verilator (or SIM=icarus) and cocotb. Takes a few minutes: four NTT-core builds
with 1-8 lanes, five butterfly campaigns, a model-only stimulus study, and the FHE
simulator re-run with the RTL-measured NTT efficiency.
"""

from __future__ import annotations

import json
import os
import platform
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fhe_sim import ACCELERATORS, PARAMS, BootOptions, SimConfig, bootstrap_trace, simulate, summarise

from ntt_cosim import cycles, rtl
from ntt_cosim.model import barrett, butterfly
from ntt_cosim.primes import PRIMES
from ntt_cosim.stimulus import GENERATORS

HERE = Path(__file__).parent
ROOT = HERE.parent
OUT: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="ntt_results_"))
W = 50


def h(title):
    OUT.append(f"\n## {title}\n")


def table(head, rows):
    OUT.append("| " + " | ".join(head) + " |")
    OUT.append("|" + "---|" * len(head))
    for r in rows:
        OUT.append("| " + " | ".join(str(x) for x in r) + " |")


def sim_version() -> str:
    sim = rtl.simulator()
    cmd = ["verilator", "--version"] if sim == "verilator" else ["iverilog", "-V"]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()[0].strip()


# 1 ── butterfly campaigns ───────────────────────────────────────────────
def campaign(prime: str, stim: str, n: int, bug: bool, seed: int = 1) -> list:
    out = TMP / f"bf_{prime}_{stim}_{n}_{bug}.json"
    env = {"BF_PRIME": hex(PRIMES[prime]), "BF_STIM": stim, "BF_N": str(n), "BF_SEED": str(seed), "BF_OUT": str(out)}
    tests, fails, _ = rtl.run("ntt_butterfly", defines={"INJECT_BUG": 1} if bug else {}, env=env,
                              testcase="butterfly_random", results_xml=TMP / "bf.xml")
    cov = json.loads(out.read_text())["coverage"]
    b = cov["bins"]
    if bug:
        verdict = "**bug caught**" if fails else "bug escaped"
    else:
        verdict = "pass" if not fails else "FAIL"
    holes = ", ".join(f"`{x}`" for x in cov["holes"]) or "none"
    return [prime, "seeded bug" if bug else "clean", stim, f"{n:,}", verdict, f"{cov['percent']:.0f}%",
            b["corr_2"], b["corr_2_x_sum_wraps"] + b["corr_2_x_diff_borrows"], holes]


h("1. Butterfly campaigns (cocotb on the RTL, scoreboard = bit-accurate Python model)")
OUT.append(f"Simulator: {sim_version()}; cocotb {__import__('cocotb').__version__}; W = {W}; "
           f"primes: near = `{hex(PRIMES['near'])}`, mid = `{hex(PRIMES['mid'])}`.\n")
rows = [campaign("near", "constrained", 20000, False),
        campaign("mid", "uniform", 20000, False),
        campaign("mid", "constrained", 20000, False),
        campaign("mid", "uniform", 4000, True),
        campaign("mid", "constrained", 4000, True)]
table(["prime", "RTL", "stimulus", "transactions", "result", "functional coverage", "corr_2 hits",
       "observable corr_2 hits", "holes"], rows)
OUT.append("\n`corr_2`: products needing Barrett's second correction step. *Observable*: the cross with "
           "`sum_wraps` or `diff_borrows`, without which a wrong second correction is masked by the "
           "butterfly's add/subtract stage. The seeded bug (`INJECT_BUG`) drops the second correction.")

# 2 ── how rare is the corner? (model only, a million draws) ─────────────
h("2. How often stimulus reaches the corner (bit-accurate model, 1,000,000 draws each)")
rows = []
for prime in ("near", "mid"):
    q = PRIMES[prime]
    for stim in ("uniform", "constrained"):
        rng, gen = random.Random(11), GENERATORS[stim]
        c2 = obs = 0
        for _ in range(1_000_000):
            a, b, w = gen(rng, q)
            x, y, corr = butterfly(a, b, w, q, W)
            if corr == 2:
                c2 += 1
                t = (x - a) % q
                obs += (a + t >= q) or (a < t)
        rows.append([prime, stim, c2, obs])
table(["prime", "stimulus", "corr_2 per million", "observable corr_2 per million"], rows)

# 3 ── NTT cores: golden-model checks and cycle counts ──────────────────
h("3. NTT cores: bit-exact against FHE_Accelerator_Sim's ntt_reference, and cycle counts")
OUT.append("Each build runs 4 tests (all sizes from 2P to 1024 with random vectors, directed vectors, "
           "an NTT/INTT round trip, random backpressure). Cycle counts are measured by the testbench "
           "(load, unload) and by the core's own counter (compute); the model is "
           "`compute = log2(n) (n/2P + 6)`, `total = 2n/P + compute`.\n")
rows = []
for p in (1, 2, 4, 8):
    out = TMP / f"ntt_{p}.json"
    tests, fails, _ = rtl.run("ntt_core", parameters={"P": p, "LOGN": 10},
                              env={"NTT_PRIME": hex(PRIMES["mid"]), "NTT_OUT": str(out), "NTT_REPS": "2"},
                              results_xml=TMP / "ntt.xml")
    assert fails == 0, f"P={p}: {fails} failing tests"
    for m in json.loads(out.read_text())["sizes"]:
        n = m["n"]
        if n not in (16, 128, 1024):
            continue
        cm, tm = cycles.compute_cycles(n, p), cycles.total_cycles(n, p)
        assert (m["compute_counter"], m["total"]) == (cm, tm), (p, m)
        rows.append([p, n, f"{tests}/{tests} pass", m["load"], m["compute_counter"], cm, m["unload"], m["total"], tm,
                     f"{cycles.efficiency(n, p):.3f}"])
table(["lanes P", "n", "tests", "load", "compute (RTL)", "compute (model)", "unload", "total (RTL)",
       "total (model)", "butterfly efficiency"], rows)

# 4 ── switching activity ─────────────────────────────────────────────────
h("4. Switching activity: register toggles per butterfly (a power proxy)")
act = TMP / "act.json"
rtl.run("ntt_butterfly", env={"BF_PRIME": hex(PRIMES["mid"]), "BF_N_ACT": "2000", "BF_OUT_ACT": str(act)},
        testcase="butterfly_activity", results_xml=TMP / "act.xml")
a = json.loads(act.read_text())
base = a["toggles_per_butterfly"]["uniform random a, b, w"]
table(["operands", "toggles per butterfly", "relative to uniform random"],
      [[k, f"{v:.1f}", f"{v / base:.2f}"] for k, v in a["toggles_per_butterfly"].items()])
OUT.append(f"\nRegisters sampled every cycle: {', '.join('`' + r + '`' for r in a['registers'])} "
           f"({a['n']} butterflies per row). Dynamic power scales with toggles x capacitance x V^2 x f; a "
           "gate-level power tool weights each net by its capacitance, this proxy weights every register bit equally.")

# 5 ── calibrating the FHE simulator ───────────────────────────────────
h("5. Feeding the RTL back into the FHE simulator")
ARK, ARK_HW, SMALL_HW = PARAMS["ark"], ACCELERATORS["ark"], ACCELERATORS["small"]
N = ARK.N
OUT.append(f"FHE_Accelerator_Sim costs an NTT as (N/2) log2 N butterflies at `ntt_bfly_per_cycle` per cycle: "
           f"one butterfly per lane per cycle, efficiency 1. The RTL's cycle model gives the efficiency a core "
           f"of P lanes actually sustains at N = 2^{ARK.log_n}. The organisation of the aggregate lanes into "
           f"cores is an **assumption** (the simulator does not specify it), so several are shown.\n")
orgs = [("ideal (simulator default)", None, None, False),
        ("16 cores x 256 lanes, this RTL", 16, 256, False),
        ("16 cores x 256 lanes, ping-pong I/O", 16, 256, True),
        ("4 cores x 1024 lanes, this RTL", 4, 1024, False),
        ("1 core x 4096 lanes, this RTL", 1, 4096, False),
        ("1 core x 4096 lanes, ping-pong I/O", 1, 4096, True)]
table(["organisation (ARK-class: 4096 lanes)", "efficiency at N = 2^16"],
      [[name, "1.000" if p is None else f"{cycles.efficiency(N, p, overlap_io=ov):.3f}"] for name, c, p, ov in orgs])

ALL = dict(min_ks=True, seeded_keys=True, otf_plaintexts=True)


def boot(hw, **opts):
    return summarise(simulate(bootstrap_trace(ARK, BootOptions(**opts)), SimConfig(hw)))


for title, hw, lanes, cases in (
        ("ARK-class design", ARK_HW, 4096, orgs),
        ("Small digital design (NTT-starved)", SMALL_HW, 512,
         [("ideal (simulator default)", None, None, False), ("2 cores x 256 lanes, this RTL", 2, 256, False),
          ("2 cores x 256 lanes, ping-pong I/O", 2, 256, True), ("1 core x 512 lanes, this RTL", 1, 512, False)])):
    OUT.append(f"\n**{title}** ({lanes} butterfly lanes), bootstrap latency and verdict\n")
    rows = []
    for name, c, p, ov in cases:
        eff = 1.0 if p is None else cycles.efficiency(N, p, overlap_io=ov)
        hw2 = hw.with_(ntt_bfly_per_cycle=lanes * eff)
        r0, r1 = boot(hw2), boot(hw2, **ALL)
        rows.append([name, f"{eff:.3f}", f"{1e3 * r0['per_bootstrap_s']:.2f} ms", r0["bound"],
                     f"{1e3 * r1['per_bootstrap_s']:.2f} ms", r1["bound"]])
    table(["NTT organisation", "efficiency", "baseline algorithm", "verdict", "Min-KS + seeded keys + OTF pt",
           "verdict"], rows)

# 6 ── test suite ───────────────────────────────────────────────────────
h("6. Test suite")
xml = TMP / "pytest.xml"
t0 = time.time()
p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"],
                   cwd=ROOT, capture_output=True, text=True, env={**os.environ})
n, fails = rtl.count(xml)
OUT.append(f"`pytest` (model tests + RTL tests on {rtl.simulator()}): {n} tests, {fails} failures, "
           f"{time.time() - t0:.0f} s.")
assert p.returncode == 0, p.stdout[-3000:]

OUT.insert(0, f"# Results (generated by examples/results.py)\n\nRecorded on {platform.machine()} Linux, "
              f"Python {platform.python_version()}. Every number in the README and in deck SimEng 05 comes from here.")
(HERE / "results.md").write_text("\n".join(OUT) + "\n")
print("\n".join(OUT))
