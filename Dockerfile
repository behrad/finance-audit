FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY skills ./skills
COPY fixtures ./fixtures
ENV PATH="/srv/.venv/bin:$PATH" PORT=8080
CMD ["sh", "-c", "uvicorn app.web:app --host 0.0.0.0 --port ${PORT}"]
