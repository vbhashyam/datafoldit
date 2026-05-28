FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATAFOLDIT_HOST=0.0.0.0
ENV DATAFOLDIT_PORT=8765
ENV DATAFOLDIT_DB=/app/data/datafoldit.db
ENV DATAFOLDIT_UPLOAD_DIR=/app/data/attachments

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py README.md ./
COPY datafoldit ./datafoldit

RUN mkdir -p /app/data/attachments

VOLUME ["/app/data"]
EXPOSE 8765

CMD ["python3", "app.py"]
