FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin puller
COPY --from=builder /install /usr/local
COPY config/config.example.yaml /etc/puller/config.yaml

ENV PYTHONUNBUFFERED=1
USER puller
EXPOSE 8080

ENTRYPOINT ["python", "-m", "puller"]
CMD ["--config", "/etc/puller/config.yaml"]
