# XEON'S API

A Django 5 dashboard for configurable API proxies and admin-managed plugins. Public endpoints accept query parameters for `GET` APIs and JSON request bodies for `POST` APIs. Plugin source is executed in a best-effort limited child process; this is not a secure sandbox.

## Local setup

Requires Python 3.10+.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:DJANGO_SECRET_KEY = "replace-with-a-long-random-secret"
$env:DJANGO_DEBUG = "True"
$env:DATABASE_URL = Read-Host "Paste the Render PostgreSQL External Database URL"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Set `DATABASE_URL` to the **External Database URL** from your Render PostgreSQL database so these commands use Render's database, not a local database file. Keep the URL private and do not commit it. Open `http://127.0.0.1:8000/`. Sign in with a staff account to upload, edit, disable, or delete endpoints. The Django admin is at `/admin/`.

## Admin plugins

Staff admins can open **Plugins** in the dashboard sidebar, choose **Add plugin**, enter its category, path slug, method, and Python code, then save. The endpoint is served by this same site at `/api/<category-slug>/<plugin-slug>/`; disabling a plugin makes its route return 404. Plugin routes read their active status and code from PostgreSQL on each request, so no service redeploy is needed.

Plugin source may include imports and helper functions, but must define `handler(request)`. The handler receives a dictionary containing `method`, `path`, `query`, and `body`, and may return either a JSON-compatible dictionary or a `(dictionary, status_code)` tuple:

```python
def handler(request):
    name = request["query"].get("name", "world")
    return {"message": f"Hello, {name}"}
```

To return a non-200 status, use a tuple such as `return {"error": "Not found"}, 404`. Status codes must be from 200 to 599. The per-plugin execution timeout is configurable in the admin form, defaults to 15 seconds, and is capped at 120 seconds. This is a Python handler contract, not a Flask application: Flask routes and `app.run()` are not used. Third-party packages installed in the service environment (including packages in `requirements.txt`) can be imported by plugin code.

For convenience, the dashboard displays GET plugin URLs with a `?url=` query placeholder; replace or remove it to match the parameters your handler expects. Each plugin gets a random API key when created; it is shown once in the admin message. Admins can rotate it and optionally require it using `X-API-Key` or `Authorization: Bearer <key>`. Keys are stored as hashes.

Only add code from admins you fully trust. The worker has a clean environment, a per-plugin wall timeout of 1-120 seconds (default 15), 64 KB input limits, and a 256 KB response limit; on Linux it also applies per-process CPU, memory, file-size, and process-count limits. Plugins are rate-limited to 30 requests per minute per IP using the app cache. **These controls do not make a secure sandbox:** plugin code can still access service files and the network, may inspect its parent process, and can affect shared CPU, memory, or availability. Limits are per child, not an aggregate container limit; the default in-memory rate limiter is per web worker and resets on restart. Descendant processes or platform-specific behavior may escape best-effort cleanup. Windows development does not enforce the Linux resource limits. A compromised admin account can therefore put the service and its data at risk. Do not run code from untrusted sources or store sensitive files in the service filesystem.

Successful GET responses from configured endpoints and plugins are cached for 30 seconds, keyed by their query and configuration. POST requests and failures are not cached. Repeated identical GETs can be faster, but may return data up to 30 seconds old; the first request still waits for the upstream API. Dashboard category/API data is cached for 5 seconds and statistics for 10 seconds. The default local-memory cache is per web worker and clears on restart, so use a shared cache backend if running multiple workers and you need cache sharing.

## Frontend JavaScript

The editable browser code is in `frontend/dashboard.js`, outside Django's static files. After changing it, run `npm ci` and `npm run obfuscate:frontend`; this generates `static/dashboard/js/dashboard.obfuscated.js`, which the site loads. Include the generated bundle when deploying. Since the readable source is outside the static directory, `collectstatic` does not publish it. Obfuscation is not encryption: browser code can still be inspected, so do not put secrets in it.

## API routes

