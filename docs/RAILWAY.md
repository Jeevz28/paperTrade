# Railway deployment — PaperLab 0.8.1

This release supports one private owner using the dashboard over HTTPS. It still
places paper orders only. The Dockerfile builds the frontend and Python backend
into one service. Local `python run.py` still works as before.

## Deploy

1. Extract the ZIP. Put the contents of its `paperlab` folder in a private GitHub
   repository. Do not upload `.env`, `data`, `.venv` or `node_modules`.
2. In Railway create a project from that repository. The service root must contain
   `Dockerfile` and `run.py`. If you committed the outer folder too, set Root Directory
   to `/paperlab`. Railway builds using the Dockerfile; do not add a separate frontend
   service or override the start command. Startup before configuration may fail safely.
3. Attach a persistent volume to this service, mounted at `/data`.
4. Generate an HTTPS public domain under Networking. Set these service Variables:

| Variable | Value |
| --- | --- |
| `PAPERLAB_HOSTED` | `true` |
| `PAPERLAB_PUBLIC_URL` | Your full `https://...up.railway.app` URL, no path |
| `PAPERLAB_USERNAME` | Your chosen login name |
| `PAPERLAB_PASSWORD` | A unique random password, at least 16 characters |
| `PAPERLAB_DB` | `/data/trading.db` |
| `PAPER_INITIAL_CAPITAL` | `5000` or your initial simulated capital |
| `PAPERLAB_MODE` | `licensed_private` for authorized live data |
| `DATA_PERMISSION_REFERENCE` | Your actual data-use basis/reference |
| `UPSTOX_ENABLED` | `true` |
| `UPSTOX_ACCESS_TOKEN` | Your current valid Upstox access token |
| `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS` | `0` |
| `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` | `60` |

Railway supplies `PORT` and `RAILWAY_VOLUME_MOUNT_PATH`; do not invent the latter
instead of attaching a volume. `PAPERLAB_PUBLIC_URL` is optional if Railway supplies
`RAILWAY_PUBLIC_DOMAIN`. Set the explicit URL for a custom domain.

5. In service Settings set health-check path `/healthz`, timeout 120 seconds,
   restart policy On Failure, and a single replica in a single region. Disable
   Serverless/sleep so the background engine can keep running without browser traffic.
6. Deploy. Open the HTTPS URL and enter your dashboard username/password in the
   browser's login prompt. Capital, feed and strategy setup remain automatic.

These dashboard settings are authoritative. No legacy railway.json is required:
Railway's current documentation marks Config as Code as deprecated. Docker startup
uses the supplied port, one worker and Railway's HTTPS proxy headers.

## Operation and persistence

The database holds market data, accounts, positions, reservations, decisions and
reviews. The volume preserves it across redeploys; enable Railway volume backups.
A new volume starts a new paper account. Changing capital on an existing database
does not deposit money. Keep the same volume when upgrading.

To migrate an existing local account, stop the local app and use SQLite's backup
API to produce a consistent database copy. Transfer that copy to `/data/trading.db`
while the hosted service is stopped, before its first normal startup. Do not copy
an actively written database alone or run both copies as the same experiment.

Update an expired Upstox token in Variables and redeploy. No token-refresh workflow
is added in this patch. A connected feed still depends on your provider permissions,
valid credentials and network availability. Read the dashboard's actual status.

Only `/healthz` is anonymous; it exposes an application liveness response, not feed
connectivity or trade readiness. All other hosted pages and APIs require HTTP Basic
authentication over HTTPS. Use a private browser window and close it to discard the
browser's remembered login; there is no in-app logout or multi-user account system.
Never enable a public raw TCP proxy: the hosted server trusts Railway's HTTP proxy.

The service refuses missing authentication, an unconfigured volume, or a database
outside that volume. A file lock prevents two workers using the same database.
It cannot coordinate two independently copied databases on different volumes.

Restart/deploy downtime interrupts quotes and stop monitoring. Stops are simulated
in the running app, not broker-held orders; fills require fresh quotes after recovery.
No fills or market data are invented for time the service was offline.

## Troubleshooting

- **Startup configuration error:** check Variables and attached volume; never put a
  short/default password in the code to bypass startup validation.
- **403:** use the exact HTTPS URL configured in `PAPERLAB_PUBLIC_URL`.
- **401:** enter the dashboard credentials, not the Upstox token.
- **Feed unavailable:** inspect the displayed runtime reason and renew provider
  credentials when needed. A successful deployment alone does not verify Upstox.
- **Another worker owns database:** use one worker/replica and wait for the old
  deployment to stop. A normal process exit releases the lock automatically.
- **No orders:** inspect warm-up, market hours, strategy and capital/risk reasons.

References checked 12 October 2026:
- https://docs.railway.com/deployments/healthchecks
- https://docs.railway.com/volumes
- https://docs.railway.com/config-as-code/reference
- https://docs.railway.com/deployments/deployment-teardown

This package has not been deployed to your Railway account. Docker image execution
and authenticated live Upstox acceptance must be checked after deployment.
