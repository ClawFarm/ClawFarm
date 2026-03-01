# Contributing to ClawFarm

Thanks for your interest in contributing!

## Development Setup

See the [README](README.md#development) for how to run the backend and frontend locally.

## Making Changes

1. Fork the repo and create a branch from `master`.
2. Make your changes.
3. Run the checks below before submitting a PR.

## Pre-PR Checklist

**Backend:**
```bash
ruff check dashboard/              # Lint (zero errors required)
cd dashboard && python -m pytest tests/test_fleet.py -v  # Tests
```

**Frontend:**
```bash
cd frontend && npm run lint         # ESLint
cd frontend && npm run build        # Type-check + build
```

All four checks run in CI on every PR.

## PR Guidelines

- Keep PRs focused — one feature or fix per PR.
- Add tests for new backend functionality in `dashboard/tests/test_fleet.py`.
- Update `CLAUDE.md` if you change project structure or architecture.

## Reporting Issues

Use the [issue templates](.github/ISSUE_TEMPLATE/) for bug reports and feature requests.
