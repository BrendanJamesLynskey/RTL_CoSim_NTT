"""cocotb testbench for rtl/ntt_core.sv: whole transforms against the golden NTT.

The golden model is FHE_Accelerator_Sim's ``ntt_reference`` (the O(n^2)
definition), reached through ntt_cosim.model. For every transform size the core
supports, random vectors go in through a stream transactor and the results come
out through another; the scoreboard compares all n words. The testbench also
counts cycles (load, compute, unload), which examples/results.py turns into the
NTT throughput the FHE simulator assumes.

  NTT_PRIME   modulus (W-bit prime, q = 1 mod 2^LOGN)
  NTT_SEED    random seed          NTT_REPS  random vectors per size
  NTT_OUT     JSON file for the measured cycle counts
"""

from __future__ import annotations

import json
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

from ntt_cosim.model import barrett_mu, find_psi, ntt_reference, twiddles


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)), 0)


class Core:
    """Transactors for the core: configuration, twiddle loading, input and output streams."""

    def __init__(self, dut):
        self.dut = dut
        self.W = len(dut.cfg_q)
        self.P = len(dut.in_data) // self.W
        self.LOGN = len(dut.tw_addr) + 1
        self.mask = (1 << self.W) - 1

    async def reset(self):
        d = self.dut
        d.rst_n.value = 0
        d.in_valid.value = 0
        d.out_ready.value = 0
        d.tw_we.value = 0
        d.tw_addr.value = 0
        d.tw_data.value = 0
        d.in_data.value = 0
        d.cfg_q.value = 0
        d.cfg_mu.value = 0
        d.cfg_logn.value = self.LOGN
        cocotb.start_soon(Clock(d.clk, 10, units="ns").start())
        await ClockCycles(d.clk, 3)
        d.rst_n.value = 1
        await RisingEdge(d.clk)

    async def configure(self, q: int, w: int, logn: int):
        d = self.dut
        d.cfg_q.value = q
        d.cfg_mu.value = barrett_mu(q, self.W)
        d.cfg_logn.value = logn
        for k, v in enumerate(twiddles(q, w, (1 << logn) // 2)):
            d.tw_we.value, d.tw_addr.value, d.tw_data.value = 1, k, v
            await RisingEdge(d.clk)
        d.tw_we.value = 0
        await RisingEdge(d.clk)

    async def transform(self, x: list[int], rng: random.Random | None = None,
                        p_in_gap: float = 0.0, p_out_stall: float = 0.0) -> tuple[list[int], dict]:
        """Stream x in, collect the result; optional random input gaps and output stalls.

        One loop iteration per clock cycle: at the falling edge the transactors drive this
        cycle's valid/ready/data, then (read-only phase) see the DUT's response for the same
        cycle and record any handshake, which the next rising edge commits.
        """
        d, P, n = self.dut, self.P, len(x)
        beats = n // P
        out: list[int] = []
        k = cycle = 0
        first_in = last_in = first_out = last_out = None
        while len(out) < n:
            await FallingEdge(d.clk)
            cycle += 1
            send = k < beats and not (rng is not None and rng.random() < p_in_gap)
            if send:
                d.in_data.value = sum(x[k * P + l] << (l * self.W) for l in range(P))
            d.in_valid.value = int(send)
            ready = not (rng is not None and rng.random() < p_out_stall)
            d.out_ready.value = int(ready)
            await ReadOnly()
            if send and d.in_ready.value == 1:
                first_in = cycle if first_in is None else first_in
                last_in = cycle
                k += 1
            if ready and d.out_valid.value == 1:
                v = int(d.out_data.value)
                out.extend((v >> (l * self.W)) & self.mask for l in range(P))
                first_out = cycle if first_out is None else first_out
                last_out = cycle
            if cycle > 64 * n + 10000:
                raise AssertionError(f"timeout: {k}/{beats} beats in, {len(out)}/{n} words out")
        await FallingEdge(d.clk)
        d.in_valid.value = 0
        d.out_ready.value = 0
        cycles = {"load": last_in - first_in + 1,
                  "load_to_first_out": first_out - last_in,
                  "unload": last_out - first_out + 1,
                  "total": last_out - first_in + 1,
                  "compute_counter": int(d.stat_compute_cycles.value)}
        return out, cycles


def prime_and_root(core: Core, logn: int) -> tuple[int, int]:
    q = env_int("NTT_PRIME", 0)
    if q == 0:
        raise ValueError("set NTT_PRIME")
    return q, find_psi(q, 1 << logn)


@cocotb.test()
async def ntt_all_sizes(dut):
    """Random vectors at every supported size, bit-exact against the golden NTT; cycle counts recorded."""
    core = Core(dut)
    await core.reset()
    rng = random.Random(env_int("NTT_SEED", 1))
    reps = env_int("NTT_REPS", 2)
    lo = max(1, (core.P - 1).bit_length() + 1)        # n >= 2P
    measured = []
    for logn in range(lo, core.LOGN + 1):
        q, w = prime_and_root(core, logn)
        await core.configure(q, w, logn)
        for r in range(reps):
            x = [rng.randrange(q) for _ in range(1 << logn)]
            got, cyc = await core.transform(x)
            want = ntt_reference(x, q, w)
            assert got == want, f"n={1 << logn} rep {r}: first mismatch at {next(i for i in range(len(x)) if got[i] != want[i])}"
        measured.append({"n": 1 << logn, "P": core.P, **cyc})
        dut._log.info(f"n={1 << logn:5} P={core.P}: {cyc}")
    out = os.environ.get("NTT_OUT")
    if out:
        with open(out, "w") as f:
            json.dump({"W": core.W, "P": core.P, "LOGN": core.LOGN, "prime": env_int("NTT_PRIME", 0),
                       "sizes": measured}, f, indent=1)


@cocotb.test()
async def ntt_directed(dut):
    """Impulse -> all ones; constant c -> (n c, 0, ..., 0); a single shifted impulse -> powers of w."""
    core = Core(dut)
    await core.reset()
    logn = core.LOGN
    n = 1 << logn
    q, w = prime_and_root(core, logn)
    await core.configure(q, w, logn)
    got, _ = await core.transform([1] + [0] * (n - 1))
    assert got == [1] * n
    got, _ = await core.transform([q - 1] * n)
    assert got == [(n * (q - 1)) % q] + [0] * (n - 1)
    got, _ = await core.transform([0, 1] + [0] * (n - 2))
    assert got == [pow(w, k, q) for k in range(n)]


@cocotb.test()
async def ntt_roundtrip(dut):
    """INTT(NTT(x)) = x, the inverse run on the same core with w^-1 and scaled by n^-1."""
    core = Core(dut)
    await core.reset()
    rng = random.Random(env_int("NTT_SEED", 1) + 7)
    logn = core.LOGN
    n = 1 << logn
    q, w = prime_and_root(core, logn)
    x = [rng.randrange(q) for _ in range(n)]
    await core.configure(q, w, logn)
    X, _ = await core.transform(x)
    await core.configure(q, pow(w, -1, q), logn)
    y, _ = await core.transform(X)
    n_inv = pow(n, -1, q)
    assert [v * n_inv % q for v in y] == x


@cocotb.test()
async def ntt_backpressure(dut):
    """Random input gaps and output stalls change the timing, never the answer."""
    core = Core(dut)
    await core.reset()
    rng = random.Random(env_int("NTT_SEED", 1) + 13)
    for logn in sorted({max(2, (core.P - 1).bit_length() + 1), core.LOGN}):
        q, w = prime_and_root(core, logn)
        await core.configure(q, w, logn)
        x = [rng.randrange(q) for _ in range(1 << logn)]
        got, cyc = await core.transform(x, rng, p_in_gap=0.3, p_out_stall=0.3)
        assert got == ntt_reference(x, q, w), f"n={1 << logn} with backpressure"
