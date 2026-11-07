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

RUN useradd --create-home factory \
    && mkdir /workspace \
    && chown factory:factory /workspace
WORKDIR /opt/factory
COPY src/__init__.py /opt/factory/src/__init__.py
COPY src/worker /opt/factory/src/worker
# Root-owned managed policy cannot be overwritten by the worker or cloned repository.
COPY image/managed_config.toml /etc/codex/managed_config.toml
# Canonical factory skills are immutable to the worker; native discovery points to this bundle.
COPY image/skills/implement/ /opt/factory/skills/implement/
COPY image/skills/code-review/ /opt/factory/skills/code-review/
COPY image/skills/pr/ /opt/factory/skills/pr/
COPY image/skills/tdd/ /opt/factory/skills/tdd/
RUN chmod -R a=rX /opt/factory/skills \
    && mkdir -p /home/factory/.agents \
    && ln -s /opt/factory/skills /home/factory/.agents/skills
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WORKSPACE=/workspace AWS_REGION=us-east-1
USER factory
EXPOSE 8080
CMD ["python3.13", "-m", "src.worker.server"]
