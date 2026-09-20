# Playwright's base image pins a matching browser build — never "playwright install" at runtime.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

WORKDIR /srv

COPY services/automation/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY services/automation/app ./app
COPY config /config
COPY schemas /schemas

# Non-root, and the artifact dir is the only writable path.
RUN useradd -r -u 10001 maya && mkdir -p /var/maya/artifacts && chown -R maya /var/maya
USER maya

ENV MAYA_SOURCES_DIR=/config/sources \
    MAYA_FRESHNESS_CONFIG=/config/freshness.yaml \
    MAYA_ARTIFACT_DIR=/var/maya/artifacts

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
