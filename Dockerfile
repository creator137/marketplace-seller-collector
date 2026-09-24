FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright is used only by bootstrap_sessions to obtain a user session;
# marketplace crawling remains curl_cffi-based.  Use Debian Chromium so the
# image does not depend on the Playwright CDN at build time.
RUN playwright install-deps chromium && \
    apt-get update && apt-get install -y --no-install-recommends chromium && \
    rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium

COPY . .

RUN python manage.py collectstatic --noinput || true

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
