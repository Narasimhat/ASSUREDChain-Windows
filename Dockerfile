FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    ASSISTANT_API_URL=http://127.0.0.1:8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ice.txt /tmp/

# Default runtime deps for Streamlit Cloud / Railway. ICE CLI is optional.
RUN pip install --upgrade pip \
    && pip install -r /tmp/requirements.txt \
    && pip install -r /tmp/requirements-ice.txt \
    && pip install "synthego-ice<=1.2.0" --no-deps || true

COPY . /app

RUN chmod +x scripts/start_railway.sh \
    && mkdir -p /app/data

EXPOSE 8503 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8503}/_stcore/health" || exit 1

CMD ["./scripts/start_railway.sh"]
