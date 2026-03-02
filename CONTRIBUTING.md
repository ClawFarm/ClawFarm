# Contributing to ClawFarm

Thanks for your interest in contributing!

## Development Setup

See the [README](README.md#development) for how to run the backend and frontend locally.

### Docker Compose (dev build)

There are two compose files:

| File | Purpose |
|------|---------|
| `docker-compose.yml` | **Production** — pulls pre-built images from `ghcr.io` |
| `docker-compose.dev.yml` | **Development** — builds dashboard and frontend from source |

To build and run locally:

```bash
cp .env.example .env   # Edit with your API keys
docker compose -f docker-compose.dev.yml up --build -d

# View logs (admin password is printed on first run)
docker compose -f docker-compose.dev.yml logs dashboard | head -20
```

### Hot-reload (no Docker)

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -r dashboard/requirements.txt
cd dashboard && uvicorn app:app --host 0.0.0.0 --port 8080 --reload

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

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
