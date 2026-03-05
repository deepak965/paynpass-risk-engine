FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# $PORT is injected by Railway / Render / DigitalOcean at runtime.
# Falls back to 8000 for local Docker use.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]