"""Build the RTL and run the cocotb testbenches (Verilator by default, Icarus as fallback).

Used by tests/test_rtl.py (pytest) and examples/results.py. Each (top level,
parameters, defines, simulator) gets its own build directory, so builds are
reused across campaigns that only change environment variables.
"""

from __future__ import annotations

import os
import sys
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RTL = ROOT / "rtl"
TB = ROOT / "tb"
BUILD = ROOT / "sim_build"
SOURCES = {
    "mod_mul_barrett": ["mod_mul_barrett.sv"],
    "ntt_butterfly": ["mod_mul_barrett.sv", "ntt_butterfly.sv"],
    "ntt_core": ["mod_mul_barrett.sv", "ntt_butterfly.sv", "ntt_core.sv"],
}
TESTS = {"ntt_butterfly": "test_butterfly", "ntt_core": "test_ntt_core"}


def simulator() -> str:
    return os.environ.get("SIM", "verilator")


def _runner(sim: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            from cocotb_tools.runner import get_runner     # cocotb >= 2.0
        except ImportError:
            from cocotb.runner import get_runner           # cocotb 1.9
    return get_runner(sim)


def build_dir(top: str, parameters: dict, defines: dict, sim: str) -> Path:
    tag = "_".join(f"{k}{v}" for k, v in sorted({**parameters, **defines}.items())) or "default"
    return BUILD / sim / f"{top}_{tag}"


def run(top: str, parameters: dict | None = None, defines: dict | None = None, env: dict | None = None,
        testcase: str | None = None, sim: str | None = None, results_xml: Path | None = None) -> tuple[int, int, Path]:
    """Build (if needed) and run a testbench. Returns (tests, failures, results xml)."""
    sim = sim or simulator()
    parameters, defines = parameters or {}, defines or {}
    bdir = build_dir(top, parameters, defines, sim)
    runner = _runner(sim)
    build_args = ["-Wno-fatal", "-O3"] if sim == "verilator" else []
    runner.build(verilog_sources=[RTL / s for s in SOURCES[top]], hdl_toplevel=top, parameters=parameters,
                 defines=defines, build_args=build_args, build_dir=bdir, timescale=("1ns", "1ps"))
    xml = results_xml or bdir / f"results_{testcase or 'all'}.xml"
    # the simulator embeds Python; the runner passes it sys.path, so add the testbenches
    for p in (str(ROOT / "src"), str(TB)):
        if p not in sys.path:
            sys.path.insert(0, p)
    if sys.prefix != sys.base_prefix:
        os.environ["VIRTUAL_ENV"] = sys.prefix
    # cocotb 1.9 refuses an explicit results_xml inside pytest; hide pytest's marker while it runs
    current = os.environ.pop("PYTEST_CURRENT_TEST", None)
    try:
        runner.test(hdl_toplevel=top, test_module=TESTS[top], testcase=testcase, build_dir=bdir, test_dir=bdir,
                    extra_env=env or {}, results_xml=str(xml), timescale=("1ns", "1ps"))
    finally:
        if current is not None:
            os.environ["PYTEST_CURRENT_TEST"] = current
    return (*count(xml), xml)


def count(xml: Path) -> tuple[int, int]:
    root = ET.parse(xml).getroot()
    cases = root.iter("testcase")
    n = fails = 0
    for c in cases:
        n += 1
        if c.find("failure") is not None or c.find("error") is not None:
            fails += 1
    return n, fails
