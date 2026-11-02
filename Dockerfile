# syntax=docker/dockerfile:1

FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.5 /uv /usr/local/bin/uv

WORKDIR /app

# Install the locked runtime dependencies using the base image's Python.
ENV UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache --python /usr/local/bin/python3

# Копируем остальные файлы проекта
COPY . .


# Команда запуска приложения
CMD ["python3", "app.py"]
