FROM public.ecr.aws/lambda/microvms:al2023-minimal

# Install pinned CLI packages through the Amazon Linux and signed GitHub repositories.
RUN curl -fsSL https://cli.github.com/packages/rpm/gh-cli.repo -o /etc/yum.repos.d/gh-cli.repo \
    && rpm --import https://cli.github.com/packages/githubcli-archive-keyring.asc \
    && dnf install -y python3 git shadow-utils nodejs22 nodejs22-npm awscli-2-2.38.0 gh-2.103.3 \
    && dnf clean all
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
COPY worker /opt/factory/worker
COPY image/acceptance.py /opt/factory/image/acceptance.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WORKSPACE=/workspace AWS_REGION=us-east-1
USER factory
EXPOSE 8080
CMD ["python3", "-m", "worker.server"]
