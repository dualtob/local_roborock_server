# syntax=docker/dockerfile:1

# Empty unless the build is given a local Web App zip with
# --build-context web_app_zip=<folder holding localrock-web.zip>
FROM scratch AS web_app_zip

FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/python-roborock/local_roborock_server" \
  org.opencontainers.image.description="Private Roborock HTTPS and MQTT stack for local LAN use" \
  org.opencontainers.image.licenses="MIT"

RUN apt-get update \
  && apt-get upgrade -y \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    coturn \
    mosquitto \
    openssl \
  && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/acme.sh \
  && curl -fsSL https://github.com/acmesh-official/acme.sh/archive/refs/tags/3.1.6.tar.gz \
  | tar -xz --strip-components=1 -C /opt/acme.sh \
  && chmod +x /opt/acme.sh/acme.sh \
  && ln -sf /opt/acme.sh/acme.sh /usr/local/bin/acme.sh

# The LocalRock Web App, served at / next to MQTT over WebSockets at /mqtt.
# WEB_APP_VERSION is the dualtob/LocalRock release tag (web-v*). Set
# WEB_APP_SHA256 to fail the build if the downloaded zip ever changes.
ARG WEB_APP_VERSION=web-v0.1.0
ARG WEB_APP_SHA256=
RUN --mount=type=bind,from=web_app_zip,target=/tmp/web_app_zip \
  set -eu \
  && zip=/tmp/localrock-web.zip \
  && if [ -f /tmp/web_app_zip/localrock-web.zip ]; then \
       echo "Using the local Web App zip (WEB_APP_VERSION=${WEB_APP_VERSION})"; \
       cp /tmp/web_app_zip/localrock-web.zip "$zip"; \
     else \
       curl -fsSL -o "$zip" \
         "https://github.com/dualtob/LocalRock/releases/download/${WEB_APP_VERSION}/localrock-web.zip"; \
     fi \
  && if [ -n "$WEB_APP_SHA256" ]; then echo "$WEB_APP_SHA256  $zip" | sha256sum -c -; fi \
  && mkdir -p /opt/web_app \
  && python -m zipfile -e "$zip" /opt/web_app \
  && rm "$zip" \
  && test -f /opt/web_app/index.html \
  && echo "$WEB_APP_VERSION" > /opt/web_app.version

LABEL io.github.dualtob.localrock.web-app.version="${WEB_APP_VERSION}"

# Defaults for keys config.toml leaves out; a key set in config.toml wins.
ENV ROBOROCK_SERVER_WEB_APP_FOLDER=/opt/web_app \
  ROBOROCK_SERVER_MQTT_WEBSOCKET_ENABLED=true

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir /app

EXPOSE 555 8881 3478/udp

CMD ["python", "-m", "roborock_local_server.container_entrypoint"]
