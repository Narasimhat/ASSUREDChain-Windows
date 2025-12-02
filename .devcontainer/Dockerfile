FROM mcr.microsoft.com/devcontainers/python:3.11-bullseye

# Install additional system dependencies as needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Pre-install project requirements for faster container start-up
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt
