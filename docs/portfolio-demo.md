# EcoKitchen portfolio demo

This three-minute walkthrough uses the actual React dashboard, Flask API, and SQLite inventory. The demo uses fictional stock, a local-only account, and the existing aggregate FreshRetailNet scorecard.

## Set up on Windows without Node or npm

Use the local Python 3.11 environment and the built `dashboard/dist` configured for Flask on port 5001. If the build is missing, use the [build instructions](../README.md#react-dashboard) on a Node-enabled machine. The Windows host only needs Python to serve the result.

Run from the repository root in a PowerShell window. The seeder creates six fictional ingredients and ten lots with expiry dates relative to the run date. It records an example stock adjustment and refuses to overwrite an existing demo directory.

```powershell
.\.venv\Scripts\python.exe -m tools.portfolio_demo
.\.venv\Scripts\python.exe -m flask --app tools.portfolio_demo:create_app create-user portfolio-demo-admin --role admin
.\.venv\Scripts\python.exe -m flask --app tools.portfolio_demo:create_app run --host 127.0.0.1 --port 5001
```

Create a unique administrator password at the hidden prompts. Use `create-user portfolio-demo-viewer --role viewer` with the same Flask factory if you want to demonstrate read-only access. Start the static server in a second PowerShell window from the same directory.

```powershell
.\.venv\Scripts\python.exe -m http.server 4173 --bind 127.0.0.1 --directory dashboard/dist
```

Open [http://127.0.0.1:4173/](http://127.0.0.1:4173/) and sign in. If either port is already serving the regular application, stop that server from its original terminal before starting the demo. Close both demo terminals when finished.

The launcher stores inventory, accounts, sessions, and media and menu state under ignored `instance/portfolio-demo/`. It uses empty integration credentials and missing checkpoint paths. It serves only the dashboard APIs, so legacy pages and historical sample media remain outside the demo. The standard Flask launcher still serves legacy pages. For a fresh rehearsal later, set `$env:ECOKITCHEN_DEMO_NAME = 'portfolio-demo-rehearsal'` in the Flask terminal and repeat setup with a new directory name. If you reuse a demo, start Flask directly. Seed close to the demonstration date to keep the expiry window relevant.

## Three-minute walkthrough

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:35 | Sign in, then open [Overview](http://127.0.0.1:4173/#overview) and [Inventory](http://127.0.0.1:4173/#inventory). | “Here’s a fictional kitchen. I can trace each balance to a stock lot and see its expiry date. Administrators can manage stock, while viewers have read-only access.” |
| 0:35–1:10 | Open [Alerts](http://127.0.0.1:4173/#alerts) and [Smart Recommendations](http://127.0.0.1:4173/#recommendations). Show **Use first** and **Replenishment**. | “FEFO ranks known, unexpired stock by expiry. The demo has seven eligible kilograms of chickpeas against a twelve-kilogram minimum. That leaves a five-kilogram replenishment gap. Yogurt has a purchasing estimate withheld because the cost is missing.” |
| 1:10–1:55 | In **Consumption and cost simulation**, choose **Demo chickpeas**, enter **4 kg**, leave assumed cost blank, and run. | “The calculation uses three kilograms from the first lot and one from the next. It leaves three kilograms available and raises the projected replenishment to nine kilograms. The scenario never writes back to inventory.” |
| 1:55–3:00 | Open [Retail Demand Benchmark](http://127.0.0.1:4173/#forecasting?view=retail). Show both charts and the stockout comparison. | “This is a separate grocery-retail research experiment. LightGBM reduced holdout WAPE from 41.8314% to 33.6450%, an 8.1864-point improvement across 350,000 forecasts. The dashboard reads the completed scorecard. It measures normalized observed sales, so stockouts can hide demand.” |

The demo disables Gemini by default. Unavailable providers and withheld object-detection workflows return HTTP 503. Fictional saved menus require no provider request.

## Capture and redistribution

The [public screenshot set](screenshots/README.md) was captured from the isolated Flask launcher and a fresh Chrome profile. To reproduce it, start the production build using the commands above. Keep private uploads, profiles, credentials, and developer tools outside the frame.

The Windows build emits React/Lucide code and DM Sans/Manrope fonts. The published screenshots contain rendered pixels. Keep the benchmark attribution and UI credits with the capture set. [Third-party notices](third-party-notices.md) · [Release audit](public-release-verification.md)

A future Windows ZIP would need the applicable copyright and license texts for the exact packages shipped. The source release includes the local build and startup commands.
