# Releasing ClawFarm

## Version Scheme

[Semantic versioning](https://semver.org/): `vMAJOR.MINOR.PATCH`

- **Patch** (v1.0.1) — bug fixes, dependency updates, docs
- **Minor** (v1.1.0) — new features, new templates, UI additions
- **Major** (v2.0.0) — breaking changes requiring user action (config format changes, env var renames, migration steps)

## How to Release

```bash
# 1. Make sure master is clean and CI passes
git checkout master
git pull

# 2. Tag the release
git tag v1.0.0        # use the appropriate version
git push --tags

# 3. Done — CI handles the rest
```

The [release workflow](.github/workflows/release.yml) automatically:

1. Runs the full CI suite (lint + tests + frontend build)
2. Builds multi-arch Docker images (amd64 + arm64) for `dashboard` and `frontend`
3. Pushes images to GHCR with tags: `v1.0.0`, `v1.0`, `v1`, `latest`
4. Creates a GitHub Release with auto-generated changelog

## Docker Images

Published to GitHub Container Registry:

| Image | Description |
|-------|-------------|
| `ghcr.io/clawfarm/clawfarm-dashboard` | FastAPI backend |
| `ghcr.io/clawfarm/clawfarm-frontend` | Next.js frontend |

Tag examples for a `v1.2.3` release:

| Tag | Moves on | Use case |
|-----|----------|----------|
| `v1.2.3` | Never | Pinned / reproducible deployments |
| `v1.2` | Patch releases | "Latest patch for v1.2" |
| `v1` | Minor releases | "Latest v1.x" |
| `latest` | Any release | Quick start default |

## Compose Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Production — pulls pre-built images from GHCR |
| `docker-compose.dev.yml` | Development — builds from local source |

Users run `docker compose up -d`. Contributors building from source run `docker compose -f docker-compose.dev.yml up --build -d`.

## Updating Users

Existing users upgrade by pulling new images:

```bash
docker compose pull
docker compose up -d
```

If a release requires migration steps, document them in the GitHub Release notes.
