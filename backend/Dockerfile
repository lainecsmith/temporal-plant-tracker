FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies into the system Python (no virtualenv needed in a container)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY activities/ ./activities/
COPY api/ ./api/
COPY models/ ./models/
COPY workflows/ ./workflows/
COPY worker.py ./

# Default command: run the FastAPI server
# Override with `command: python worker.py` in docker-compose for the worker service
CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
