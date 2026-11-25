# Render Free portfolio demo

This deployment serves the React dashboard and the Flask read API from one Render Free Docker web service. It uses the included `onrender.com` HTTPS address. Expect a temporary wait after the service sleeps. This is a recruiter demo with fictional kitchen records. Keep the normal local application for administration and access to optional features.

## Cost checks before creating the service

- Confirm the workspace is **Hobby ($0)** and its Billing page shows **no payment method**. Render documents that Free Docker hosting does not require a card. If your account requires a card, stop here and use the static GitHub Pages option.
- Select **Free ($0)** compute for the Web Service itself. This is **0.1 CPU and 512 MB RAM**. The paid entry-level plan also has 512 MB, so verify the plan name and price.
- Use only one web service. Leave persistent disks, databases, background workers, paid APIs, and custom domains disabled. Use manual deploys and no preview environments.
- Hobby includes 750 running instance hours, 5 GB outbound bandwidth, and 500 Starter build minutes per month across the workspace. With no payment method, bandwidth exhaustion suspends Free services and build-minute exhaustion pauses new builds until the next month. Exhausting free instance hours suspends Free services. Keep the account cardless since adding a card enables usage overages. Check the current terms before deployment.

[Render Free limits](https://render.com/docs/free) · [Compute plans](https://render.com/docs/compute-plans) · [Pricing](https://render.com/pricing)

## Create the service after approval

1. In the Render Dashboard, select **New → Web Service**. Connect the public `Rimmii7702/EcoKitchen-2.0` repository and select the verified deployment branch or its approved merge. Keep the repository root as the build context.
2. Choose **Docker** and set the Dockerfile path to `deploy/Dockerfile.render`. Leave the Docker command override blank so the image runs its own launcher. Give the service a name such as `ecokitchen-2-0` and select the **Free** instance. The expected URL is `https://ecokitchen-2-0.onrender.com/`, depending on availability.
3. Set **Auto-Deploy** to **Off** before the first deploy and leave previews disabled. Set **Health Check Path** to `/api/health`.
4. In **Environment**, set the following. Keep Gemini credentials unset and avoid attaching shared environment groups containing secrets.

   | Variable | Value |
   | --- | --- |
   | `ECOKITCHEN_PUBLIC_DEMO` | `1` |
   | `ECOKITCHEN_SECRET_KEY` | A unique random value stored as a Render runtime environment secret. |
   | `PORT`, `RENDER_EXTERNAL_URL` | Use Render's assigned values. The launcher binds Nginx to the assigned port and validates the HTTPS hostname. |

   Generate a secret on a trusted machine with `python -c "import secrets; print(secrets.token_hex(32))"`. Copy it only into Render's environment settings. Omit it from Git, screenshots, Vite variables, and Docker build arguments. Visitors enter no credentials. The demo supplies its viewer identity server-side.
5. Recheck **Free** on the creation screen and verify Billing still shows no payment method. Start one manual deploy. The service should begin reporting healthy after it seeds the fictional state. The normal Flask/Compose deployment continues to run independently.

[Render web service setup](https://render.com/docs/web-services) · [Render environment variables](https://render.com/docs/environment-variables)

## Verify a deployment

- Open the HTTPS address in a private browser window. The dashboard should open directly as a read-only viewer. Check overview, inventory search, stock history, expiry and low-stock alerts, Smart Recommendations, and Today's Specials. The what-if calculator stays in the local administrator app.
- Open Forecasting. The historical FreshRetailNet benchmark must show 350,000 holdout observations and a WAPE improvement of 8.1864 percentage points when rounded for display. It evaluates normalized observed grocery sales. The benchmark loads the existing scorecard and requires no inference, training, or dataset download.
- Check that `/api/health` returns `{"status":"ok"}` and `/api/auth/session` identifies the public viewer. A POST to `/api/inventory` should return 405, and `/legacy/` should return 404. Repeat the browser checks after a restart or cold start.

On startup the launcher creates fresh, private temporary SQLite files and seeds six fictional ingredients, ten lots, transaction history, and one viewer account. The inventory service opens its database with SQLite read-only mode after seeding. Nginx serves the static bundle and forwards only API paths to an internal Gunicorn listener. Flask registers only approved read endpoints. Gemini, YOLO, logins, administrative operations, and uploads are unavailable. The same-origin demo has no cross-origin allowance.

Render sleeps after 15 minutes without inbound traffic and documents approximately one minute for wake-up. It discards the temporary filesystem after sleep, restart, or redeploy. On each new startup, the fixtures are recreated with expiry dates relative to that day. Service maintenance or quota suspensions can make the demo temporarily unavailable.

## Reproduce the resource check

From the repository root on a Linux Docker host with cgroup v2, run:

```sh
python deploy/smoke_render.py
```

The script builds the image, binds only to a random loopback port, sets a 0.1 CPU quota and a 512 MiB memory limit with no added swap, and verifies the production assets, HTTPS forwarding boundary, viewer APIs, benchmark, and startup reseeding. It prints the image size in bytes and the observed Linux cgroup memory peak. GitHub Actions runs this alongside the existing local Compose smoke test. These results describe the test container. Render edge behavior and account eligibility are verified only when the owner approves deployment.

## Remove the service

Open the service's **Settings** in Render and choose **Delete Web Service**. Confirm the displayed service name. Verify it is gone from the workspace and that Billing shows no paid resources. Remove the GitHub integration's repository access if it was granted solely for this project. The public repository and local application remain available.

If cardless Free service creation is unavailable, use a separate GitHub Pages build labeled **Frontend demonstration** with fictional browser-side data and the published scorecard. Keep the existing Flask application and CI intact.
