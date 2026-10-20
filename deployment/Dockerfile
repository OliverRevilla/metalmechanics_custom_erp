# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.14.8-slim-trixie@sha256:cfe2e24a75302a15934d37c2d86412893c0aa934dc3a97cbb439d04c01890ca9
ARG NODE_IMAGE=node:24-trixie-slim@sha256:27526714fd6b7dc938f8cb1665bf1fe749efadadf0145d5bcc07f0fbd0370b60
FROM ${NODE_IMAGE} AS node
FROM ${PYTHON_IMAGE} AS base
ARG DEBIAN_SNAPSHOT=20261019T000000Z
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN printf 'Types: deb\nURIs: https://snapshot.debian.org/archive/debian/%s/\nSuites: trixie trixie-updates\nComponents: main\nCheck-Valid-Until: no\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: https://snapshot.debian.org/archive/debian-security/%s/\nSuites: trixie-security\nComponents: main\nCheck-Valid-Until: no\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' "$DEBIAN_SNAPSHOT" "$DEBIAN_SNAPSHOT" > /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install --no-install-recommends -y ca-certificates git curl nginx gettext-base mariadb-client libmariadb3 libpq5 libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 libharfbuzz0b libffi8 libjpeg62-turbo libtiff6 libwebp7 libldap2 libsasl2-2 fonts-dejavu-core fonts-liberation jq redis-tools tzdata-legacy \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -u 1000 -g 0 -d /home/frappe -ms /bin/bash frappe \
 && mkdir -p /etc/nginx/snippets /templates/nginx /home/frappe/frappe-bench /opt/metalmecanica/scripts \
 && rm -f /etc/nginx/sites-enabled/default \
 && sed -i '/^user /d' /etc/nginx/nginx.conf \
 && touch /run/nginx.pid \
 && ln -sf /dev/stdout /var/log/nginx/access.log \
 && ln -sf /dev/stderr /var/log/nginx/error.log \
 && chown -R frappe:0 /home/frappe /etc/nginx /var/log/nginx /var/lib/nginx /run/nginx.pid
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm install --global yarn@1.22.22
COPY vendor/frappe_docker/resources/core/nginx/nginx-template.conf /templates/nginx/frappe.conf.template
COPY vendor/frappe_docker/resources/core/nginx/security_headers.conf /etc/nginx/snippets/security_headers.conf
COPY --chmod=755 vendor/frappe_docker/resources/core/nginx/nginx-entrypoint.sh /usr/local/bin/nginx-entrypoint.sh
COPY --chmod=755 vendor/frappe_docker/resources/core/main-entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chmod=755 vendor/frappe_docker/resources/core/start.sh /usr/local/bin/start.sh
ENV PATH=/opt/tools/bin:/home/frappe/frappe-bench/env/bin:/usr/local/bin:/usr/bin:/bin

FROM base AS resolver
RUN python -m venv /opt/resolver \
 && /opt/resolver/bin/pip install --no-cache-dir uv==0.11.33
COPY vendor/frappe/pyproject.toml /inputs/frappe/pyproject.toml
COPY vendor/erpnext/pyproject.toml /inputs/erpnext/pyproject.toml
COPY deployment/lock-inputs/ /inputs/
RUN mkdir -p /locks \
 && /opt/resolver/bin/uv pip compile --python-version 3.14 --python-platform x86_64-unknown-linux-gnu --generate-hashes /inputs/tools.in -o /locks/tools.txt \
 && /opt/resolver/bin/uv pip compile --python-version 3.14 --python-platform x86_64-unknown-linux-gnu /inputs/frappe/pyproject.toml /inputs/erpnext/pyproject.toml /inputs/runtime.in -o /locks/runtime.txt

FROM scratch AS locks
COPY --from=resolver /locks/ /

FROM base AS builder
RUN apt-get update \
 && apt-get install --no-install-recommends -y build-essential pkg-config libmariadb-dev libpq-dev libffi-dev libldap2-dev libsasl2-dev libjpeg-dev zlib1g-dev libtiff-dev libwebp-dev liblcms2-dev \
 && rm -rf /var/lib/apt/lists/*
COPY deployment/locks/ /opt/locks/
RUN python -m venv /opt/tools \
 && /opt/tools/bin/pip install --no-cache-dir --require-hashes -r /opt/locks/tools.txt
WORKDIR /home/frappe/frappe-bench
RUN mkdir -p apps sites config logs config/pids \
 && python -m venv env \
 && chown -R frappe:0 /home/frappe/frappe-bench
COPY --chown=frappe:0 vendor/frappe/ apps/frappe/
COPY --chown=frappe:0 vendor/erpnext/ apps/erpnext/
COPY --chown=frappe:0 apps/metalmecanica/ apps/metalmecanica/
USER frappe
RUN uv pip install --python env/bin/python flit_core==3.12.0 setuptools==84.2.0 wheel==0.48.0 pip==26.2.1 \
 && uv pip install --python env/bin/python --no-cache --no-build-isolation -r /opt/locks/runtime.txt \
 && uv pip install --python env/bin/python --no-deps --no-build-isolation -e apps/frappe -e apps/erpnext -e apps/metalmecanica \
 && printf 'frappe\nerpnext\nmetalmecanica\n' > sites/apps.txt \
 && printf '{}\n' > sites/common_site_config.json
RUN yarn --cwd apps/frappe install --frozen-lockfile --network-timeout 120000 \
 && yarn --cwd apps/erpnext install --frozen-lockfile --ignore-scripts --network-timeout 120000 \
 && yarn --cwd apps/erpnext/banking install --frozen-lockfile --network-timeout 120000 \
 && bench build --production --force \
 && mv sites/assets assets
RUN env/bin/python -m pip freeze --all > installed-runtime.txt
RUN uv pip check --python env/bin/python

FROM base AS runtime
RUN apt-get update \
 && apt-get install --no-install-recommends -y chromium-headless-shell file gpg \
 && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/tools/ /opt/tools/
COPY --from=builder /opt/locks/ /opt/locks/
COPY --from=builder /home/frappe/frappe-bench/installed-runtime.txt /opt/locks/installed-runtime.txt
COPY --from=builder --chown=frappe:0 /home/frappe/frappe-bench/ /home/frappe/frappe-bench/
COPY --chown=frappe:0 deployment/scripts/ /opt/metalmecanica/scripts/
COPY tests/ /opt/metalmecanica/tests/
COPY docs/architecture/sources.lock.json /opt/metalmecanica/sources.lock.json
COPY vendor/frappe/LICENSE /opt/metalmecanica/licenses/frappe-MIT.txt
COPY vendor/erpnext/license.txt /opt/metalmecanica/licenses/erpnext-GPL.txt
COPY vendor/frappe_docker/LICENSE /opt/metalmecanica/licenses/frappe_docker-MIT.txt
LABEL org.opencontainers.image.title="Metalmecanica ERP" \
 org.opencontainers.image.version="0.0.1" \
 com.metalmecanica.frappe.revision="4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a" \
 com.metalmecanica.erpnext.revision="5af6561f5aa48104559692fb4fd82f2580e8d338"
ENV PATH=/opt/tools/bin:/home/frappe/frappe-bench/env/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin
WORKDIR /home/frappe/frappe-bench
USER frappe
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["start.sh"]
