# Deploy Cathena

One small Linux server runs everything: the app plus Caddy for automatic https.

## 1. Server

- Any VPS with 1 vCPU, 1 GB RAM and Docker (Ubuntu 24.04 is fine). A shared server works: Cathena uses about 400 MB RAM and 1 GB disk. Pick a **non-US region**: Hyperliquid restricts US users.
- A domain or subdomain, e.g. `app.cathena.xyz`. Add a DNS **A record** pointing at the server's IP.
- Open ports 80 and 443.

Install Docker on a fresh Ubuntu box:

```bash
curl -fsSL https://get.docker.com | sh
```

## 2. Upload and configure

```bash
scp cathena-<date>.tar.gz you@server:~
ssh you@server
mkdir cathena && tar -xzf cathena-*.tar.gz -C cathena && cd cathena
cp .env.example .env && nano .env
```

Set at least:

| Setting | Value |
|---|---|
| `DOMAIN` | `app.cathena.xyz` (your domain) |
| `HL_NETWORK` | `mainnet` |
| `HL_ACCOUNT_ADDRESS` | your wallet: the owner, who sees Admin |
| `BUILDER_ADDRESS` | `0x9a3ce1C68A9798D812Adf47206799b5F6D98a3Cc` |
| `INVITE_CODES` | e.g. `colosseum-k7q2:25:2026-11-30` (hard to guess, with an end date) |
| `LIVE_TRADING` | `false` until your own first run looks right, then `true` |

Leave `HL_AGENT_PRIVATE_KEY` empty: every user, you included, gets a trading key through Set up.

## 3. Start

```bash
docker compose up -d --build
docker compose logs -f app        # wait for "Cathena Convex ready"
```

Open `https://<DOMAIN>`. The certificate is issued on first visit and can take up to a minute.

### Already running nginx on 80/443?

Skip Caddy: start only the app on `127.0.0.1:8000` and add a site to your nginx.

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.nginx.yml up -d --build app
sudo cp deploy/nginx-cathena.conf /etc/nginx/sites-available/cathena     # set server_name to your DOMAIN
sudo ln -s /etc/nginx/sites-available/cathena /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d <DOMAIN>                                          # https
```

`DOMAIN` in `.env` is only used by Caddy, so you can leave it empty here. Cookies stay secure because the app is told
it's behind https (`X-Forwarded-Proto`).

### Without Docker: systemd user services (a shared server)

Everything stays in `~/cathena-sleeve`: uv, Python, the app, its data and Caddy. No root except two one-time commands.

```bash
cd ~/cathena-sleeve && mkdir -p .tools
curl -fsSL https://github.com/astral-sh/uv/releases/download/0.11.6/uv-x86_64-unknown-linux-gnu.tar.gz | tar -xz -C .tools --strip-components=1
UV_CACHE_DIR=$PWD/.cache/uv UV_PYTHON_INSTALL_DIR=$PWD/.tools/python .tools/uv sync --frozen --no-dev
cp .env.example .env && chmod 600 .env && nano .env
systemctl --user link ~/cathena-sleeve/deploy/cathena.service && systemctl --user enable --now cathena
sudo loginctl enable-linger $USER                                          # root, once: keep running after logout
```

https with Caddy (needs `DOMAIN` in `.env`, DNS pointing here, ports 80/443 open in the cloud firewall):

```bash
# put the caddy binary in .tools (see the GitHub releases), then, as root once:
sudo setcap cap_net_bind_service=+ep ~/cathena-sleeve/.tools/caddy           # redo after replacing the binary
systemctl --user link ~/cathena-sleeve/deploy/caddy.service && systemctl --user enable --now caddy
```

Logs: `journalctl --user -u cathena -f` · restart after editing `.env`: `systemctl --user restart cathena`.

## 4. Your first run (before inviting anyone)

1. Open the app, **Connect wallet**, accept the terms, deposit $5–10, approve the trading key, fund both legs.
2. With `LIVE_TRADING=false`, place a few trades: they're simulated against the live order book.
3. Set `LIVE_TRADING=true`, then `docker compose up -d` to apply. Place one small live trade, cash it out, and withdraw.
4. Desktop → **Admin** → **Copy link** for your invite code.

## 5. Day to day

| Task | Command |
|---|---|
| Logs | `docker compose logs -f app` |
| Change settings | edit `.env`, then `docker compose up -d` |
| Update the code | upload a new package, extract over the folder, `docker compose up -d --build` |
| Health | `curl https://<DOMAIN>/v1/health` |
| After the hackathon | Admin → **Wind down cohort**, or let the invite end date pass |

## 6. Back up the data volume

The `cathena-data` volume holds users, positions, the encrypted trading keys and the two secrets that unlock them.
Lose it and every user has to approve a new trading key. Their funds are never at risk: those stay in their own accounts.

```bash
docker compose exec app tar -czf - -C /data . > cathena-data-$(date +%F).tar.gz      # back up
docker compose cp ./restore/. app:/data && docker compose restart app                 # restore (from an extracted backup)
```

Store backups somewhere private: they contain encrypted keys plus the secret that decrypts them.

## Links to share

| What | URL |
|---|---|
| App (demo for anyone, no wallet needed) | `https://<DOMAIN>/` |
| Invite link (create an account and trade) | `https://<DOMAIN>/?invite=<code>` |
| Videos page | `https://<DOMAIN>/watch` (add `?invite=<code>` so its button carries the invite) |
| Teaser 16:9 | `https://<DOMAIN>/media/cathena-teaser-16x9.mp4` |
| Bar talk 9:16 | `https://<DOMAIN>/media/cathena-bar-talk-9x16.mp4` |
| Club chat 9:16 | `https://<DOMAIN>/media/cathena-club-chat-9x16.mp4` |

For X and Telegram, upload the mp4 files directly rather than linking: they autoplay inline that way.
