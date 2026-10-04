# AGENTS.md

Guidance for AI coding agents (and humans in a hurry) working on this
repository.

## What this is

ML Automation is a Home Assistant custom integration, distributed through HACS.
The user picks an entity to control and one or more entities to learn from.
The integration records the learning entities, works out by itself what "in
use" looks like, learns at which times that usually happens, and switches the
controlled entity on a bit before and off a while after those times. One
config entry is one learned pattern.

The product goal is **zero configuration**: no thresholds, no state lists, no
optional fields in setup. If a change would add a required decision to the
setup flow, find a way to learn or default it instead.

## Layout

```
custom_components/ml_automation/
  learner.py       The model. Pure Python, no Home Assistant imports.
  manager.py       One PatternManager per config entry: observe, learn, act.
  config_flow.py   Config flow (what to control → what to learn from) and options flow.
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

1. `manager.py` samples every learning entity once a minute and stores the
   mean per 5-minute slot (`_days[iso_day][entity_id][slot]`). Numeric states
   are stored as they are, everything else as 1/0 (see `INACTIVE_STATES`).
   Data is persisted with `helpers.storage.Store`; new learning entities are
   seeded from the recorder.
2. Once a day (and on setup, Predict now, Re-learn) `learner.learn()`
   - derives a threshold per numeric entity from its values (`learn_threshold`,
     Otsu's method),
   - labels every slot *in use* if any **label entity** is active,
   - trains a random forest on (slot of day, weekday) → in use,
   - reads *habits* off the weekly probability profile: every crossing of the
     confidence threshold is a switch-on or switch-off habit.
3. `upcoming_actions()` projects habits onto the calendar, shifted by the lead
   time (on) and the off delay (off). The tick every minute executes what is
   due.

With the automation switch off (or one direction disabled) nothing is switched;
`manager.suggestion` then reports the switching the pattern calls for, the
*Apply suggestion* button does it, and `ml_automation_suggestion` is fired once
per new suggestion.

The status sensor has to explain itself: when nothing was learned it says
whether data, activity or regularity is missing. Keep that true when adding
reasons for the model to come up empty.

Three entity roles, all derived from the two config fields:

- **learn entities**: everything that is recorded.
- **evidence entities**: learn entities other than the controlled one. If any
  is active, the controlled entity is *in use* and is not switched off.
- **label entities**: what the model learns the timing of. The evidence
  entities, or the controlled entity itself if there are none.

## Rules that are easy to break

- **`learner.py` stays pure.** No `homeassistant` imports and no third-party
  packages. `manifest.json` has `"requirements": []` on purpose: anything that
  needs compiling (numpy, scikit-learn, torch) does not install reliably on
  Home Assistant OS. Training runs in the executor, never on the event loop.
- **Today is never part of the model.** Only complete days are trained on.
- **The integration must not learn from its own actions.** We switch on before
  the learned time and off after it. `_discount_own_action` marks the slots in
  between as ignored so they are never labelled *in use*; otherwise the habit
  would creep earlier (or later) every day. Any change to how actions are
  executed needs a look at this.
- **Unknown is not idle.** Slots without data are `None` and are left out of
  training. Don't fill gaps with zeros.
- **The controlled entity is not evidence** when other learning entities
  exist. Its state reflects our own switching.
- **Training must be deterministic.** The forest is seeded; tests rely on it.
- **Switch-off respects use.** `in_use` postpones, it never skips.
- **Translations stay in sync.** Every key in `translations/en.json` must exist
  in `translations/de.json`. New config options need a label and a description
  in both, in both the `config` and the `options` section.
- **Existing entries must keep working.** A change to the shape of the config
  entry needs a version bump and a step in `async_migrate_entry`.
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
