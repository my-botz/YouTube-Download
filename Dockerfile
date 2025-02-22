FROM python:3.10-slim

RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    gunicorn \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD gunicorn --bind 0.0.0.0:$PORT healthcheck:app & python main.py