- `GET /api/stats/` — live visitors, active API count, requesting IP, and request count.
- `GET /api/endpoints/` — public list of active endpoint configurations and plugins.
- `/api/<category-slug>/<endpoint-slug>/` — calls the configured upstream and records the result. `GET` endpoints take query parameters; `POST` endpoints take a JSON object.
- `GET /api/demo/example-todo/` — seeded demo endpoint that proxies a sample JSONPlaceholder todo.

`params_schema` is a JSON object with parameter names as keys. Set `{"required": true}` for required keys. Unknown parameters and missing required keys are rejected.

## Bulk import format

Upload a `.json` file containing an array of entries:

```json
[
  {
    "name": "Search API",
    "category": "Search",
    "method": "GET",
    "upstream_url": "https://api.example.com/search",
    "params_schema": {"q": {"required": true}},
    "description": "Search endpoint"
  }
]
```

Only endpoint configuration fields are accepted. Uploads are limited to 1 MB / 100 endpoints, and each upstream URL is checked against private, reserved, and internal network addresses. Proxy redirects are disabled. Keep outbound network access appropriately restricted in production.

## Tests

```powershell
python manage.py test
```

## Deploying to Render (manual Web Service, no Blueprint)

1. Push this repository to GitHub. In the Render Dashboard, choose **New + → Web Service**, connect the repository, and select the Python runtime. Do not create a Blueprint.
2. Set the build command to:

   ```sh
   python -m pip install -r requirements.txt && python manage.py collectstatic --noinput
   ```

3. Set the start command to:

   ```sh
   python manage.py migrate && gunicorn xeon_api.wsgi:application --bind 0.0.0.0:$PORT
   ```

4. Create a Render PostgreSQL database in the same region as the web service. In the database's **Info** page, copy its **Internal Database URL**. In the web service, open **Environment → Add Environment Variable**, set the key to `DATABASE_URL`, and paste the URL as the value. Keep it private; do not commit it to the repository. The Internal URL is for the deployed web service; use the **External Database URL** from your local terminal.
5. Add these web-service environment variables before the first deploy:
   - `DJANGO_SECRET_KEY` — use Render's **Generate** option.
   - `DJANGO_DEBUG` — `False`.
   - `DATABASE_URL` — the PostgreSQL Internal Database URL from step 4. Startup fails if this is missing or invalid.
   - `DJANGO_ALLOWED_HOSTS` — your service hostname, for example `xeon-api.onrender.com` (without a scheme).
   - `CSRF_TRUSTED_ORIGINS` — the HTTPS origin, for example `https://xeon-api.onrender.com`.
6. Deploy. To create the admin account without a Render Shell, set `DATABASE_URL` in your local PowerShell terminal to the database's External Database URL, then run `python manage.py migrate` followed by `python manage.py createsuperuser`. Do not share or commit the URL.

WhiteNoise serves the collected Django static files from the web service, so no separate static site is needed.
Before deploying frontend changes, regenerate and include the obfuscated bundle as described above.

## Deploying to Koyeb

1. Deploy this repository as a Python web service and set `DJANGO_SECRET_KEY` to a generated secret, `DJANGO_DEBUG=False`, and `DJANGO_ALLOWED_HOSTS` to the assigned Koyeb hostname. Set `CSRF_TRUSTED_ORIGINS` to the full HTTPS origin, for example `https://your-service.koyeb.app`.
2. Attach a managed PostgreSQL database and set `DATABASE_URL` to its `postgres://` or `postgresql://` connection string. A PostgreSQL URL is required for local development and deployment; no SQLite fallback is configured.
3. Use `pip install -r requirements.txt` as the build command. Set the start command to `python manage.py migrate && python manage.py collectstatic --noinput && gunicorn xeon_api.wsgi:application --bind 0.0.0.0:$PORT`.
4. Run `python manage.py createsuperuser` from a one-off Koyeb shell/task. Configure persistent object storage separately if you later add user-uploaded media; this application stores only parsed JSON configuration and does not retain uploaded files.

The application enables Django CSRF middleware, secure content-type headers, and proxy HTTPS detection. Configure trusted hosts/origins explicitly and use PostgreSQL in production.
