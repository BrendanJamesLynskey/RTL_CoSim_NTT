"""Bit-accurate Python models of the RTL, and the golden NTT they are checked against.

Two levels of model, as in a typical verification bridge:

* **Functional golden model**: what the hardware must compute, with no notion of
  how. The NTT golden model is ``fhe_sim.precision.ntt_reference`` from
  FHE_Accelerator_Sim (the O(n^2) definition X_k = sum_j x_j w^(jk) mod q), so the
  RTL is checked against the same code the accelerator simulator reasons with.
* **Bit-accurate (transaction-level) model**: mirrors the RTL's arithmetic step by
  step (Barrett's quotient estimate and how many correction steps it needed), so
  a scoreboard can compare internal behaviour and functional coverage can be
  measured on the *model's* view of each transaction.
"""

from __future__ import annotations

from fhe_sim.precision import find_psi, ntt_reference

__all__ = ["barrett_mu", "barrett", "butterfly", "ntt_reference", "ntt_fast", "intt_fast", "find_psi",
           "twiddles", "bitrev", "is_prime", "ntt_primes"]


# ── primes ──────────────────────────────────────────────────────────────
def is_prime(n: int) -> bool:
    """Deterministic Miller-Rabin for n < 3.3e24 (bases up to 41)."""
    if n < 2:
        return False
    small = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41)
    for p in small:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in small:
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def ntt_primes(w_bits: int, order: int, count: int, start: int | None = None) -> list[int]:
    """The ``count`` largest primes q < ``start`` (default 2^w_bits) with q = 1 mod ``order``
    and exactly ``w_bits`` bits, so the Barrett constant fits the RTL."""
    hi = (1 << w_bits) if start is None else start
    c = (hi - 1) // order * order + 1
    out = []
    while len(out) < count:
        if c >= hi:
            c -= order
            continue
        if c < 1 << (w_bits - 1):
            raise ValueError("ran out of w_bits-bit primes")
        if is_prime(c):
            out.append(c)
        c -= order
    return out


# ── Barrett multiplier and butterfly (bit-accurate) ─────────────────────
def barrett_mu(q: int, w_bits: int) -> int:
    """mu = floor(2^(2W) / q); needs 2^(W-1) < q < 2^W so that mu < 2^(W+1)."""
    if not (1 << (w_bits - 1)) < q < (1 << w_bits):
        raise ValueError(f"q must have exactly {w_bits} bits")
    return (1 << (2 * w_bits)) // q


def barrett(a: int, b: int, q: int, w_bits: int, mu: int | None = None) -> tuple[int, int]:
    """(a * b mod q, correction steps), computed exactly as rtl/mod_mul_barrett.sv does."""
    if mu is None:
        mu = barrett_mu(q, w_bits)
    x = a * b
    q3 = ((x >> (w_bits - 1)) * mu) >> (w_bits + 1)
    r0 = (x - q3 * q) & ((1 << (w_bits + 2)) - 1)       # the RTL keeps W + 2 bits
    if r0 >= 2 * q:
        return r0 - 2 * q, 2
    if r0 >= q:
        return r0 - q, 1
    return r0, 0


def butterfly(a: int, b: int, w: int, q: int, w_bits: int, mu: int | None = None) -> tuple[int, int, int]:
    """Cooley-Tukey butterfly (a + w*b, a - w*b) mod q, plus the multiplier's correction count."""
    t, corr = barrett(w, b, q, w_bits, mu)
    x = a + t - q if a + t >= q else a + t
    y = a - t if a >= t else a - t + q
    return x, y, corr


# ── NTT ─────────────────────────────────────────────────────────────────
def bitrev(i: int, logn: int) -> int:
    return int(f"{i:0{logn}b}"[::-1], 2) if logn else 0


def twiddles(q: int, w: int, half: int) -> list[int]:
    """w^0 .. w^(half - 1) mod q, the table the RTL's twiddle memory holds."""
    out, x = [], 1
    for _ in range(half):
        out.append(x)
        x = x * w % q
    return out


def ntt_fast(x: list[int], q: int, w: int) -> list[int]:
    """Iterative radix-2 DIT NTT in the RTL's order (bit-reversed load, stage by stage).

    Equal to ``ntt_reference`` (tested), O(n log n), so large transforms can be checked
    quickly; it is also the reference for the RTL's stage-by-stage memory contents.
    """
    n = len(x)
    logn = n.bit_length() - 1
    a = [0] * n
    for i, v in enumerate(x):
        a[bitrev(i, logn)] = v
    tw = twiddles(q, w, n // 2)
    for s in range(logn):
        m = 1 << s
        for t in range(n // 2):
            j = t & (m - 1)
            i0 = ((t >> s) << (s + 1)) | j
            i1 = i0 | m
            wt = tw[j << (logn - 1 - s)]
            u, v = a[i0], a[i1] * wt % q
            a[i0], a[i1] = (u + v) % q, (u - v) % q
    return a


def intt_fast(X: list[int], q: int, w: int) -> list[int]:
    """Inverse: a forward transform with w^-1, then scale by n^-1 (what the testbench does)."""
    n = len(X)
    y = ntt_fast(X, q, pow(w, -1, q))
    n_inv = pow(n, -1, q)
    return [v * n_inv % q for v in y]
