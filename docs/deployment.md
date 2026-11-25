# EcoKitchen deployment

EcoKitchen can run as a local two-container application or with the existing Windows Python setup. The container uses Nginx to serve the compiled React dashboard and forward Flask requests on the same origin. Gunicorn runs Flask on the private Compose network. Its two workers share a session-signing key and the SQLite authentication store. The React page and API both use `http://127.0.0.1:8080` by default.

## Requirements and source files

| Layer | Requirements |
| --- | --- |
| Docker host | Docker with a running Linux engine and Compose v2.20 or newer. Docker Desktop on Windows needs a working Linux backend, such as WSL 2. Keep Docker current for localhost port publishing. |
| Backend | CPython 3.11.15 on Debian Bookworm slim, Gunicorn 26.0.0, and pinned Python transitive dependencies in `requirements-deploy.lock`. The default image needs no additional OS packages. |
| Frontend | Node 24.22.2 and `npm ci` with `dashboard/package-lock.json` in the build stage. Nginx 1.30.8 Alpine is the runtime. A Docker host needs neither Node nor Python for normal operation. |
| CI | Ubuntu 24.04 x64, Python 3.11.15, Node 24.22.2, Docker Compose, and the standard runner `libgomp1` for LightGBM fixture tests. `requirements-ci.lock` adds the existing forecast and analysis test dependencies. |
| State | One managed Docker volume at `/app/instance`. The backend user is UID and GID `10001`. |

A clean checkout needs the Flask sources, Jinja templates, static CSS/JS, `data/` reference CSVs, the generated fictional menu fixture, dashboard source and lockfile, deployment files, and the versioned `forecasting/results/freshretailnet-holdout-v1.json` scorecard. Include the existing benchmark modules, test fixtures, other published scorecards and test suites for CI. Downloaded FreshRetailNet files, private uploads, legacy sample media, model weights, SQLite databases, and generated builds are excluded from the runtime image. Legacy Flask routes remain available at `/legacy/` and their prior feature URLs. Historical sample results and videos are outside this edition. The benchmark reads the aggregate JSON scorecard and does no inference, data downloads, training, or rescoring.

## First run

Run these from the repository root. Generate the session secret once and keep it private. Loading or restarting the same stack with the same key preserves signed sessions until they expire or are revoked. If `.env` already exists, add the setting without replacing its other values. Do not print or commit the result.

PowerShell with Python available:

```powershell
$env:ECOKITCHEN_SECRET_KEY = py -3.11 -c "import secrets; print(secrets.token_hex(32))"
```

PowerShell without Python, once the Docker Linux engine is running:

```powershell
$env:ECOKITCHEN_SECRET_KEY = docker run --rm python:3.11.15-slim-bookworm python -c "import secrets; print(secrets.token_hex(32))"
```

On Bash, use `export ECOKITCHEN_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"`. The commands below require the variable in the shell or a stable value in the ignored `.env` file.

```text
docker compose config --quiet
docker compose up --build --detach --wait
docker compose exec backend flask --app app:app create-user kitchen-admin --role admin
```

Enter a unique password of at least 12 characters at the hidden prompts. An empty inventory schema initializes on the first boot. To load the repository's historical demo stock, run `docker compose exec backend flask --app app:app import-inventory`. It keeps the original dates and skips existing imports. Open `http://127.0.0.1:8080/`, sign in, and use **Forecasting → Retail Demand Benchmark** to check the historical benchmark. To create the read-only account, run `docker compose exec backend flask --app app:app create-user kitchen-viewer --role viewer` and use a separate browser profile when comparing roles. `GET /api/health` checks the inventory database and returns a minimal status response. Successful health checks mean the app is serving and inventory is ready. Create an account before attempting login.

## Configuration and boundaries

