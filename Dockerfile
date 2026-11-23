# syntax=docker/dockerfile:1

FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.5 /uv /usr/local/bin/uv

WORKDIR /app

# curl installs acme.sh; the panel schedules container renewals.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install the locked runtime dependencies using the base image's Python.
ENV PANEL_DUCKDNS_STATE_DIR=/app/data/duckdns \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache --python /usr/local/bin/python3

# Копируем остальные файлы проекта
COPY . .


# Команда запуска приложения
CMD ["python3", "app.py"]
