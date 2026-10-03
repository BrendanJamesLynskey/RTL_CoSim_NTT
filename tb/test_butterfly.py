"""cocotb testbench for rtl/ntt_butterfly.sv (and the Barrett multiplier inside it).

Structure (a UVM-style testbench in a few dozen lines of Python):

* **driver**: applies one transaction per cycle (or a bubble), from a generator;
* **monitor**: samples (x, y, tag, corr) whenever out_valid is high;
* **scoreboard**: the bit-accurate Python model predicts each result, including
  how many Barrett correction steps the multiplier took, and the monitor's
  stream must match it in order;
* **functional coverage**: every transaction is sampled into named bins
  (src/ntt_cosim/coverage.py) and the bins are written to JSON.

Run through the pytest wrapper (tests/test_rtl.py) or examples/results.py. Settings
come from environment variables so one build serves many campaigns:

  BF_PRIME   modulus (W-bit prime)          BF_STIM  uniform | constrained
  BF_N       transactions                    BF_SEED  random seed
  BF_OUT     JSON file for coverage and activity results
"""

from __future__ import annotations

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

from ntt_cosim.coverage import butterfly_coverage, sample_butterfly
from ntt_cosim.model import barrett_mu, butterfly
from ntt_cosim.stimulus import GENERATORS, gap

LAT = 5


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)), 0)


async def reset(dut, q: int, mu: int) -> None:
    dut.rst_n.value = 0
    dut.in_valid.value = 0
    dut.a.value = 0
    dut.b.value = 0
    dut.w.value = 0
    dut.in_tag.value = 0
    dut.q.value = q
    dut.mu.value = mu
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


def width(dut) -> int:
    return len(dut.a)


@cocotb.test()
async def butterfly_random(dut):
    """Constrained-random (or uniform) stimulus, in-order scoreboard and functional coverage."""
    W = width(dut)
    q = env_int("BF_PRIME", 0)
    if q == 0:
        raise ValueError("set BF_PRIME")
    mu = barrett_mu(q, W)
    n = env_int("BF_N", 2000)
    rng = random.Random(env_int("BF_SEED", 1))
    gen = GENERATORS[os.environ.get("BF_STIM", "constrained")]
    await reset(dut, q, mu)

    expected: list[tuple[int, int, int, int]] = []
    cov = butterfly_coverage()
    errors: list[str] = []
    received = 0

    async def monitor():
        nonlocal received
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if dut.out_valid.value == 1:
                got = (int(dut.x.value), int(dut.y.value), int(dut.out_tag.value), int(dut.corr.value))
                want = expected[received]
                if got != want and len(errors) < 10:
                    errors.append(f"txn {received}: got {got}, want {want}")
                received += 1

    cocotb.start_soon(monitor())
    for i in range(n):
        g = gap(rng)
        if g:
            dut.in_valid.value = 0
            await ClockCycles(dut.clk, g)
        a, b, w = gen(rng, q)
        x, y, corr = butterfly(a, b, w, q, W, mu)
        t = (x - a) % q
        sample_butterfly(cov, a, b, w, q, t, corr, g)
        expected.append((x, y, i & 0xFF, corr))
        dut.a.value, dut.b.value, dut.w.value, dut.in_tag.value = a, b, w, i & 0xFF
        dut.in_valid.value = 1
        await RisingEdge(dut.clk)
    dut.in_valid.value = 0
    await ClockCycles(dut.clk, LAT + 2)

    out = os.environ.get("BF_OUT")
    if out:
        with open(out, "w") as f:
            json.dump({"prime": q, "W": W, "n": n, "stimulus": gen.__name__, "received": received,
                       "errors": len(errors), "coverage": json.loads(cov.to_json())}, f, indent=1)
    dut._log.info("\n" + cov.report())
    assert received == n, f"received {received} of {n} results"
    assert not errors, "scoreboard mismatches:\n" + "\n".join(errors)


@cocotb.test()
async def butterfly_latency_and_throughput(dut):
    """One result per cycle, LAT cycles after its input."""
    W = width(dut)
    q = env_int("BF_PRIME", 0)
    mu = barrett_mu(q, W)
    await reset(dut, q, mu)
    dut.a.value, dut.b.value, dut.w.value, dut.in_tag.value = 1, 2, 3, 0x5A
    dut.in_valid.value = 1
    await RisingEdge(dut.clk)
    dut.in_valid.value = 0
    for cycle in range(1, 10):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if dut.out_valid.value == 1:
            # the input was captured at the edge before the loop: latency counts that edge too
            assert cycle + 1 == LAT, f"latency {cycle + 1}, expected {LAT}"
            assert int(dut.out_tag.value) == 0x5A
            assert (int(dut.x.value), int(dut.y.value)) == ((1 + 6) % q, (1 - 6) % q)
            break
    else:
        raise AssertionError("no output")
    await RisingEdge(dut.clk)
    # 64 back-to-back inputs produce 64 back-to-back outputs
    seen = []

    async def count():
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            seen.append(int(dut.out_valid.value))

    counter = cocotb.start_soon(count())
    for i in range(64):
        dut.a.value, dut.b.value, dut.w.value, dut.in_valid.value = i, i, 1, 1
        await RisingEdge(dut.clk)
    dut.in_valid.value = 0
    await ClockCycles(dut.clk, LAT + 2)
    counter.kill()
    ones = "".join(map(str, seen)).strip("0")
    assert ones == "1" * 64, f"outputs not back to back: {''.join(map(str, seen))}"


# ── switching activity ───────────────────────────────────────────────
ACTIVITY_REGS = ("u_mul.x1", "u_mul.x2", "u_mul.q3", "u_mul.r0", "u_mul.r", "x", "y")


def _handle(dut, path: str):
    h = dut
    for part in path.split("."):
        h = getattr(h, part)
    return h


@cocotb.test()
async def butterfly_activity(dut):
    """Register toggles per butterfly for different data distributions (a power proxy)."""
    W = width(dut)
    q = env_int("BF_PRIME", 0)
    mu = barrett_mu(q, W)
    n = env_int("BF_N_ACT", 500)
    await reset(dut, q, mu)
    regs = [_handle(dut, p) for p in ACTIVITY_REGS]
    rng = random.Random(env_int("BF_SEED", 1))
    w_fixed = rng.randrange(q)
    cases = {
        "uniform random a, b, w": lambda: (rng.randrange(q), rng.randrange(q), rng.randrange(q)),
        "random data, one twiddle": lambda: (rng.randrange(q), rng.randrange(q), w_fixed),
        "small data (< 2^10), one twiddle": lambda: (rng.randrange(1024), rng.randrange(1024), w_fixed),
        "constant operands": lambda: (12345, 67890, w_fixed),
    }
    results = {}
    state = {"on": False, "toggles": 0, "prev": None}

    async def sampler():
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            vals = [int(r.value) for r in regs]
            if state["on"] and state["prev"] is not None:
                state["toggles"] += sum(bin(v ^ p).count("1") for v, p in zip(vals, state["prev"]))
            state["prev"] = vals

    cocotb.start_soon(sampler())
    for name, draw in cases.items():
        dut.in_valid.value = 0
        await ClockCycles(dut.clk, LAT + 2)
        state["on"], state["toggles"] = True, 0
        for i in range(n):
            dut.a.value, dut.b.value, dut.w.value = draw()
            dut.in_valid.value = 1
            await RisingEdge(dut.clk)
        dut.in_valid.value = 0
        await ClockCycles(dut.clk, LAT)       # let the last inputs reach the outputs
        state["on"] = False
        results[name] = state["toggles"] / n
        dut._log.info(f"{name}: {results[name]:.1f} register toggles per butterfly")
    out = os.environ.get("BF_OUT_ACT")
    if out:
        with open(out, "w") as f:
            json.dump({"prime": q, "W": W, "n": n, "registers": list(ACTIVITY_REGS),
                       "toggles_per_butterfly": results}, f, indent=1)
    assert results["constant operands"] < results["uniform random a, b, w"]
