"""Run the cocotb testbenches from pytest (marker ``rtl``; needs Verilator or Icarus and cocotb).

    SIM=verilator pytest -m rtl        # default simulator
    SIM=icarus    pytest -m rtl        # fallback

Each test builds (once) and runs a testbench, then checks the JUnit XML it wrote
and any JSON results: coverage closure, cycle counts against the cycle model, and
that a seeded bug is caught by constrained-random stimulus but escapes uniform
stimulus, with the escape visible as holes in the coverage crosses.
"""

from __future__ import annotations

import json
import os

import pytest

from ntt_cosim import cycles
from ntt_cosim.primes import PRIMES

pytestmark = pytest.mark.rtl
rtl = pytest.importorskip("ntt_cosim.rtl")
NIGHTLY = os.environ.get("NIGHTLY") == "1"


def run_bf(tmp_path, prime, stim, n, seed=1, defines=None, testcase=None):
    out = tmp_path / "bf.json"
    env = {"BF_PRIME": hex(prime), "BF_STIM": stim, "BF_N": str(n), "BF_SEED": str(seed),
           "BF_OUT": str(out), "BF_OUT_ACT": str(tmp_path / "act.json"), "BF_N_ACT": "200"}
    tests, fails, _ = rtl.run("ntt_butterfly", defines=defines, env=env, testcase=testcase,
                              results_xml=tmp_path / "bf.xml")
    cov = json.loads(out.read_text())["coverage"] if out.exists() else None
    return tests, fails, cov


@pytest.mark.parametrize("name", ["near", "mid"])
def test_butterfly_constrained_random(tmp_path, name):
    tests, fails, cov = run_bf(tmp_path, PRIMES[name], "constrained", 20000 if NIGHTLY else 4000)
    assert tests == 3 and fails == 0
    # closure: every bin hit, except the second Barrett correction for the 'near' prime (see primes.py)
    expected_holes = ["corr_2", "corr_2_x_sum_wraps", "corr_2_x_diff_borrows"] if name == "near" else []
    assert cov["holes"] == expected_holes


def test_seeded_bug_caught_by_constrained_random(tmp_path):
    tests, fails, cov = run_bf(tmp_path, PRIMES["mid"], "constrained", 4000, defines={"INJECT_BUG": 1},
                               testcase="butterfly_random")
    assert tests == 1 and fails == 1, "the seeded bug must be caught"
    assert cov is None or cov["bins"]["corr_2_x_sum_wraps"] + cov["bins"]["corr_2_x_diff_borrows"] > 0


def test_seeded_bug_escapes_uniform_random_but_coverage_shows_it(tmp_path):
    tests, fails, cov = run_bf(tmp_path, PRIMES["mid"], "uniform", 4000, defines={"INJECT_BUG": 1},
                               testcase="butterfly_random")
    assert tests == 1 and fails == 0, "uniform stimulus does not reach the bug"
    # it may even reach the second correction, but only where the add/subtract stage masks it:
    # the crosses that make the bug observable stay empty, and coverage says so
    assert {"corr_2_x_sum_wraps", "corr_2_x_diff_borrows"} <= set(cov["holes"])


CORES = [(1, 8), (4, 10)] + ([(2, 10), (8, 10)] if NIGHTLY else [])


@pytest.mark.parametrize("p,logn", CORES)
def test_ntt_core_against_golden_and_cycle_model(tmp_path, p, logn):
    out = tmp_path / "ntt.json"
    env = {"NTT_PRIME": hex(PRIMES["mid"]), "NTT_OUT": str(out), "NTT_REPS": "3" if NIGHTLY else "1"}
    tests, fails, _ = rtl.run("ntt_core", parameters={"P": p, "LOGN": logn}, env=env,
                              results_xml=tmp_path / "ntt.xml")
    assert tests == 4 and fails == 0
    for m in json.loads(out.read_text())["sizes"]:
        n = m["n"]
        assert m["compute_counter"] == cycles.compute_cycles(n, p), m
        assert m["total"] == cycles.total_cycles(n, p), m
        assert m["load"] == m["unload"] == n // p
