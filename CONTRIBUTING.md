# Contributing to Cohort

Thanks for your interest in contributing to Cohort. This guide covers the basics.

## Development Setup

```bash
git clone https://github.com/cohort-dev/cohort.git
cd cohort
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest                    # full suite
pytest tests/test_foo.py  # single file
ruff check cohort/ tests/ # lint
mypy cohort/              # type check
```

All three checks (pytest, ruff, mypy) must pass before submitting a PR.

## Submitting Changes

1. Fork the repo and create a branch from `main`
2. Make your changes -- keep PRs focused on one thing
3. Add or update tests for any new behavior
4. Run the full test suite locally
5. Open a pull request with a clear description of what and why

## Code Style

- **Ruff** handles formatting and linting -- run `ruff check --fix` before committing
- **Type hints** on all public functions
- **No new dependencies** for the core library (`cohort/`). The zero-dep core is a design constraint, not an accident. Optional extras (`[server]`, `[claude]`) can add dependencies.

## What to Work On

- Issues labeled `good first issue` are a good starting point
- Bug reports with reproduction steps are always welcome
- Feature proposals -- open an issue first to discuss before writing code

## Reporting Bugs

Open an issue with:
- Python version and OS
- Minimal reproduction steps
- Expected vs actual behavior
- Full traceback if applicable

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