| Variable | Container behavior |
| --- | --- |
| `ECOKITCHEN_SECRET_KEY` | Required stable secret of at least 32 characters. Generate it with a secure random source. Keep the same key across workers and container recreations. |
| `ECOKITCHEN_PORT` | Published IPv4 loopback port. Default `8080`. Use the exact `127.0.0.1` URL. |
| `ECOKITCHEN_DOCKER_NETWORK_PREFIX` | First three octets of a private, unused IPv4 subnet. Default `172.30.96`. Compose uses the `.0/28` subnet, with Nginx at `.2` and Flask at `.3`. |
| `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_RECIPE_MODEL` | Optional server-side settings. A clean stack can run with a blank key. Explicit inference actions may incur charges when configured. |
| `ECOKITCHEN_INSTALL_AI` | Defaults to `0`. Set to `1` and rebuild the backend to install optional `requirements-ai.txt` and Debian `libgl1` and `libglib2.0-0` for OpenCV. This image is larger. |
| `INVENTORY_DB_PATH`, `ECOKITCHEN_AUTH_DB`, `RECOMMENDATIONS_FILE` | Compose supplies absolute paths inside the named volume. Private job media and menu snapshot history use that same volume. The bundled menu is copied only if the volume has no saved snapshot. |
| `ECOKITCHEN_TRUSTED_PROXY`, `ECOKITCHEN_PROXY_ORIGIN` | Compose supplies the exact Nginx socket peer and browser origin. These settings are optional in direct Windows mode. |

Optional Gemini workflows use the server-side API key. Optional image dependencies add Pillow, OpenCV, and Matplotlib for Gemini photo processing. Object detection and annotated waste-photo analysis are unavailable in this edition. Missing optional credentials return HTTP 503. Saved fictional menus, SQLite inventory, and the historical benchmark work without provider access.

