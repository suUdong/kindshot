FROM python:3.12-slim

WORKDIR /app

# Layer 1: dependencies (cached unless pyproject.toml changes)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . 2>/dev/null || true

# Layer 2: source code (rebuilds on code change, deps cached)
COPY src/ src/
COPY scripts/ scripts/
COPY deploy/ deploy/
RUN pip install --no-cache-dir -e .

ENV HEALTH_HOST=0.0.0.0
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["python", "-m", "kindshot", "--paper"]
