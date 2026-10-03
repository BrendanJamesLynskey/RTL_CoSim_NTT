"""Stimulus generators: uniform random and constrained random.

Uniform random operands almost never reach some corners. The clearest is the
Barrett multiplier's *second* correction step (r0 >= 2q), which for many primes
occurs a handful of times per million uniform products, or never. A constrained
generator biases the distribution towards the corners (0, 1, q - 1 and operands
just below q) while still covering the whole range; functional coverage
(``coverage.py``) shows whether the bias closed the holes.
"""

from __future__ import annotations

import random


def uniform(rng: random.Random, q: int) -> tuple[int, int, int]:
    return rng.randrange(q), rng.randrange(q), rng.randrange(q)


def _corner(rng: random.Random, q: int) -> int:
    r = rng.random()
    if r < 0.10:
        return rng.choice((0, 1, q - 1, q - 2))
    if r < 0.55:
        return q - 1 - rng.randrange(max(1, q >> 8))      # the top 1/256 of the range
    return rng.randrange(q)


def constrained(rng: random.Random, q: int) -> tuple[int, int, int]:
    """(a, b, w): corner-biased operands and twiddles."""
    return _corner(rng, q), _corner(rng, q), _corner(rng, q)


GENERATORS = {"uniform": uniform, "constrained": constrained}


def gap(rng: random.Random, p_gap: float = 0.25, max_gap: int = 3) -> int:
    """Idle cycles before the next transaction: mostly back to back, sometimes a bubble."""
    return rng.randint(1, max_gap) if rng.random() < p_gap else 0
