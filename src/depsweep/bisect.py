"""Bisection: find the one upgrade that broke the gate, without re-running
the gate once per package.

The batch as a whole is known to fail (that is how we got here). ``narrow``
probes halves of the candidate list; a failing half becomes the new suspect
set, a passing half clears its members. Each probe at least halves the set,
so a single culprit is pinned in ceil(log2(N)) + 1 probes at the worst. When
no single package fails alone (an interaction, or a gate that was already
red), ``narrow`` returns None and the caller reverts the whole batch.
"""

from __future__ import annotations

from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


def narrow(
    candidates: Sequence[T],
    budget: int,
    probe: Callable[[Sequence[T]], bool],
) -> T | None:
    """Return the candidate that breaks the gate on its own, or None.

    ``probe(subset)`` applies exactly ``subset`` to a clean tree and runs the
    gate; True means the gate failed. At most ``budget`` probes are spent;
    running out means None (could not isolate), never an exception.
    """
    suspects = list(candidates)
    runs = 0
    while len(suspects) > 1:
        if runs >= budget:
            return None
        cut = max(1, len(suspects) // 2)
        half, rest = suspects[:cut], suspects[cut:]
        runs += 1
        if probe(half):
            if len(half) == 1:
                return half[0]  # this single package failed on its own
            suspects = half
        else:
            suspects = rest  # the breakage lives in the other half
    if runs >= budget:
        return None
    # One suspect left, but it has only been seen inside a passing-free
    # group: it has never been acquitted, and never convicted alone either.
    return suspects[0] if probe(suspects) else None
