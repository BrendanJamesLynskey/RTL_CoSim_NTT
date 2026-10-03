"""Performance gate: the NTT core's cycle counts must not regress against ci/cycle_baseline.json.

Cycle counts are deterministic, so the gate is exact (no noise margin): any transform
that takes more cycles than the baseline fails the build, and an improvement is
reported so the baseline can be re-blessed with --bless.

    python ci/cycle_gate.py [--bless]
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from ntt_cosim import rtl
from ntt_cosim.primes import PRIMES

HERE = Path(__file__).parent
BASELINE = HERE / "cycle_baseline.json"
CONFIGS = [(1, 8), (4, 10)]


def measure() -> dict:
    out: dict = {}
    tmp = Path(tempfile.mkdtemp())
    for p, logn in CONFIGS:
        js = tmp / f"ntt_{p}.json"
        tests, fails, _ = rtl.run("ntt_core", parameters={"P": p, "LOGN": logn},
                                  env={"NTT_PRIME": hex(PRIMES["mid"]), "NTT_OUT": str(js), "NTT_REPS": "1"},
                                  testcase="ntt_all_sizes", results_xml=tmp / "gate.xml")
        if fails:
            sys.exit(f"P={p}: functional failure, see {tmp}")
        for m in json.loads(js.read_text())["sizes"]:
            out[f"P{p}_n{m['n']}"] = m["total"]
    return out


def main() -> int:
    now = measure()
    if "--bless" in sys.argv:
        BASELINE.write_text(json.dumps(now, indent=1) + "\n")
        print(f"blessed {len(now)} entries")
        return 0
    base = json.loads(BASELINE.read_text())
    lines = ["# NTT core cycle gate", "", "| config | baseline | now | change |", "|---|---|---|---|"]
    worse = []
    for k, b in base.items():
        n = now.get(k)
        if n is None:
            worse.append(k)
            lines.append(f"| {k} | {b} | missing | |")
            continue
        lines.append(f"| {k} | {b} | {n} | {n - b:+d} |")
        if n > b:
            worse.append(k)
    lines.append("")
    lines.append("FAIL: " + ", ".join(worse) if worse else "PASS: no transform takes more cycles than the baseline")
    Path("perf_report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