Compose publishes Nginx only on loopback and publishes no Gunicorn port. Nginx replaces forwarded client and scheme headers with values it observes. Flask accepts those values only from the configured Nginx peer and rejects mismatched hosts, schemes, or browser origins. The backend has a direct socket exception for its minimal loopback health probe. The SQLite throttle is shared across workers and uses the IP observed by Nginx. Docker Desktop NAT may group local callers behind one IP and therefore one limit. Keep the application network dedicated to the stack. See [Nginx header forwarding](https://nginx.org/en/docs/http/ngx_http_proxy_module.html) and [Compose startup health checks](https://docs.docker.com/compose/how-tos/startup-order/).

This Compose file serves HTTP for local use. For a separately reviewed HTTPS deployment, configure Nginx or another explicit trusted proxy for TLS termination and forwarding, set `ECOKITCHEN_AUTH_MODE=https` with the exact public HTTPS origin in `ECOKITCHEN_HTTPS_HOSTS` and `ECOKITCHEN_PROXY_ORIGIN`, and retain a strong signing key. The production proxy must replace inbound forwarding headers and the backend must remain private. HTTPS mode requires Secure cookies and uses HSTS. Keep a same-origin frontend/API setup, or configure exact HTTPS CORS origins for a deliberate same-site split. The default Compose stack does not provide a public endpoint or terminate TLS. See [security policy](security.md) for current account and media limitations.

## Persistence, backup and recovery

The default Compose stack uses a named volume separate from the host `instance/` directory. Files created during local Windows use stay where they are. `docker compose stop`, `restart`, and `down` retain the named volume. `down --volumes` deletes the selected project's state. Only one app instance should write a given volume. The two Gunicorn workers share the same local SQLite files. There is no automatic schema migration. Startup checks an existing inventory file and reports invalid schemas for operator review.

To take a consistent backup, stop the stack and archive the state volume to a private location. The example uses a PowerShell directory under the ignored `instance/` folder. Restrict access to the backup. It contains accounts, session state, inventory, private media, and menu snapshots.

```powershell
$backup = (New-Item -ItemType Directory -Force instance/deployment-backups).FullName
$archive = "ecokitchen-state-$(Get-Date -Format yyyyMMdd-HHmmss).tgz"
docker compose stop
docker compose run --rm --no-deps --volume "${backup}:/backup" --entrypoint tar backend -czf "/backup/$archive" -C /app/instance .
docker compose up --detach --wait
```

Restore only a trusted archive into a fresh volume. Use a new project name, port, and subnet to keep the original volume intact. Set the session secret in this shell first. Use a fresh key if restored sessions should expire. Restore under a separate browser profile, since browsers share same-host cookies across ports.

```powershell
$env:ECOKITCHEN_PORT = '8081'
$env:ECOKITCHEN_DOCKER_NETWORK_PREFIX = '172.30.98'
docker compose -p ecokitchen-recovery run --rm --no-deps --volume "${backup}:/backup:ro" --entrypoint tar backend --no-same-owner -xzf "/backup/$archive" -C /app/instance
docker compose -p ecokitchen-recovery up --detach --wait
```

Open `http://127.0.0.1:8081/` and verify accounts, inventory, media, and snapshots before switching anything over. Stop the recovery stack and clear the temporary `ECOKITCHEN_PORT` and `ECOKITCHEN_DOCKER_NETWORK_PREFIX` settings when finished. From a new shell, set `$backup` and `$archive` to the directory and name of the backup you intend to restore.

For an existing Windows installation, stop Flask and package only its default state files into a portable archive. Adjust the source paths if your `.env` overrides them. An absent auth file means you will create an account after recovery.

```powershell
$backup = (New-Item -ItemType Directory -Force instance/deployment-backups).FullName
$archive = "ecokitchen-windows-$(Get-Date -Format yyyyMMdd-HHmmss).tgz"
$state = @('inventory.sqlite3', 'auth.sqlite3', 'vision', 'ai-menus', 'recommendations.json') |
    Where-Object { Test-Path -LiteralPath (Join-Path 'instance' $_) }
if (-not $state) { throw 'No default EcoKitchen runtime state found' }
tar.exe -czf (Join-Path $backup $archive) -C instance @state
```

Keep the Windows copy and restore to a separate named volume as above. If you use a custom legacy menu snapshot, include a copy as `recommendations.json` at the archive root. If SQLite left journal or WAL files, use SQLite's backup facility for those databases or include their matching sidecars in the stopped-state archive.

## CI and verification

The GitHub Actions workflow runs on pushes and pull requests with read-only repository permission. It checks for common secrets and disallowed tracked runtime files, compiles Python sources, installs the locked CI packages, runs backend and frontend tests, builds Vite, and builds both Docker images. Its isolated Compose test creates temporary admins and viewers, verifies access and CSRF, checks the retail benchmark and optional-provider errors, then stops and starts the stack to verify session and stock persistence. There is no publish or deployment job. The basic repository check is not a full scan of Git history or all secret formats. The source checker accepts only the five independently generated data fixtures under data/.

Run the same isolated smoke test from a Docker host with Python 3.11. It defaults to port 18080 and uses a dedicated volume that it removes when finished.

```powershell
py -3.11 deploy/smoke.py
```

Use the regular stack for a desktop and mobile browser check. Confirm sign-in, a test stock entry, viewer controls, and the Retail Demand Benchmark at `http://127.0.0.1:8080/#forecasting?view=retail`. Browser testing requires an operator-created account. The automated Compose test checks HTTP and the static bundle.

To refresh the pinned Python locks deliberately, use uv from the repository root and review changes before committing them.

```text
uv pip compile --python-version 3.11 --python-platform x86_64-manylinux_2_28 --no-header --no-annotate requirements-deploy.in -o requirements-deploy.lock
uv pip compile --python-version 3.11 --python-platform x86_64-manylinux_2_28 --no-header --no-annotate -c requirements-deploy.lock requirements-ci.in -o requirements-ci.lock
```

## Troubleshooting

- **Docker engine unavailable.** Start Docker Desktop, check its Linux engine and `docker info`. On Windows, repair or install the Linux backend through your organization's supported setup. `docker compose config --quiet` validates configuration without proving the engine can build or run it.
- **Port or subnet conflicts.** Set `ECOKITCHEN_PORT` or a free private `ECOKITCHEN_DOCKER_NETWORK_PREFIX` before starting. Use the same values through restarts and health checks.
- **Unexpected sign-in errors.** Use the exact `127.0.0.1` host. Check that the account CLI ran against the active Compose project. After too many attempts, wait for the rate-limit window. `docker compose ps` and `docker compose logs --tail 80 backend web` show startup and proxy diagnostics. `docker compose config --quiet` avoids printing interpolated secrets.
- **Missing AI dependencies or artifacts.** Check the configuration table. Keep provider credentials server-side and model mounts read-only.
- **Windows host without Node.** Keep the [Python-only Windows workflow](../README.md#serve-the-built-dashboard-on-windows). Run Flask on 5001 and serve the port-5001-compatible `dashboard/dist` on 4173. A clean source checkout needs that compatible build produced on a Node-enabled machine first. The Docker build uses same-origin URLs and is intended to run behind Nginx.
