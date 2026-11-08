FROM public.ecr.aws/lambda/microvms:al2023-minimal

# Tool version defaults are build-time only, not runtime environment variables.
ARG CODEX_VERSION=0.166.0
ARG DOCKER_VERSION=29.9.3
ARG COMPOSE_VERSION=5.7.0

# Install runtime tools through Amazon Linux and signed GitHub repositories; keep the OS interpreter intact.
RUN curl -fsSL https://cli.github.com/packages/rpm/gh-cli.repo -o /etc/yum.repos.d/gh-cli.repo \
    && rpm --import https://cli.github.com/packages/githubcli-archive-keyring.asc \
    && dnf install -y python3.13 python3.13-pip git shadow-utils nodejs22 nodejs22-npm awscli-2-2.38.0 gh-2.103.3 \
        iptables-nft-1.8.8-3.amzn2023.0.2 iproute-6.10.0-319.amzn2023.0.1 util-linux-2.37.4-1.amzn2023.0.7 tar-1.34-1.amzn2023.0.4 gzip-1.12-1.amzn2023.0.1 \
    && dnf clean all \
    && ln -s /usr/bin/python3.13 /usr/local/bin/python3
# Install only version-pinned runtime SDK dependencies for the selected interpreter.
COPY image/requirements.txt /opt/factory/image/requirements.txt
RUN python3.13 -m pip install --no-cache-dir -r /opt/factory/image/requirements.txt
# npm supplies the ARM64 bundle; expose its required search and sandbox helpers.
RUN npm install --prefix /opt/codex --no-audit --no-fund "@openai/codex@${CODEX_VERSION}" \
    && vendor=/opt/codex/node_modules/@openai/codex-linux-arm64/vendor/aarch64-unknown-linux-musl \
    && ln -s "$vendor/codex-path/rg" /usr/local/bin/rg \
    && ln -s "$vendor/codex-resources/bwrap" /usr/local/bin/bwrap \
    && npm cache clean --force
ENV PATH=/opt/codex/node_modules/.bin:$PATH

# Install versioned ARM64 Engine/CLI and Compose; the lifecycle server starts the daemon only after resume.
RUN curl -fsSL "https://download.docker.com/linux/static/stable/aarch64/docker-${DOCKER_VERSION}.tgz" -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 \
    && rm /tmp/docker.tgz \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v${COMPOSE_VERSION}/docker-compose-linux-aarch64" -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod 755 /usr/local/lib/docker/cli-plugins/docker-compose \
    && docker --version \
    && docker compose version
RUN useradd --create-home factory \
    && groupadd docker \
    && usermod -aG docker factory \
    && mkdir /workspace \
    && chown factory:factory /workspace
WORKDIR /opt/factory
COPY src/__init__.py /opt/factory/src/__init__.py
COPY src/worker /opt/factory/src/worker
# Managed policy is root-owned; Docker socket access gives factory root-equivalent control inside the VM.
COPY image/managed_config.toml /etc/codex/managed_config.toml
# Canonical factory skills are root-owned; native discovery points to this bundle.
COPY image/skills/ /opt/factory/skills/
RUN chmod -R a=rX /opt/factory/skills \
    && mkdir -p /home/factory/.agents \
    && ln -s /opt/factory/skills /home/factory/.agents/skills
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WORKSPACE=/workspace HOME=/home/factory
# Only the lifecycle supervisor/daemon run as root; worker and validation subprocesses drop to factory.
USER root
EXPOSE 8080
CMD ["python3.13", "-m", "src.worker.server"]
