# RTL_CoSim_NTT

A **synthesisable SystemVerilog NTT butterfly pipeline** (Barrett modular multiplier,
Cooley-Tukey butterfly, and an iterative NTT core with P butterfly lanes), verified with
**[cocotb](https://www.cocotb.org) on [Verilator](https://www.veripool.org/verilator/)**
(Icarus Verilog as a fallback) against the Python NTT of
[FHE_Accelerator_Sim](https://github.com/BrendanJamesLynskey/FHE_Accelerator_Sim) as the
golden model, with the cycle counts the RTL measures fed back into that simulator.

It is the companion code for deck 05 (verification bridge: cocotb, Verilator and golden models)
of the [Simulation Engineering Toolkit](https://github.com/BrendanJamesLynskey/SimEng_Hub_Toolkit)
series.

The point is the *bridge* between an architecture simulator and RTL, in both directions:

* **The simulator checks the RTL.** Every transform the core computes, at every size from
  2P to 1024 points, is compared word for word with `fhe_sim.precision.ntt_reference`, the
  same code the FHE simulator reasons with. A bit-accurate Python model of the Barrett
  multiplier is the scoreboard for the butterfly, down to how many correction steps each
  product needed.
* **The RTL calibrates the simulator.** The simulator assumes one butterfly per lane per
  cycle. The RTL measures what a P-lane core really sustains (pipeline drain between
  stages, load and unload), a cycle model fits every measurement exactly, and re-running
  the FHE simulator with that efficiency changes its answers. For one design, it changes
  which resource it says is the bottleneck.
* **Coverage, not vector counts, says when you are done.** Uniform random stimulus almost
  never reaches the multiplier's second Barrett correction. A seeded bug there escapes a
  passing uniform-random regression; constrained-random stimulus catches it, and the
  functional coverage crosses show the difference before anyone looks at a waveform.

All numbers below come from [`examples/results.md`](examples/results.md).

---

## Quick start

```bash
sudo apt-get install verilator iverilog          # or a user-space Verilator (see below)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[rtl,test]"                     # cocotb 1.9.2, pytest, hypothesis, fhe-sim

pytest -m "not rtl"                              # model tests: Barrett, butterfly, fast NTT vs golden
pytest -m rtl                                    # cocotb on Verilator (SIM=icarus for Icarus)
python examples/results.py                       # campaigns, cycle counts, FHE calibration -> results.md
python ci/cycle_gate.py                          # cycle-count regression gate
```

`fhe-sim` installs from [FHE_Accelerator_Sim](https://github.com/BrendanJamesLynskey/FHE_Accelerator_Sim)
(`pip install git+https://github.com/BrendanJamesLynskey/FHE_Accelerator_Sim`).
cocotb is pinned to 1.9.2 because cocotb 2.0 needs Verilator 5.036 or newer and Ubuntu
24.04 ships 5.020; the testbenches use only APIs common to both.

Without root, Verilator can be unpacked from the Ubuntu package:
`apt-get download verilator && dpkg -x verilator_*.deb root`, then set `VERILATOR_ROOT` to
`root/usr/share/verilator` and link `root/usr/bin/verilator*` into `$VERILATOR_ROOT/bin`.

---

## The RTL

| File | What | Latency |
|------|------|---------|
| [`rtl/mod_mul_barrett.sv`](rtl/mod_mul_barrett.sv) | `a*b mod q`, run-time modulus (one per RNS limb), Barrett reduction with a precomputed `mu = floor(2^2W / q)` and up to two correction steps | 4 |
| [`rtl/ntt_butterfly.sv`](rtl/ntt_butterfly.sv) | Cooley-Tukey butterfly `(a + w*b, a - w*b) mod q`, one per cycle, with a tag that carries write-back addresses | 5 |
| [`rtl/ntt_core.sv`](rtl/ntt_core.sv) | Iterative radix-2 DIT NTT of any n up to 2^LOGN, P lanes, bit-reversed load, twiddle table loaded by the host, stream in and out | see below |

W = 50 bits (the FHE simulator's limb size). The core's cycle count is exactly

```
compute = log2(n) * (n/(2P) + 6)        # each stage drains the 5-stage butterfly pipeline
total   = 2n/P + compute                # load and unload are not overlapped with compute
```

The data memory is a register array with 2P read ports, which is fine for simulation and
small n; a silicon design would bank SRAMs (conflict-free for this access pattern with an
XOR bank mapping) without changing the cycle counts. `-Wall` lint-clean in Verilator 5.020.

## The testbenches

[`tb/test_butterfly.py`](tb/test_butterfly.py) is a UVM-style testbench in a few dozen lines
of Python: a **driver** applies a transaction per cycle (with random bubbles), a **monitor**
samples every output, a **scoreboard** compares it in order with the bit-accurate model
([`src/ntt_cosim/model.py`](src/ntt_cosim/model.py)), and every transaction is sampled
into **functional coverage** bins ([`src/ntt_cosim/coverage.py`](src/ntt_cosim/coverage.py)),
including the crosses that make a wrong second correction observable at the outputs.
[`tb/test_ntt_core.py`](tb/test_ntt_core.py) streams whole transforms through ready/valid
transactors and compares them with the golden NTT; it also checks an NTT/INTT round trip,
directed vectors and random backpressure, and records cycle counts.

Measured (Verilator 5.020, cocotb 1.9.2; `examples/results.md` §1):

| prime | RTL | stimulus | transactions | result | functional coverage | observable corr_2 hits |
|---|---|---|---|---|---|---|
| mid | clean | constrained | 20,000 | pass | 100% | 129 |
| mid | seeded bug | uniform | 4,000 | bug escaped | 53% | 0 |
| mid | seeded bug | constrained | 4,000 | **bug caught** | 100% | 29 |

Over a million model draws, uniform stimulus produces 8 observable second corrections per
million butterflies for the `mid` prime, against 7,307 for the corner-biased generator
(§2). For the `near` prime (just under 2^50) the Barrett constant is almost exact and no
second correction was ever observed, so that bin is excluded for it, with the reason stated.

## Feeding the RTL back into the simulator

Efficiency is the fraction of peak butterfly throughput a core sustains at N = 2^16. How
the aggregate lanes are organised into cores is an assumption; §5 shows several.

| ARK-class design, 4096 lanes as | efficiency | baseline bootstrap | Min-KS + seeded keys + OTF plaintexts |
|---|---|---|---|
| ideal (simulator default) | 1.000 | 13.94 ms, memory-bound | 7.19 ms, MAC-bound |
| 16 cores x 256 lanes, this RTL | 0.771 | 14.14 ms, memory-bound | 7.70 ms, MAC-bound |
| 1 core x 4096 lanes, this RTL | 0.500 | 14.63 ms, memory-bound | 9.06 ms, **NTT-bound** |

On the NTT-starved small design, the same derating costs 22% (17.46 to 21.33 ms with 2
cores of 256 lanes). A ping-pong buffer that overlaps load and unload with compute recovers
most of it (efficiency 0.955); that is a modelled what-if, not something this RTL implements.

## Switching activity

Register toggles per butterfly, sampled every cycle on the multiplier and output registers
(§4): 223 for uniform random operands, 164 for small data, 0.1 for constant operands.
It is a power proxy that weights every register bit equally; a gate-level power tool
weights each net by its capacitance.

## CI

* **GitHub Actions** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): model tests,
  then Verilator lint and the cocotb suite on Verilator and on Icarus.
* **Jenkins** ([`Jenkinsfile`](Jenkinsfile)): lint, model tests with coverage, RTL tests on
  both simulators with JUnit reports, an exact cycle-count gate against
  [`ci/cycle_baseline.json`](ci/cycle_baseline.json), `results.md` as an artifact, and a
  nightly seed sweep ([`ci/seed_sweep.py`](ci/seed_sweep.py)).

## Related

* [FHE_Accelerator_Sim](https://github.com/BrendanJamesLynskey/FHE_Accelerator_Sim): the
  golden NTT and the simulator calibrated here.
* [Interview_SystemVerilog](https://github.com/BrendanJamesLynskey/Interview_SystemVerilog):
  interview questions on SystemVerilog, UVM and coverage.
* [Interview_DSP challenge 06](https://github.com/BrendanJamesLynskey/Interview_DSP/blob/main/06_implementation/coding_challenges/challenge_06_fft_butterfly_rtl.sv):
  the fixed-point FFT butterfly this design's structure follows.

## Licence

MIT.
