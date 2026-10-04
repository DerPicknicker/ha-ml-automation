# AGENTS.md

Guidance for AI coding agents (and humans in a hurry) working on this
repository.

## What this is

ML Automation is a Home Assistant custom integration, distributed through HACS.
It watches one entity (the *source*), learns at which times it is usually
active, and switches other entities (the *targets*) on a bit before and off a
while after those times. One config entry is one learned pattern.

## Layout

```
custom_components/ml_automation/
  learner.py       The model. Pure Python, no Home Assistant imports.
  manager.py       One PatternManager per config entry: observe, learn, act.
  config_flow.py   Config flow (user → source → control → learning) and options flow.
  entity.py        Base entity; all entities follow the manager via a dispatcher signal.
  sensor.py, binary_sensor.py, switch.py, button.py, number.py
  diagnostics.py   What a bug report needs.
  translations/    en.json and de.json
tests/
  test_learner.py      Model only, no Home Assistant needed.
  test_manager.py      Behaviour inside Home Assistant.
  test_config_flow.py
  test_history.py      Recorder import. Needs `recorder_mock` before `hass`.
```

## Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest              # everything
.venv/bin/python -m pytest tests/test_learner.py -q   # model only, fast
```

CI (`.github/workflows/validate.yml`) runs the tests, hassfest and the HACS
validation. All three must pass.

## How the pieces fit

1. `manager.py` records every debounced on/off transition of the source as an
   event (`{"ts", "kind"}`) and keeps a set of *observed days*. Both are
   persisted with `helpers.storage.Store`.
2. Once a day (and on setup, Predict now, Re-learn) `learner.learn()` turns
   the events into a 5-minute occupancy grid per observed day, trains a random
   forest on (slot of day, weekday) → active, and reads *habits* off the weekly
   probability profile: every crossing of the confidence threshold is a
   switch-on or switch-off habit.
3. `upcoming_actions()` projects habits onto the calendar, shifted by the lead
   time (on) and the off delay (off). A tick every minute executes what is due.

## Rules that are easy to break

- **`learner.py` stays pure.** No `homeassistant` imports and no third-party
  packages. `manifest.json` has `"requirements": []` on purpose: anything that
  needs compiling (numpy, scikit-learn, torch) does not install reliably on
  Home Assistant OS. Training runs in the executor, never on the event loop.
- **Today is never part of the model.** Only complete days are trained on.
- **The integration must not learn from its own actions.** When a target we
  switched makes the source change, `_record_transition` records it at the
  learned time instead of the real one, and drops it if the user undoes the
  action. Otherwise the lead time would drag the habit earlier every day.
  Any change to how actions are executed needs a look at this.
- **Training must be deterministic.** The forest is seeded; tests rely on it.
- **Switch-off respects the guard.** `is_busy` postpones, it never skips.
- **Translations stay in sync.** Every key in `translations/en.json` must exist
  in `translations/de.json`. New config options need a label and a description
  in both, in both the `config` and the `options` section.
- **Options override data.** `manager.conf` is `{**entry.data, **entry.options}`;
  read settings from there with a default from `const.py`.

## Conventions

- Follow Home Assistant core style: `from __future__ import annotations`, type
  hints everywhere, `async_` prefix for coroutines and `@callback` functions
  that must run in the event loop, docstrings on public functions.
- Comments explain why, not what.
- New behaviour comes with a test. Tests set states with
  `hass.states.async_set`, mock `homeassistant.turn_on/off` with
  `async_mock_service`, and move time with the `move_to` helper in
  `tests/conftest.py`.
- Commit messages: imperative subject line, body explains the reason.
- Do not edit the `version` in `manifest.json` by hand; the Release workflow
  sets it.

## Releasing

Run the **Release** workflow on GitHub (Actions → Release → Run workflow) with
the new version number. It runs the tests, writes the version into
`manifest.json`, tags the commit, and publishes a GitHub release with generated
notes and a zip of the integration. HACS picks the release up from the tag.
