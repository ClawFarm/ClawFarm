FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as UID 1000 to match OpenClaw's node user and typical host user.
# This eliminates the need for chown/chmod hacks on bot directories.
RUN useradd -u 1000 -m botfarm
USER botfarm

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
