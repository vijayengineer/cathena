# Cathena: notes for Claude Code

BTC options made simple on Hyperliquid: one slider (Conservative → Bold → Aggressive → Moonshot, bear ↔ bull) builds a position from the
HIP-4 daily BTC binary plus a perp-replicated "synthetic option". Max loss is fixed upfront (20–50% of stake), with
early cash-out for a Cathena builder fee. USDC only. FastAPI backend, single-file vanilla-JS front end (`web/index.html`).

## Map

- `app/main.py` – API, per-user books, sign-in, set-up (deposit / approveAgent / approveBuilderFee / usdClassTransfer / withdraw3), admin
- `app/book.py` – open, rebalance, cash out, settle; open-risk cap; `fee_bps()` only charges when the builder approval covers it
- `app/engine/` – market snapshot, pricing, quote (`quote.py` must stay in parity with the JS `quote()`; tests check it)
- `app/hl/account.py` – EIP-712 user-signed actions built for the browser wallet; tests prove signatures match the SDK
- `app/users.py` – users.json, encrypted agent keys (pending until approved), join rules, invite codes, `open_block`
- `app/terms.py` – early-access terms; version + hash go into the signed sign-in message
- `web/index.html` – mobile UI + desktop terminal (`#desk`, ≥1100px), set-up sheet, leaderboard, admin; `web/watch.html` – videos
- `docs/DEPLOY.md`, `docs/SUBMISSION.md`; `scripts/package.sh` builds the upload

## Run and test

`uv run pytest -q` (must pass) · local: `uv run uvicorn app.main:app --port 8000` · server: `docker compose up -d --build`
(or with `deploy/docker-compose.nginx.yml` behind an existing nginx). Exactly one worker: state lives in the process.

## Rules

- Never ask for, print or handle anyone's private key or seed phrase. Users sign in their own wallet; agent keys are
  generated server-side, encrypted, and never returned by the API.
- The trading agent can't withdraw by design. Never add code that moves user funds without the user's own wallet signature.
- Don't restart/stop the production app, prune Docker volumes, or touch the `cathena-data` volume (keys + secrets)
  without asking first. Back it up before risky changes.
- `LIVE_TRADING=true` sends real orders. Test changes in dry run first.
- Early-access limits (stake, open risk, deposit, invite end dates) are product requirements: don't loosen silently.

## Product preferences (from the owner)

- Desktop: one bipolar slider only (no extra Bull/Bear or zone buttons), Setups and Live tabs, plain BTC candle chart with
  level overlays (no forecast cone), fomo.family-like premium look (inky violet-black, Geist, colourful avatars/icons).
- Keep copy short and plain; zones are Conservative / Bold / Aggressive / Moonshot (all in; placing it asks "Degen mode" first); tagline "Options, made easy."
- Privy onboarding is the next milestone (email/social login + embedded wallet replacing browser-wallet sign-in).
