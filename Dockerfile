FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Mount a persistent volume here in production so the SQLite DB (and the
# FIFO queue / assignment history it holds) survives restarts and redeploys.
RUN mkdir -p /app/data
ENV DATABASE_URL=sqlite:////app/data/allocation.db

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
