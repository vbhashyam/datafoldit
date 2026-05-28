# DataFold IT Private Deployment

This app is ready to run as a private organization website with Docker.

## Recommended Setup

Use a small Linux server and keep the app bound to `127.0.0.1` on that server. Put an identity-aware access layer in front of it, such as Cloudflare Tunnel with Cloudflare Access, so only approved organization users can reach the site.

The app still has its own password login. The access layer is the outer door; the app password is the inner door.

## Server Files

Copy the project folder to the server, then create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Edit `.env` and set strong values:

```text
DATAFOLDIT_PASSWORD=your-long-private-dashboard-password
DATAFOLDIT_SECRET=a-long-random-secret-used-for-login-cookies
DATAFOLDIT_COOKIE_SECURE=1
```

## Start The App

```bash
docker compose up -d --build
```

If the server has the older Compose command, use:

```bash
docker-compose up -d --build
```

The app listens on the server at:

```text
http://127.0.0.1:8765
```

The compose file intentionally binds to localhost only:

```text
127.0.0.1:8765:8765
```

That keeps it off the public internet until a private access layer is configured.

## Private Organization Access

Recommended flow:

1. Point a private hostname to the server through your access layer, for example `ops.datafoldit.com`.
2. Configure the access layer to allow only your organization emails.
3. Forward traffic to `http://127.0.0.1:8765`.
4. Open the hostname and sign in with the DataFold IT app password.

## Data And Backups

Persistent files live in:

```text
./data
```

This includes:

- `datafoldit.db`
- uploaded attachments
- local backups

Back up the whole `data` folder regularly. You can also use the Reports page to download an Excel report and SQLite database backup.

## Updating The App

After copying new code to the server:

```bash
docker compose up -d --build
```

Or, on older Docker installs:

```bash
docker-compose up -d --build
```

The `./data` folder is preserved.

## OCR Support

The Docker image includes:

- `tesseract-ocr`
- `poppler-utils`

That lets PDF/image invoice reading work on Linux without macOS Quick Look.
