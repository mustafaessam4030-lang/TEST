# Official Playwright image: Chromium + system dependencies, no extra browser tweaks.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config

# Secrets are injected at runtime as environment variables (Key Vault / secret store).
USER pwuser
ENTRYPOINT ["python", "-m", "app.collect"]
CMD ["--skip-if-succeeded-today"]
