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

Or with Docker Compose — see [Deploying with Docker Compose](#deploying-with-docker-compose) below.

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

## Deploying with Docker Compose

Prebuilt images are published to
[`mryozo/puller`](https://hub.docker.com/repository/docker/mryozo/puller/general)
on Docker Hub (see [Releasing](#releasing)), so you don't have to build
locally unless you're developing against this repo.

**1. Prepare your config and any secret files**

```bash
mkdir -p secrets
cp config/config.example.yaml config.yaml
# edit config.yaml: registries, watchers, rules, commands

# for any registry auth using {file: ...} secret refs, drop the raw value in:
echo -n "my-dockerhub-password" > secrets/dockerhub_password
```

Point the corresponding `{file: ...}` entries in `config.yaml` at
`/run/secrets/<name>` — that's where `docker-compose.yml` mounts the
`secrets/` directory. `{env: VAR}` entries can instead be set directly as
`environment:` values on the service.

**2. `docker-compose.yml`** (already included in this repo)

```yaml
services:
  puller:
    image: mryozo/puller:latest   # or `build: .` to build from source instead
    environment:
      - DOCKERHUB_USERNAME=your-dockerhub-username
      - GHCR_PAT=your-github-pat
    volumes:
      - ./config.yaml:/etc/puller/config.yaml:ro
      - puller-state:/var/lib/puller
      - ./secrets:/run/secrets:ro
    ports:
      - "8080:8080"
    restart: unless-stopped

volumes:
  puller-state:
```

**3. Run it**

```bash
docker compose up -d
docker compose logs -f puller
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
```

`puller-state` is a named volume, so `state.json` survives `docker compose
down` / `up` restarts.

**4. Update to a new version**

```bash
docker compose pull
docker compose up -d
```

(Skip `pull` if you're using `build: .` — run `docker compose up -d --build`
instead after pulling repo changes.)

## Deploying to Kubernetes

Manifests are in [`k8s/`](k8s/); `deployment.yaml` already references the
published `mryozo/puller:latest` image, so no local build is required. Pin
to a specific version tag (e.g. `mryozo/puller:0.2.0`) for anything beyond
testing, since `latest` will move on future releases.

**1. Create the credential secrets**

Rather than hand-editing `k8s/secret.yaml`, create secrets imperatively so
real values never end up in a file you might commit:

```bash
kubectl create secret generic puller-dockerhub-creds \
  --from-literal=password='your-dockerhub-password'
kubectl create secret generic puller-ghcr-creds \
  --from-literal=pat='your-github-pat'
```

These match the volume mounts already wired up in `k8s/deployment.yaml`
(`/run/secrets/dockerhub/password`, `/run/secrets/ghcr/pat`) and the `{file:
...}` refs in `k8s/configmap.yaml`.

**2. Edit the config**

Open [`k8s/configmap.yaml`](k8s/configmap.yaml) and adjust the embedded
`config.yaml` — registries, watchers, rules, and commands — the same as the
standalone config file (see [Configuration](#configuration) above). Note
that `command.exec` runs *inside the container*, so any script it calls
(e.g. `/opt/scripts/deploy.sh`) needs to be baked into a custom image or
mounted in via an additional volume. The stock image does ship with **Ruby
and [Kamal](https://kamal-deploy.org/)** preinstalled (plus `git` and
`openssh-client`), so a `command.shell: 'kamal deploy --version="$PULLER_MATCHED_TAG"'`
trigger works out of the box — you just need to mount an SSH key and the
target app's `config/deploy.yml` into the container and point `command.cwd`
at it. `exec:` (array form) does **not** do shell variable expansion, so use
`shell:` when you need `$PULLER_*` values substituted into the command
itself rather than just passed as env vars.

**3. Apply everything**

```bash
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml      # skip if you created secrets imperatively in step 1
kubectl apply -f k8s/pvc.yaml         # optional, see note below
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

**4. Verify it's running**

```bash
kubectl rollout status deployment/puller
kubectl logs -f deployment/puller
kubectl port-forward deployment/puller 8080:8080
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/metrics | grep puller_
```

**5. Roll out a config or image change**

```bash
kubectl apply -f k8s/configmap.yaml        # after editing it
kubectl rollout restart deployment/puller  # ConfigMap changes aren't picked up automatically

# or, to move to a new image tag:
kubectl set image deployment/puller puller=mryozo/puller:0.2.0
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

## Releasing

Docker images are published by [`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml)
to [`mryozo/puller`](https://hub.docker.com/repository/docker/mryozo/puller/general)
on Docker Hub. Every push and PR to `main` runs the test suite; **publishing
only happens when a `vX.Y.Z` tag is pushed**, so `latest` on Docker Hub
always matches the most recent tagged release rather than whatever's on
`main`.

To cut a release:

```bash
# 1. Bump the version
#    Edit the `version` field in pyproject.toml (this is the single source
#    of truth -- src/puller/version.py reads it back via importlib.metadata).

# 2. Commit the bump
git add pyproject.toml
git commit -m "chore: release v0.2.0"
git push origin main

# 3. Tag and push the tag -- this is what triggers the publish workflow
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

That triggers the workflow to build a multi-arch (`linux/amd64`,
`linux/arm64`) image and push `mryozo/puller:latest`, `:0.2.0`, and `:0.2`.
Watch it under the repo's **Actions** tab, then confirm the new tags show up
on Docker Hub.

Required one-time setup (already done if the workflow is running): repo
secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` under **Settings → Secrets
and variables → Actions**.

Optional but recommended for anything beyond a solo project: draft a GitHub
Release for the tag (`gh release create v0.2.0 --generate-notes`) so there's
a changelog entry alongside the image.

## Contributing

Issues and pull requests are welcome. Please open an issue to discuss any
significant change before submitting a PR.

## License

[MIT](LICENSE)
