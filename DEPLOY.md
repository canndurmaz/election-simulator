# Deploying Election Night

The server is a single Python process (FastAPI + WebSockets) that also serves the frontend.
Everything is self-contained: no database, no CDN (three.js is vendored), so it runs on any small VPS.

> **Important:** rooms are kept in memory. Run exactly **one** worker/container, and note that restarting
> the server ends all running games. Deploy between game nights.

## Option A — Docker on a VPS with HTTPS (recommended)

Needs: a Linux server (1 vCPU / 512 MB is plenty for a few rooms), a domain name, Docker with the compose plugin.

1. Point your domain's **A record** (e.g. `election.example.com`) to the server's IP.
2. Open ports **80** and **443** in the server firewall / cloud security group.
3. On the server:
   ```bash
   git clone <your repo> election && cd election
   cp deploy/.env.example .env      # set DOMAIN and a long random ADMIN_TOKEN
   docker compose up -d --build
   ```
4. Open `https://election.example.com`. Caddy fetches the certificate automatically on first request;
   WebSockets (`wss://`) work through it with no extra configuration.
5. Admin page: `https://election.example.com/admin` with your `ADMIN_TOKEN`.

Update to a new version: `git pull && docker compose up -d --build`.
Logs: `docker compose logs -f game`.

## Option B — Docker without a domain (LAN / quick test)

```bash
docker build -t election .
docker run -d --name election -p 8000:8000 -e ADMIN_TOKEN=secret --restart unless-stopped election
```
Players open `http://<server-ip>:8000`. (Plain HTTP: fine on a LAN, not for the public internet.)

## Option C — no Docker

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd backend && ADMIN_TOKEN=secret ../.venv/bin/uvicorn app.server:app --host 0.0.0.0 --port 8000 --workers 1
```
To keep it running, use a systemd unit:
```ini
# /etc/systemd/system/election.service
[Unit]
Description=Election Night
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/election/backend
Environment=ADMIN_TOKEN=change-me
ExecStart=/opt/election/.venv/bin/uvicorn app.server:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers
Restart=always

[Install]
WantedBy=multi-user.target
```
and put Caddy or nginx in front for HTTPS. For nginx, WebSockets need:
```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 1h;
}
```

## Checklist

- [ ] `ADMIN_TOKEN` set to a long random value (otherwise a random one is printed in the logs at startup)
- [ ] One worker only
- [ ] Proxy passes WebSocket upgrades (Caddy does by default)
- [ ] Clear idle rooms from `/admin` if needed (idle rooms are also cleaned automatically after 30 min)
