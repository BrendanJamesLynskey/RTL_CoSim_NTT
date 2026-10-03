"""Functional coverage: named bins, sampled from each transaction, with a report.

Code coverage says which lines of RTL ran; functional coverage says which
*situations* the stimulus created. Each bin is a predicate over a transaction
(here, one butterfly: its operands, the modulus and what the bit-accurate model
says happened inside). A test campaign is done when every bin has been hit at
least ``goal`` times, or a bin is argued unreachable and excluded.

Small and dependency-free on purpose; cocotb-coverage and SystemVerilog
covergroups provide the same idea with crosses and richer bin syntax.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Coverage:
    name: str
    bins: dict[str, int] = field(default_factory=dict)
    goal: int = 1

    def define(self, *names: str) -> "Coverage":
        for n in names:
            self.bins.setdefault(n, 0)
        return self

    def hit(self, name: str, cond: bool = True) -> None:
        if name not in self.bins:
            raise KeyError(f"undefined coverage bin {name!r}")
        if cond:
            self.bins[name] += 1

    def holes(self) -> list[str]:
        return [n for n, c in self.bins.items() if c < self.goal]

    def percent(self) -> float:
        return 100.0 * (len(self.bins) - len(self.holes())) / len(self.bins)

    def to_json(self) -> str:
        return json.dumps({"name": self.name, "goal": self.goal, "bins": self.bins,
                           "percent": self.percent(), "holes": self.holes()}, indent=1)

    def report(self) -> str:
        lines = [f"{self.name}: {self.percent():.0f}% ({len(self.bins) - len(self.holes())}/{len(self.bins)} bins)"]
        for n, c in self.bins.items():
            lines.append(f"  {'HOLE ' if c < self.goal else '     '}{n:28} {c}")
        return "\n".join(lines)


BUTTERFLY_BINS = (
    "a_zero", "a_max", "b_zero", "b_max", "w_one", "w_max",
    "sum_wraps", "sum_no_wrap", "diff_borrows", "diff_no_borrow",
    "corr_0", "corr_1", "corr_2",
    "corr_2_x_sum_wraps", "corr_2_x_diff_borrows",
    "back_to_back", "after_gap",
)


def butterfly_coverage() -> Coverage:
    return Coverage("butterfly").define(*BUTTERFLY_BINS)


def sample_butterfly(cov: Coverage, a: int, b: int, w: int, q: int, t: int, corr: int, gap: int) -> None:
    """Sample one butterfly transaction; t = w*b mod q, corr = Barrett correction steps."""
    cov.hit("a_zero", a == 0)
    cov.hit("a_max", a == q - 1)
    cov.hit("b_zero", b == 0)
    cov.hit("b_max", b == q - 1)
    cov.hit("w_one", w == 1)
    cov.hit("w_max", w == q - 1)
    cov.hit("sum_wraps", a + t >= q)
    cov.hit("sum_no_wrap", a + t < q)
    cov.hit("diff_borrows", a < t)
    cov.hit("diff_no_borrow", a >= t)
    cov.hit(f"corr_{corr}")
    # Crosses. A wrong second correction leaves t off by q, which the add/subtract stage
    # hides unless the sum wraps or the difference borrows: only these crosses make a
    # bug in that step observable at the outputs.
    cov.hit("corr_2_x_sum_wraps", corr == 2 and a + t >= q)
    cov.hit("corr_2_x_diff_borrows", corr == 2 and a < t)
    cov.hit("back_to_back", gap == 0)
    cov.hit("after_gap", gap > 0)
