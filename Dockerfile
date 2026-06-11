FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fonts-dejavu-core \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-upload-scheduler.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt -r requirements-upload-scheduler.txt

COPY . .

CMD ["python", "runner.py"]
