"""Cycle model of rtl/ntt_core.sv, and the NTT efficiency it implies for an accelerator.

The RTL's compute phase is log2(n) stages, each issuing n/(2P) butterfly groups and
then draining the butterfly pipeline before the next stage may read its results:

    compute(n, P) = log2(n) * (n / (2P) + DRAIN)          DRAIN = butterfly latency + 1 = 6
    total(n, P)   = 2n/P + compute(n, P)                  (load and unload not overlapped)

tests/test_rtl.py checks these formulas against the cycle counts the testbench
measures for every size and lane count it builds. The formulas then extrapolate
to the ring degrees an FHE accelerator runs (n = 2^16), which are too large for a
register-array RTL model to simulate quickly.

``efficiency`` is the fraction of peak butterfly throughput a core sustains:
butterflies / (P x cycles). An accelerator model that assumes one butterfly per
lane per cycle (FHE_Accelerator_Sim's ``ntt_bfly_per_cycle``) should be derated by it.
"""

from __future__ import annotations

DRAIN = 6


def log2(n: int) -> int:
    if n <= 0 or n & (n - 1):
        raise ValueError(f"{n} is not a power of two")
    return n.bit_length() - 1


def compute_cycles(n: int, p: int, drain: int = DRAIN) -> int:
    if n < 2 * p:
        raise ValueError("needs n >= 2P")
    return log2(n) * (n // (2 * p) + drain)


def total_cycles(n: int, p: int, drain: int = DRAIN, overlap_io: bool = False) -> int:
    """Cycles per transform back to back. With overlap_io (a ping-pong buffer, not in this RTL),
    loading the next transform and unloading the last are hidden behind compute."""
    c = compute_cycles(n, p, drain)
    io = 2 * n // p
    return max(c, io) if overlap_io else c + io


def butterflies(n: int) -> int:
    return n // 2 * log2(n)


def efficiency(n: int, p: int, drain: int = DRAIN, overlap_io: bool = False) -> float:
    return butterflies(n) / (p * total_cycles(n, p, drain, overlap_io))
