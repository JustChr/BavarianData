"""How a wallbox meter reading that went backwards is read.

An energy meter only counts up, so a lower reading is either a **glitch** or a
**reset**. Some integrations briefly report a stale value and then the real one
again (#197: a go-e meter fell back to the previous charge's end for a single
sample, 15625.427 -> 15611.155 -> 15625.461). A meter that was replaced or
power-cycled starts again from a low count and keeps climbing from there.

Treating every dip as a reset counted the glitch twice: the climb back to the
real reading was billed as fresh energy, and the session's measured figure was
thrown away. So a dip is *held*, and counts nothing, until the next reading
says which it was:

- back at or above the last trusted reading: a glitch, and counting goes on
  from the trusted reading as though the dip never happened;
- above the dip but still below the trusted reading: the meter is counting from
  its new, lower start -- a reset, measured from the dip;
- at or below the dip: still held.

Each caller keeps its own trusted reading and held dip; this module only judges
one reading against them. Home Assistant-free on purpose.
"""

from __future__ import annotations

from typing import Optional

# The reading is at or above the trusted one: count the difference.
ADVANCE = "advance"
# Below the trusted reading, and not (yet) a reset: count nothing, keep the dip.
HOLD = "hold"
# Climbing again below the trusted reading: the meter restarted from the dip.
RESET = "reset"


def judge(trusted: float, dip: Optional[float], reading: float) -> str:
    """Which of :data:`ADVANCE`, :data:`HOLD` or :data:`RESET` ``reading`` is."""

    if reading >= trusted:
        return ADVANCE
    if dip is not None and reading > dip:
        return RESET
    return HOLD
