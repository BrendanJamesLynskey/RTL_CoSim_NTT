"""Tests of the Python models (no simulator needed): Barrett, butterfly, fast NTT vs the golden model."""

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ntt_cosim.model import (barrett, barrett_mu, bitrev, butterfly, find_psi, intt_fast, is_prime, ntt_fast,
                             ntt_primes, ntt_reference, twiddles)
from ntt_cosim.primes import PRIMES

W = 50


def test_primes_are_ntt_friendly():
    for name, q in PRIMES.items():
        assert is_prime(q), name
        assert q.bit_length() == W, name
        assert (q - 1) % (1 << 11) == 0, name        # transforms up to n = 2048


def test_ntt_primes_finds_known_prime():
    assert ntt_primes(17, 1 << 12, 1)[0] == max(q for q in range(1, 1 << 17, 1 << 12) if is_prime(q) and q > 1 << 16)


def test_is_prime_small():
    assert [n for n in range(50) if is_prime(n)] == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]


def test_mu_needs_exact_width():
    with pytest.raises(ValueError):
        barrett_mu(97, W)


@pytest.mark.parametrize("q", list(PRIMES.values()))
@settings(max_examples=300, deadline=None)
@given(data=st.data())
def test_barrett_matches_modmul(q, data):
    a = data.draw(st.integers(0, q - 1))
    b = data.draw(st.integers(0, q - 1))
    r, corr = barrett(a, b, q, W)
    assert r == a * b % q
    assert corr in (0, 1, 2)


def test_barrett_second_correction_happens_for_some_primes():
    """The reason for the 'corr_2' coverage bin: reachable, but rare under uniform stimulus."""
    q = PRIMES["mid"]
    rng = random.Random(5)
    corr2 = sum(barrett(q - 1 - rng.randrange(q >> 8), q - 1 - rng.randrange(q >> 8), q, W)[1] == 2
                for _ in range(20000))
    assert corr2 > 0


@settings(max_examples=200, deadline=None)
@given(st.integers(0, PRIMES["near"] - 1), st.integers(0, PRIMES["near"] - 1), st.integers(0, PRIMES["near"] - 1))
def test_butterfly(a, b, w):
    q = PRIMES["near"]
    x, y, _ = butterfly(a, b, w, q, W)
    assert x == (a + w * b) % q and y == (a - w * b) % q


def test_bitrev():
    assert [bitrev(i, 3) for i in range(8)] == [0, 4, 2, 6, 1, 5, 3, 7]
    assert bitrev(0, 0) == 0


def test_twiddles():
    q = PRIMES["near"]
    w = find_psi(q, 16)
    assert twiddles(q, w, 8) == [pow(w, k, q) for k in range(8)]


@pytest.mark.parametrize("logn", range(1, 9))
def test_fast_ntt_equals_golden(logn):
    q = PRIMES["near"]
    n = 1 << logn
    w = find_psi(q, n)
    rng = random.Random(logn)
    x = [rng.randrange(q) for _ in range(n)]
    assert ntt_fast(x, q, w) == ntt_reference(x, q, w)
    assert intt_fast(ntt_fast(x, q, w), q, w) == x


def test_w_is_primitive():
    q = PRIMES["mid"]
    w = find_psi(q, 1024)
    assert pow(w, 1024, q) == 1 and pow(w, 512, q) != 1


def test_near_is_the_largest():
    assert ntt_primes(W, 1 << 11, 1)[0] == PRIMES["near"]
