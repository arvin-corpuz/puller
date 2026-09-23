FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

# Ruby + Kamal, so trigger commands can shell out to `kamal deploy` directly
# without needing a separate custom image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ruby-full \
        build-essential \
        git \
        openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && gem install kamal --no-document

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin puller
COPY --from=builder /install /usr/local
COPY config/config.example.yaml /etc/puller/config.yaml

ENV PYTHONUNBUFFERED=1
USER puller
EXPOSE 8080

ENTRYPOINT ["python", "-m", "puller"]
CMD ["--config", "/etc/puller/config.yaml"]
