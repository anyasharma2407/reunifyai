# The synthetic corpus is generated at BUILD time, not at boot.
#
# Generation draws 160 faces and embeds them, which takes about twenty seconds.
# Doing that on every container start would make each deploy and each restart
# sit there not serving. Baking it into the image means the corpus is fixed,
# identical across replicas, and the container answers immediately.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engine/ ./engine/
COPY web/ ./web/
COPY scripts/ ./scripts/

# Same seed as the README, so a deployed instance and a local checkout hold
# the same synthetic people and the same faces.
RUN python -m engine.generate --seed 7 --size 80

EXPOSE 8000
CMD ["sh", "-c", "uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
