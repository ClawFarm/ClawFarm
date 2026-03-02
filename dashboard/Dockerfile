FROM python:3.12.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

# gosu for dropping privileges after entrypoint setup
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*

COPY dashboard/ .

# Create app user (UID 1000 to match OpenClaw's node user and typical host user).
# This eliminates the need for chown/chmod hacks on bot directories.
RUN useradd -u 1000 -m botfarm

# Entrypoint runs as root to auto-detect Docker socket GID,
# then drops to botfarm user via gosu.
ENTRYPOINT ["/app/entrypoint.sh"]

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
