"""The 50-bit NTT-friendly primes the testbenches use (q = 1 mod 2^11, so n <= 2048).

* ``near``: the largest such prime below 2^50. Its Barrett constant is almost exact,
  so the multiplier's second correction step was never observed in our campaigns
  (uniform, constrained and directed); coverage excludes that bin for this prime.
* ``mid``: a prime near 0.9 x 2^50, for which corner-biased stimulus reaches the
  second correction step about once in a hundred products.
"""

PRIMES = {
    "near": 0x3FFFFFFFFC001,
    "mid": 0x399999999D801,
}
