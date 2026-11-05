FROM python:3.12-slim

# Tesseract handles OCR for scanned invoices when no LLM key is set (and for the cross-check when one is).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
# One worker on purpose: runs execute in background threads and share one SQLite file.
CMD gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:${PORT} app.main:app
