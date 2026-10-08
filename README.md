# Cathena: options, made easy

One slider for BTC options on Hyperliquid. Slide from bear to bull, from Conservative to Moonshot. Cathena builds the
position, shows your max payoff and max loss before you trade, and settles it at 06:00 UTC.

**Live:** https://cathena.rndm.io · **Docs and deep dive:** https://cathena.rndm.io/docs · **Video:** https://cathena.rndm.io/watch
Built for the Colosseum hackathon (Hyperliquid track) by [RNDM](https://rndm.io). Judges: see [docs/SUBMISSION.md](docs/SUBMISSION.md).

## How it works

Every position is two Hyperliquid legs:

- **HIP-4 daily BTC binary.** Pays $1 per contract if BTC is on your side of today's line at 06:00 UTC. It sets the
  direction and caps the loss.
- **Synthetic option.** A BTC perp held at an option's delta and rebalanced every few seconds. It adds convex upside past
  the line.

The slider sets the mix and how much of the stake is at risk: 20–50% from Conservative to Aggressive, or the whole stake
in Moonshot, after a "Degen mode" check. Pricing is Black-Scholes, with implied vol read from the binary's own price and
hedged at the higher of realised and implied vol. Leverage is capped at 20× and delta at 0.8. Stakes too small for the
binary's $10 minimum run entirely as the option. Moonshot is hidden on a side where BTC is already well past the line.

Binary orders fill level by level against a fresh order book with at most 0.5% slippage. Early cash-out sells the binary
at the bid and closes the perp. Cathena's fee is a user-approved Hyperliquid builder fee charged only on early cash-out
(50 bps binary, 5 bps perp). Holding to settlement costs nothing.

| Piece | File |
| --- | --- |
| Live market snapshot: daily binary, books, perp, volatility | `app/engine/market.py` |
| Slider to position engine (identical maths to the page's JS) | `app/engine/quote.py`, `app/engine/pricing.py` |
| Positions: open, fill, rebalance, budget stop, cash out, settle | `app/book.py` |
| Hyperliquid orders (live agent key or dry run) | `app/hl/venue.py` |
| EIP-712 user-signed actions built for the browser wallet | `app/hl/account.py` |
| Users, encrypted agent keys, invites, early-access rules | `app/users.py`, `app/auth.py`, `app/terms.py` |
| API | `app/main.py` |
| App, docs and video pages (single-file vanilla JS) | `web/index.html`, `web/docs.md`, `web/watch.html` |

## Safety

- Funds stay in each user's own Hyperliquid account. Sign-in, deposits, key approval, fee approval and withdrawals are
  all signed by the user's own wallet.
- Each user gets a server-generated **agent key**, encrypted at rest, that can trade but **cannot withdraw**. The API
  never returns it, and the user's main key never leaves their wallet.
- Early-access limits: $5–$20 deposits, $20 per trade, at most $20 at risk per user, and invite codes with end dates.
  Terms are accepted inside the signed sign-in message.

## Run it

```bash
uv sync
uv run uvicorn app.main:app --port 8000      # http://localhost:8000
uv run pytest -q                              # engine, accounting, signing and parity tests
```

With no `.env` it runs as a **dry run**: live Hyperliquid prices, with orders simulated against the live book and
nothing sent. Settings are listed in [.env.example](.env.example). `LIVE_TRADING=true` sends real orders. Deployment
notes are in [docs/DEPLOY.md](docs/DEPLOY.md). Production runs the API on a VPS behind Caddy (systemd), and the front end
on Vercel (`scripts/build_frontend.sh`).

## Tests

`tests/` covers:

- the pricing engine against numbers from the page's JavaScript (Python/JS parity)
- risk caps, minimums and small-stake behaviour
- Moonshot rules
- binary fills with slippage and retries
- invites and access windows
- EIP-712 onboarding signatures, checked against the Hyperliquid Python SDK

## Roadmap

Email login (Privy embedded wallets), ETH, SOL and HYPE dailies (HIP-4 lists all four), real options via Derive RFQ,
and a daily market brief.
