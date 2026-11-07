FROM public.ecr.aws/lambda/microvms:al2023-minimal

# Install pinned CLI packages through the Amazon Linux and signed GitHub repositories.
RUN curl -fsSL https://cli.github.com/packages/rpm/gh-cli.repo -o /etc/yum.repos.d/gh-cli.repo \
    && rpm --import https://cli.github.com/packages/githubcli-archive-keyring.asc \
    && dnf install -y python3.13 python3.13-pip git shadow-utils nodejs22 nodejs22-npm awscli-2-2.38.0 gh-2.103.3 \
    && dnf clean all
# Repository python3 commands use the selected runtime; leave the OS interpreter intact.
RUN ln -s /usr/bin/python3.13 /usr/local/bin/python3
# Install only version-pinned runtime SDK dependencies for the selected interpreter.
COPY image/requirements.txt /opt/factory/image/requirements.txt
RUN python3.13 -m pip install --no-cache-dir -r /opt/factory/image/requirements.txt
# npm supplies the ARM64 bundle; expose its required search and sandbox helpers.
RUN npm install --prefix /opt/codex --no-audit --no-fund @openai/codex@0.166.0 \
    && vendor=/opt/codex/node_modules/@openai/codex-linux-arm64/vendor/aarch64-unknown-linux-musl \
    && ln -s "$vendor/codex-path/rg" /usr/local/bin/rg \
    && ln -s "$vendor/codex-resources/bwrap" /usr/local/bin/bwrap \
    && npm cache clean --force
ENV PATH=/opt/codex/node_modules/.bin:$PATH

# Install versioned ARM64 Engine/CLI and Compose; the lifecycle server starts the daemon only after resume.
RUN dnf install -y iptables-nft-1.8.8-3.amzn2023.0.2 iproute-6.10.0-319.amzn2023.0.1 util-linux-2.37.4-1.amzn2023.0.7 tar-1.34-1.amzn2023.0.4 gzip-1.12-1.amzn2023.0.1 \
    && dnf clean all \
    && curl -fsSL https://download.docker.com/linux/static/stable/aarch64/docker-29.9.3.tgz -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 \
    && rm /tmp/docker.tgz \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL https://github.com/docker/compose/releases/download/v5.7.0/docker-compose-linux-aarch64 -o /usr/local/lib/docker/cli-plugins/docker-compose \
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
COPY image/skills/implement/ /opt/factory/skills/implement/
COPY image/skills/code-review/ /opt/factory/skills/code-review/
COPY image/skills/pr/ /opt/factory/skills/pr/
COPY image/skills/tdd/ /opt/factory/skills/tdd/
RUN chmod -R a=rX /opt/factory/skills \
    && mkdir -p /home/factory/.agents \
    && ln -s /opt/factory/skills /home/factory/.agents/skills
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WORKSPACE=/workspace AWS_REGION=us-east-1 HOME=/home/factory
# Only the lifecycle supervisor/daemon run as root; worker and validation subprocesses drop to factory.
USER root
EXPOSE 8080
CMD ["python3.13", "-m", "src.worker.server"]
