# puller

Watches container image registries (Amazon ECR, Docker Hub / Harbor / any
Docker Registry V2 API, GitHub Container Registry) for new images matching
configurable rules, and runs a command whenever the resolved image changes.

It only resolves the manifest **digest** via the registry's HTTP API — no
Docker/containerd daemon and no `docker.sock` dependency. If your triggered
command needs to actually deploy the new image (SSH into a box and pull,
`kubectl set image`, etc.), that's the command's job; `puller` just detects
the change and hands it the details.

## Features

- **Multiple registries in one process** — ECR, Docker Hub/Harbor (or any
  Docker Registry V2 / OCI Distribution Spec compliant registry), and GHCR,
  each with their own auth, defined once and reused across watchers.
- **Multiple watchers per instance** — one YAML config, any number of
  independently-scheduled image watchers.
- **Three rule types**:
  - `semver` — tracks the highest valid [SemVer](https://semver.org/) tag,
    with optional prefix (`v1.2.3`) and pattern filtering.
  - `tag` — tracks a fixed/floating tag (e.g. `production`); fires when it's
    re-pointed at a new digest.
  - `latest` — sugar for `tag: latest`.
- **Arbitrary trigger command** — runs any local command or script on
  change, with image/tag/digest context passed via environment variables. No
  built-in SSH client — if you need to reach a remote host, your script does
  that (`ssh ...`, `kubectl`, a webhook call, anything).
- **Runs standalone or in Kubernetes** — a Dockerfile, `docker-compose.yml`,
  and a full set of Kubernetes manifests are included.
- **Built for unattended operation** — structured JSON logs, Prometheus
  metrics, `/healthz` + `/readyz` probes, graceful shutdown, and per-watcher
  failure isolation (one broken registry connection never affects the
  others).

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp config/config.example.yaml config.yaml
# edit config.yaml: registries, watchers, rules, commands

.venv/bin/python -m puller --config config.yaml --validate-config
.venv/bin/python -m puller --config config.yaml --once   # one-shot dry run, no triggers
.venv/bin/python -m puller --config config.yaml          # run the daemon
```

Or with Docker Compose:

```bash
cp config/config.example.yaml config.yaml
docker compose up --build
```

## Configuration

One YAML file with three top-level sections: `global`, `registries`, and
`watchers`. See [`config/config.example.yaml`](config/config.example.yaml)
for a complete, annotated example.

### `global`

```yaml
global:
  log_level: info                          # debug | info | warning | error
  state_file: /var/lib/puller/state.json   # last-seen digest per watcher
  default_poll_interval_seconds: 60        # used when a watcher doesn't set its own
  health:
    bind_host: 0.0.0.0
    bind_port: 8080
  http:
    timeout_seconds: 10
    max_concurrent_requests_per_registry: 4
```

### `registries`

Each registry is named once and referenced by watchers. Credentials accept a
literal string, `{env: VAR_NAME}`, or `{file: /path/to/secret}` — file refs
are resolved at startup and are the rotation-friendly choice under
Kubernetes (a mounted Secret volume updates in place; an env var requires a
pod restart).

```yaml
registries:
  - name: dockerhub-public
    type: docker_v2                 # generic Docker Registry V2 (Docker Hub, Harbor, ...)
    base_url: https://registry-1.docker.io
    tls_verify: true                # set false for self-signed internal registries
    auth:                           # omit entirely for anonymous/public access
      username: {env: DOCKERHUB_USERNAME}
      password: {file: /run/secrets/dockerhub_password}

  - name: ghcr-main
    type: ghcr                      # GitHub Container Registry
    auth:
      username: my-github-username
      password: {env: GHCR_PAT}     # classic PAT with read:packages

  - name: ecr-prod
    type: ecr
    region: ap-southeast-1
    account_id: "123456789012"
    # access_key_id / secret_access_key / session_token are all optional --
    # omit them to use the default AWS credential chain (instance profile
    # or IRSA in EKS), which is the recommended approach.
```

### `watchers`

```yaml
watchers:
  - name: myapp-prod-semver
    registry: ecr-prod               # references a name from `registries`
    repository: myapp/backend
    poll_interval_seconds: 30        # optional, falls back to global default
    trigger_on_first_run: false      # if true, fires the command on the very first poll too
    rule:
      type: semver
      prefix: "v"                    # strip this prefix before parsing, e.g. v1.2.3 -> 1.2.3
      pattern: "^v[0-9]+\\.[0-9]+\\.[0-9]+$"  # optional pre-filter regex
      include_prerelease: false
    command:
      exec: ["/opt/scripts/deploy.sh"]   # array form: no shell-injection surface
      # shell: "curl -X POST ..."        # or a shell string (mutually exclusive with exec)
      timeout_seconds: 300
      cwd: /opt/scripts
      env:
        DEPLOY_TARGET: production
      env_passthrough: false         # if true, inherits the daemon's full environment too
```

`command` must set exactly one of `exec` (array, preferred) or `shell`
(string, for pipes/redirection).

### Environment variables passed to the triggered command

| Variable | Meaning |
|---|---|
| `PULLER_WATCHER_NAME` | Name of the watcher that fired |
| `PULLER_REGISTRY_TYPE` | `docker_v2`, `ghcr`, or `ecr` |
| `PULLER_REGISTRY_HOST` | Registry hostname |
| `PULLER_REPOSITORY` | Repository/image name |
| `PULLER_IMAGE` | Full `repository@digest` reference |
| `PULLER_MATCHED_TAG` | The tag the rule resolved to |
| `PULLER_NEW_DIGEST` | The newly detected digest |
| `PULLER_PREVIOUS_DIGEST` | The previously recorded digest (empty on first trigger) |
| `PULLER_RULE_TYPE` | `semver`, `tag`, or `latest` |
| `PULLER_TIMESTAMP` | UTC ISO-8601 timestamp of the trigger |
| `PULLER_EVENT_ID` | Unique id for this trigger, for log correlation |

Exit code `0` advances the stored state; a non-zero exit leaves it unchanged
so the same change is retried on the next poll. A watcher will never run two
triggered commands concurrently — an overlapping trigger is skipped (and
logged) until the previous one finishes.

## CLI

```
python -m puller --config <path> [--validate-config] [--once] [--log-level LEVEL]
```

- `--validate-config` — load and validate the config, then exit.
- `--once` — resolve every watcher's current tag/digest once and print it,
  without polling or running any commands. Useful for testing registry
  connectivity and rule configuration.
- `--log-level` — override `global.log_level`.

## Running in Kubernetes

Manifests are in [`k8s/`](k8s/):

```bash
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml      # edit with real credentials first
kubectl apply -f k8s/pvc.yaml         # optional, see note below
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

Notes:
- The Deployment runs a **single replica** with `strategy: Recreate`, since
  polling/triggering is not designed for multiple concurrent replicas.
- The state file (`state.json`) is optionally persisted on a small PVC. If
  you skip the PVC, the service still works correctly — it just re-baselines
  silently on every pod restart instead of remembering what it last saw (no
  false triggers, just no cross-restart memory).
- For ECR, prefer an IRSA-annotated `ServiceAccount` (see the comment in
  `k8s/deployment.yaml`) over static AWS keys.
- Credentials are mounted as **Secret volumes**, not env vars, so rotation
  doesn't require a pod restart.

## Observability

- Structured JSON logs to stdout.
- `GET /healthz` — liveness.
- `GET /readyz` — readiness (ready once config/state are loaded at startup).
- `GET /metrics` — Prometheus metrics: `puller_poll_total`,
  `puller_trigger_total`, `puller_last_poll_timestamp_seconds`,
  `puller_last_digest_change_timestamp_seconds`,
  `puller_command_duration_seconds`.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/pytest tests/unit -q               # fast, fully mocked
.venv/bin/pytest tests/integration -m integration -q   # spins up a local `registry:2` container, requires Docker
```

## Contributing

Issues and pull requests are welcome. Please open an issue to discuss any
significant change before submitting a PR.

## License

[MIT](LICENSE)
