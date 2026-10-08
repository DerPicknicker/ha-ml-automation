"""Measure the pure action learner with bounded distributed and burst history.

Run with Python 3.12+; no Home Assistant installation is needed. Allocation
tracing measures temporary Python allocations, excluding the input history,
interpreter and Home Assistant. Timing is measured without allocation tracing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import sys
from time import perf_counter
import tracemalloc


def main() -> None:
    """Compare ordinary history with densely packed service action bursts."""
    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components/ml_automation/action_learner.py"
    )
    spec = importlib.util.spec_from_file_location("action_benchmark", path)
    assert spec is not None and spec.loader is not None
    learner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = learner
    spec.loader.exec_module(learner)
    today = datetime(2026, 10, 8, tzinfo=UTC)
    for name, span in (("distributed", 28), ("burst", 3)):
        events = []
        for index in range(learner.MAX_OBSERVATIONS):
            day = index % span + 1
            seconds = (index // span) * (120 if span == 28 else 3)
            when = (today - timedelta(days=day)).replace(hour=6) + timedelta(
                seconds=seconds
            )
            action = f"action_{index % learner.MAX_ACTIONS}"
            events.append(learner.Observation(when, f"action:{action}", action))
        days = {(today - timedelta(days=index)).date() for index in range(1, span + 1)}
        started = perf_counter()
        rules = learner.learn_actions(events, days, today=today.date())
        elapsed = perf_counter() - started
        tracemalloc.start()
        learner.learn_actions(events, days, today=today.date())
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(
            f"{name}: {len(events)} observations, {len(rules)} rules, "
            f"{elapsed:.3f}s, {peak / 1024**2:.2f} MiB peak temporary allocations"
        )


if __name__ == "__main__":
    main()
