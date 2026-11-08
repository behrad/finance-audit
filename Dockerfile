FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=dotenv/dotenvx:v2.38.2 /usr/local/bin/dotenvx /usr/local/bin/dotenvx
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY skills ./skills
COPY fixtures ./fixtures
COPY .env ./.env
ENV PATH="/srv/.venv/bin:$PATH" PORT=8080
# .env is encrypted; the only runtime secret is DOTENV_PRIVATE_KEY (Cloud Run: Secret Manager -> env var)
ENTRYPOINT ["dotenvx", "run", "--"]
CMD ["sh", "-c", "uvicorn app.web:app --host 0.0.0.0 --port ${PORT}"]
