# EcoKitchen security

## Phase 6B starting audit

| Priority | Existing exposure at the start of Phase 6B |
| --- | --- |
| High | Inventory writes rely on loopback checks. Inventory, history, and saved operational data have no application-level identity or roles. |
| High | AI routes use loopback checks as their primary barrier. A local caller can trigger paid inference. Legacy scanner uploads lack even that same route guard. |
| High | Legacy upload routes write under directories served by Flask. Legacy result URLs and dashboard media job URLs lack account-level access control. |
| High | Legacy uploads rely on filename extensions and have no consistent request, file, or image decoding limits. Dashboard uploads already have shared checks. |
| High | There is no login, session revocation, CSRF token requirement, or login throttling. CORS is an explicit loopback allowlist but is separate from authorization. |
| Medium | Environment variables hold provider secrets server-side and local environment files are ignored. The application has no dedicated secure session key or cookie deployment policy. Historical media is already tracked and needs a separate redistribution review. |
| Medium | Legacy templates use inline scripts and CDN assets. A strict Content Security Policy and hardened deployment remain separate work. |

## Accounts and access

Flask is the authorization boundary. Username, Werkzeug scrypt password hash, role, server-side session, and login-throttle state live in a separate SQLite file, `instance/auth.sqlite3` by default. The file is created by the account CLI. The signed Flask cookie holds an opaque session ID. The server stores its digest, an expiry time, and a CSRF token. Login rotates the session, logout deletes it, and the server rejects expired or revoked sessions.

| Access | Routes |
| --- | --- |
| Anonymous, within the configured origin boundary | Flask landing page, static CSS and JS, login and session-status endpoints, and `GET /api/forecasting/retail-benchmark`. The benchmark exposes its existing aggregate historical scorecard only. |
| Viewer or administrator | Inventory and history reads, Smart Recommendations overview, recipe ingredient availability, saved menus and Today's Specials, and forecast status. The React dashboard requires sign-in even when opened directly to the retail benchmark. |
| Administrator | Inventory mutations, what-if simulations, forecast or synthetic prediction actions, generation and analysis endpoints, all image and video uploads, private media and legacy saved results. |
| Either signed-in role | Logout with its session's CSRF token. |

The default for registered application routes is authenticated read access and administrator access for unsafe methods. Legacy HTML pages redirect anonymous users to the Flask sign-in page. API requests generally return 401 for a missing session and 403 for an insufficient role or a rejected origin or CSRF token. The website has no registration endpoint. Local management commands run under the operator's operating-system permissions.

## Set up an admin and launch on Windows

From the repository root, use the existing Python environment. If needed, create it first and install `requirements.txt` as described in the [README](../README.md#local-setup).

```powershell
.\.venv\Scripts\python.exe -m flask --app app:app create-user kitchen-admin --role admin
```

Enter a unique password of at least 12 characters twice at the hidden prompts. Create a read-only account by using a different username and `--role viewer`. Passwords are never supplied as CLI arguments. The account store is outside served directories and ignored by Git. Use Windows file permissions to restrict the account database and any private media to the intended operator.

Start Flask and the prebuilt dashboard in separate PowerShell windows.

```powershell
.\.venv\Scripts\python.exe -m flask --app app:app run --host 127.0.0.1 --port 5001
```

```powershell
.\.venv\Scripts\python.exe -m http.server 4173 --bind 127.0.0.1 --directory dashboard/dist
```

Open `http://127.0.0.1:4173/` and sign in as the account you created. The React bundle targets port 5001. The original Flask pages are at `http://127.0.0.1:5001/`. Use the same hostname on both ports, since `localhost` and `127.0.0.1` are different cookie sites. Node or npm is only required to build the frontend. The [build instructions](../README.md#serve-the-built-dashboard-on-windows) reproduce this bundle.

## Cookies, origin checks, and CSRF

- **Loopback development.** The default mode requires a loopback network peer, a loopback request host, and an explicitly permitted browser origin when an Origin header is present. The development cookie is HttpOnly, host-only, and SameSite=Lax, with an eight-hour absolute lifetime. An absent local session key generates a process-only random key, so restarting Flask requires another sign-in. Use an independently generated random `ECOKITCHEN_SECRET_KEY` for persistent sessions. Keep it in private environment configuration.
- **HTTPS configuration.** `ECOKITCHEN_AUTH_MODE=https` requires a key of at least 32 characters, exact API origins in `ECOKITCHEN_HTTPS_HOSTS`, and explicit HTTPS-only values in `INVENTORY_CORS_ORIGINS`. Use a high-entropy generated key. Secure cookies and HSTS are enabled in this mode. The application rejects plaintext and unrecognized hosts. Frontend and API must remain same-site for the Lax cookie. No proxy headers are trusted by default. Establish the real HTTPS request boundary before deploying behind a reverse proxy.
- **Credentialed CORS.** Recognized origins are echoed explicitly, including `http://127.0.0.1:4173` by default. Preflight exposes the JSON, login, and CSRF request headers. Unrecognized origins are rejected. Setting wildcard origins fails configuration validation.
- **CSRF and throttling.** JSON sign-in requires a custom header, which prevents simple cross-origin form submissions. Other unsafe routes require the server-issued session CSRF token. Legacy HTML photo forms also accept their rendered hidden token. Sign-in attempts are limited to eight per direct peer per five-minute window. Unknown account names and bad passwords use the same error, and both paths perform a password-hash check for ordinary password inputs.

## Uploads and retention

Dashboard and legacy image/video submissions use shared multipart checks for one file, its extension and signature, and its byte size. Images are limited to 10 MiB and 24 megapixels and decoded before inference. Videos are limited to 50 MiB and must open and yield a readable frame in the video pipeline. The request limit allows a small multipart overhead. New jobs use random UUID directories and controlled filenames under `instance/vision/`. The media serving route allowlists names and rejects linked paths. Saved legacy media and result URLs now require administrator access. Existing sample files stay in place. A failed job removes its own new scratch directory.

For optional retention, stop Flask and review the dated UUID directories in `instance/vision/`. Back up media needed for records or demos and remove only the reviewed job directories under that root. Their old URLs will expire. Leave the existing `uploads/` and `static/` samples in place unless their references and rights have been reviewed. The application does not periodically purge user media.

## Docker and remaining limits

This configuration was verified as a local application. HTTPS configuration is exercised in tests, with real TLS termination and deployment still to be validated. The app has no MFA, self-service recovery, per-record ownership, or durable security audit log. The throttle is per direct peer and assumes this local topology. Private media is shared among administrator accounts, with manual retention and no antivirus scan. Add inference concurrency limits and resource budgets before accepting network traffic. Legacy inline scripts and CDN dependencies still need a CSP and dependency review. Gemini integration remains externally unverified. The candidate omits the object-detection implementation.

The loopback Compose profile uses Nginx and Gunicorn. Flask accepts a single forwarded client IP and scheme only from the configured Nginx peer and checks the configured origin before authorizing the request. A required signing key and shared SQLite storage keep revocation and login throttling consistent across Gunicorn workers. See [Deployment](deployment.md) for the HTTPS controls, health probe, protected volume, and recovery instructions.

[Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/) · [Werkzeug password hashing](https://werkzeug.palletsprojects.com/en/stable/utils/#werkzeug.security.generate_password_hash) · [OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
