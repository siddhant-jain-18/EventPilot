# WorkshopPilot demo image: three uAgents + the RSVP web service.
# Build:  docker build -t workshoppilot .
# Run:    docker run --rm --env-file .env -p 8104:8104 workshoppilot
#
# The RSVP service must be reachable by attendees, so map its port (or set
# RSVP_PORT to the platform's PORT). Agents can reach each other over
# localhost inside the container; the coordinator talks to ASI:One through the
# Agentverse mailbox when AGENT_MAILBOX=true.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

# Do not run the application as root. The database directory is created at
# runtime and must be mounted writable when a persistent volume is attached.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# Demo inventory is seeded idempotently on every start.
CMD ["sh", "-c", "python -m scripts.seed_demo_data && python -m scripts.run_local"]

EXPOSE 8101 8102 8103 8104
