# One image serves both configurations; ENCRYPTION_MODE selects the mode at startup.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv

COPY requirements.lock.txt .
RUN pip install --require-hashes --no-deps -r requirements.lock.txt

COPY app ./app
RUN useradd --system --uid 10001 --no-create-home api
USER api

EXPOSE 8443
HEALTHCHECK --interval=5s --timeout=3s --retries=10 CMD ["python", "-c", "import ssl,urllib.request as u; u.urlopen('https://localhost:8443/healthz', context=ssl.create_default_context(cafile='/certs/ca.pem'), timeout=2)"]
CMD ["python", "-m", "app.server"]
