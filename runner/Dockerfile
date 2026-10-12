FROM ubuntu:22.04
ARG RUNNER_VERSION=2.338.0
ARG RUNNER_SHA256=af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git sudo python3 util-linux jq unzip xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 --shell /bin/bash runner \
    && printf 'runner ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner
WORKDIR /home/runner
RUN curl -fsSL --retry 3 "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" -o runner.tar.gz \
    && echo "${RUNNER_SHA256}  runner.tar.gz" | sha256sum --check \
    && tar xzf runner.tar.gz && rm runner.tar.gz \
    && ./bin/installdependencies.sh \
    && chown -R runner:runner /home/runner
COPY entrypoint.py /opt/bridge-entrypoint.py
USER runner
ENTRYPOINT ["python3", "/opt/bridge-entrypoint.py"]
