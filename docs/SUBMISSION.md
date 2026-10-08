# Cathena · hackathon submission

Colosseum · Hyperliquid track. Paste the section below into the submission form.

---

**Cathena: options, made easy.**

One slider turns a view on BTC into an options position on Hyperliquid. Slide left for bearish, right for bullish,
from Conservative to Moonshot. You see the maximum loss before you trade, and you can cash out any time.

- **Try it:** https://cathena.rndm.io/?invite=colosseum
  Connect a wallet, deposit $5–$20 USDC from Arbitrum, approve two signatures, and trade from $10. Without a wallet,
  the same page works as a live-price demo.
- **Watch:** https://cathena.rndm.io/watch?invite=colosseum (20-second teaser plus a 9:16 short)
- **Teaser video:** https://cathena.rndm.io/media/cathena-teaser-16x9.mp4
- **Docs and technical deep dive:** https://cathena.rndm.io/docs

### How it works

- Each position pairs Hyperliquid's **HIP-4 daily BTC binary** with a **synthetic option**: a BTC perp held at the
  option's delta and rebalanced every few seconds. Pricing is Black-Scholes, with implied volatility read from the
  binary itself and hedged at the higher of realised and implied volatility.
- **Max loss is fixed before you trade**: 20–50% of the stake from Conservative to Aggressive. **Moonshot** puts the
  whole stake on today's line, which can lose 100%, and asks the user to confirm "Degen mode" first. It is hidden on a
  side where BTC is already well past the line, because it would pay too little there.
- Binary orders fill level by level against a fresh order book, with at most 0.5% slippage.
- Positions **settle automatically at 06:00 UTC**. **Early cash-out** sells the binary at the bid and closes the perp.
  Cathena earns a Hyperliquid builder fee on early cash-outs only: 50 bps on the binary sale and 5 bps on the perp
  close. Holding to settlement costs nothing.
- **History** shows every position with its size, fill price and result.

### Proof

Live on Hyperliquid mainnet with real USDC. A $12 Bull Moonshot filled 182 Yes contracts at 6.53¢ and settled on its
own at 06:00 UTC. BTC finished at $82,637, below the $84,325 line, so it lost $11.88: exactly the max loss shown
before the trade.

### Safety

- Funds stay in each user's **own Hyperliquid account**. Cathena creates a per-user **agent key**, encrypted at rest,
  that can trade but **cannot withdraw**. The user's main key never leaves their wallet.
- Deposits, approvals and withdrawals are EIP-712 signatures from the user's own wallet. The builder fee only applies
  after the user approves it on-chain, capped at 0.5%.
- Early-access limits: deposits of $5–$20, $20 per trade, at most $20 at risk per user, and invite codes with an end
  date. Terms are accepted in the wallet-signed sign-in message.

### Stack

FastAPI (Python 3.12) · Hyperliquid API (HIP-4 outcomes, perps, agent wallets, builder codes) · vanilla JS front end on
Vercel · API on a VPS behind Caddy (systemd). The quote engine is identical in Python and JavaScript, and tests check
that the two agree.

### Team

Vijay L (@vijayln), solo founder and lead developer, building under RNDM.

### For judges

The invite `colosseum` allows 25 accounts until 30 November 2026 (end of day UTC). After that, existing accounts can
still cash out and withdraw.
