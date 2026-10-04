# Contributing

Thanks for wanting to improve ML Automation. This page describes what a
contribution should look like so it can be reviewed and merged quickly.

## Reporting a problem or suggesting something

Use the issue forms; they ask for exactly what is needed.

- **Bug:** attach the diagnostics of the affected pattern (*Settings → Devices
  & services → ML Automation → ⋮ on the pattern → Download diagnostics*). It
  contains the settings, the learned habits and the model state.
- **Feature request:** describe the situation in your home first, the solution
  second. A concrete example with entities is worth more than a general idea.

Please search the existing issues before opening a new one.

## Development setup

```bash
git clone <your fork>
cd ha-ml-automation
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

Python 3.13 is what CI uses. To try a change in a real Home Assistant, copy or
symlink `custom_components/ml_automation` into the `custom_components` folder
of a development instance and restart it.

[AGENTS.md](AGENTS.md) explains how the code is organised and which rules are
easy to break. Read it before changing `learner.py` or `manager.py`.

## Standards

**Code**

- Home Assistant core style: type hints, `from __future__ import annotations`,
  `async_` prefix, docstrings on public functions.
- `learner.py` stays free of Home Assistant imports and of third-party
  packages. The integration has no requirements, and that is a feature.
- Nothing slow on the event loop.
- Comments say why, not what.

**Tests**

- Every change in behaviour has a test; every bug fix has a test that fails
  without the fix.
- `pytest` must pass. Tests must not depend on the real clock or on randomness.

**Translations**

- User-facing text lives in `translations/en.json` and `translations/de.json`.
  Both files must have the same keys. If you don't speak German, say so in the
  pull request and a maintainer will fill it in.

**Commits and pull requests**

- One topic per pull request. Small pull requests get reviewed faster.
- Commit subject in the imperative, up to about 70 characters ("Postpone
  switch-off while the guard is active"). Use the body for the reason.
- Describe what changed for the user and how you tested it.
- Don't change the version in `manifest.json`; releases do that.
- CI has to be green: tests, hassfest and HACS validation.

## Releases

Maintainers release by running the **Release** workflow with a version number.
Versions follow [Semantic Versioning](https://semver.org): breaking changes to
settings or entities bump the major version, new features the minor version,
fixes the patch version.

## License

By contributing you agree that your contribution is licensed under the
[MIT License](LICENSE).
