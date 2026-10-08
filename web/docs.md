## Introduction

Cathena lets you take a view on where Bitcoin will be tomorrow morning with one slider, and shows the most you can lose before you tap.

**In 30 seconds**

- **Pick a side.** Slide left if you think BTC will fall, right if you think it will rise.
- **Pick how bold.** Conservative risks the least. Aggressive pays the most on a big move. Moonshot risks your whole stake for the biggest payout, and asks you to confirm "Degen mode" first.
- **Pick a stake** in USDC. The app shows your maximum loss and what you'd make if BTC moves 3%.
- **Wait or cash out.** Every position settles at 06:00 UTC, and you can cash out at the live price any time before that.

**An example.** On 7 October, a $100 Bold bet that BTC would rise risked at most $35. If BTC finished 3% higher by 06:00, it paid about +117%, or $117. If BTC fell instead, the most it could lose was $35, and the other $65 was never at risk. Prices change all day, so the app always shows the current figures before you stake.

**Why we built it.** Memecoin trading is fast and fun, but it comes with bundled supply, rug pulls and losses with no floor. Cathena keeps the same one-tap feel and applies it to Bitcoin, the deepest and most transparent market in crypto, with a daily upside and a loss cap you choose. Behind the slider sit options and perpetual futures on Hyperliquid: premiums, strikes and margin are handled for you.

**The risks.** This is real money and early-access software. You can lose up to the maximum shown on every trade, and up to everything you deposit. Your funds stay in your own Hyperliquid account, and Cathena can never withdraw them.

**What's next.** Bitcoin is the first market. The same slider can work for other crypto assets and, later, stocks.

## Using Cathena, step by step

From first visit to cash-out takes about two minutes. These screens are from a phone; the desktop layout has the same steps.

<div class="flow">
<figure><img src="/assets/flow/01-demo.jpg" alt="Demo mode with live prices" loading="lazy"><figcaption><b>1. Try it with paper money.</b> Open cathena.rndm.io and you start in demo mode: live prices and paper money, so you can play with the slider before connecting anything.</figcaption></figure>
<figure><img src="/assets/flow/02-connect.jpg" alt="Connect to Cathena" loading="lazy"><figcaption><b>2. Connect your wallet.</b> Tap Connect wallet and pick one of the wallets installed on your device. On a phone without a wallet, Cathena offers to open itself inside MetaMask, Coinbase Wallet or Trust.</figcaption></figure>
<figure><img src="/assets/flow/03-sign.jpg" alt="Sign to verify" loading="lazy"><figcaption><b>3. Sign to verify.</b> Your wallet asks you to sign one message. It costs nothing, can't move funds, and records that you accept the early-access terms.</figcaption></figure>
<figure><img src="/assets/flow/04-setup.jpg" alt="Set up steps" loading="lazy"><figcaption><b>4. Set up your account.</b> Four one-time steps, each one signature in your wallet: deposit USDC from Arbitrum ($5 per deposit, $10 in total to trade), approve your trading key, allow the cash-out fee, and move part of your deposit to the binary's balance.</figcaption></figure>
<figure><img src="/assets/flow/05-dial.jpg" alt="The slider" loading="lazy"><figcaption><b>5. Choose your view.</b> Drag the slider: left for bearish, right for bullish, further out for bolder. The heading shows where you are and your max loss as a share of your stake. Pick a stake underneath.</figcaption></figure>
<figure><img src="/assets/flow/06-payoff.jpg" alt="Payoff before you stake" loading="lazy"><figcaption><b>6. Check the payoff.</b> Before you stake you see what you'd make if BTC moves 3%, as a percentage and in dollars, plus your max loss and the part that's never at risk. Tap −2%, Now or +3% under the chart to see other outcomes.</figcaption></figure>
<figure><img src="/assets/flow/07-staked.jpg" alt="Staked" loading="lazy"><figcaption><b>7. Slide to stake.</b> Slide the green button to the end. The confirmation shows exactly what was bought: the binary contracts, the synthetic option and your max loss.</figcaption></figure>
<figure><img src="/assets/flow/08-position.jpg" alt="Live position" loading="lazy"><figcaption><b>8. Watch it live.</b> The Positions tab shows what you'd get back if you cashed out now, updated every few seconds, and what each leg is doing.</figcaption></figure>
<figure><img src="/assets/flow/09-cashout.jpg" alt="Cash out" loading="lazy"><figcaption><b>9. Cash out, or hold to 06:00.</b> Tap Cash out to see exactly what you'd get back after fees, then slide to confirm. Or keep it: at 06:00 UTC it settles on its own and any winnings land in your Hyperliquid account.</figcaption></figure>
</div>

**10. Withdraw.** On a phone go to Account, then Manage; on desktop open Deposit. Choose Withdraw to wallet: your wallet signs it, and your USDC goes back to your address on Arbitrum, less Hyperliquid's $1 fee.

## How it works

Cathena turns a view on BTC into an options position with one slider. You pick bearish or bullish and how bold, choose a stake in USDC, and see your maximum loss before you tap. Every position settles at 06:00 UTC, and you can cash out at any time before that.

Under the hood each position is built on Hyperliquid from two parts: the HIP-4 daily BTC binary and a synthetic option that Cathena runs on the BTC perp. This page explains how those parts are priced, sized and managed, down to the formulas and constants in the code.

## The dial

The dial runs from −100 (max bearish) to +100 (max bullish); its distance from the centre sets how much of your stake can be lost and what the position leans on. Left of centre the position profits if BTC falls, right of centre if it rises.

| Zone | Dial (either side) | Max loss, % of stake | Leans on | You get |
| --- | --- | --- | --- | --- |
| Conservative | 1–33 | 20–30% | Mostly the binary | Smallest risk, capped win |
| Bold | 34–66 | 30–40% | Binary and synthetic option | Bigger payoff if you're right |
| Aggressive | 67–94 | 40–50% | Mostly the synthetic option, strike further out | Max payoff on a big move |
| Moonshot | 95–100 | 50–100% | The binary, with the whole stake at 100 | Lose it all if wrong, biggest payout if right |

Max loss follows one rule, rounded to the nearest 5%: below 95 it is 20% + 30% × (dial ÷ 100); from 95 to 100 it climbs in steps of 10%, from 50% to 100%. Everything not at risk stays in your account and is used only as margin for the synthetic option.

## What a position is made of

Every position is a binary leg plus a synthetic option leg, and your stake splits three ways: the binary's cost, the synthetic option's budget, and cash that is never at risk.

**The binary leg.** Hyperliquid's HIP-4 market asks one question each day: will BTC be at or above today's line at 06:00 UTC? Yes and No contracts trade between $0 and $1 and pay $1 each to the winning side. Bulls buy Yes, bears buy No. The contract price is the market's own probability, so a Yes at $0.30 says a 30% chance.

**The synthetic option leg.** Hyperliquid has no listed BTC options, so Cathena builds one. A call (for bulls) or a put (for bears) is replicated by holding a BTC perp position equal to the option's delta, resized as the price moves. Holding it costs money over time, the way an option's premium does, so it runs on a fixed budget.

**How the dial mixes them.** The bolder the dial, the more of the available margin the synthetic option uses, rising smoothly from about 15% of the way out to almost all of it at 95% of the way out. The synthetic option's budget is capped at 60% of your max loss, and the binary gets the rest. At 100 on the dial no cash is left for margin, so the whole stake goes into the binary.

## Volatility

Cathena prices with the higher of two volatilities: what BTC has actually done over the last month, and what the HIP-4 binary's price implies for today.

**Realised volatility.** Every 10 minutes the backend pulls the last 32 daily BTC candles, takes the daily log returns between closes, and uses their standard deviation as the one-day volatility. It is annualised with 365 days, because crypto trades every day.

```latex
\sigma_{1d} = \operatorname{stdev}\left(\ln \frac{C_i}{C_{i-1}}\right), \qquad \sigma_{RV} = \sigma_{1d}\sqrt{365}
```

**Implied volatility from the binary.** The Yes contract's mid price p (best bid plus best ask, halved) is the market's probability that BTC finishes at or above the line K. With time to settlement T in years and no interest, that probability is N(d2), so the volatility the market is pricing is the σ that solves:

```latex
N(d_2) = p, \qquad d_2 = \frac{\ln(S/K) - \tfrac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}
```

The solver bisects σ between 2% and 300% for 80 steps. When BTC is below the line, d2 only rises with σ up to σ√T = √(2|ln S/K|), so the search stays on that rising branch; without that, the solver could return a wrong answer such as 300%.

**The one Cathena uses.** σ = max(σ_RV, σ_IV), or σ_RV alone when the binary has no usable price. A higher σ makes the synthetic option's premium, and so its budget, larger per unit, which means fewer units. Pricing with the higher figure never understates what the option will cost to run.

## Pricing and the Greeks

The synthetic option is priced with Black-Scholes at zero interest, using the σ above and the time left until 06:00 UTC; its delta then tells the perp how much BTC to hold.

```latex
d_1 = \frac{\ln(S/K) + \tfrac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}, \quad d_2 = d_1 - \sigma\sqrt{T}
```

```latex
\text{Call} = S\,N(d_1) - K\,N(d_2), \qquad \text{Put} = K\,N(-d_2) - S\,N(-d_1)
```

The same model prices the binary: a Yes contract is worth N(d2), the chance BTC finishes at or above the line.

| Greek | What it measures | What it means for your position |
| --- | --- | --- |
| Delta | Change in option value per $1 move in BTC: N(d1) for a call, N(d1) − 1 for a put | The perp holds units × delta BTC: long for a call, short for a put. This is the number the controller tracks. |
| Gamma | How fast delta changes as BTC moves: φ(d1) ÷ (Sσ√T) | High gamma means frequent rebalancing. Replication buys as BTC rises and sells as it falls, and that steady small loss is how the option's cost is paid. It is highest near the strike and near 06:00. |
| Theta | Value lost as time passes with BTC unchanged | Shows up as the synthetic budget being used up through rebalancing. The binary drifts towards $0 or $1 as 06:00 nears. |
| Vega | Value change per point of volatility | Pricing at the higher σ sets a bigger budget per unit. If BTC moves less than σ implied, replication costs less than budgeted and the difference is yours. If it moves more, the budget stop caps the loss. |

Cathena assumes running the synthetic option costs 1.2 × its fair premium once trading fees and whipsaw are included, a figure taken from backtests. The payoff estimates in the app use that 1.2.

## Building a quote

A quote turns the dial value and your stake into a strike, a synthetic size, a budget and a number of binary contracts. The same calculation runs in the page as you drag and on the server when you stake, and the server refuses the trade if its max loss differs from yours by more than 5%.

1. **Max loss.** M = stake × max-loss % from the dial.
2. **How much synthetic.** A weight w rises smoothly from 0 at 15% of the way out to 1 at 95% (a smoothstep curve).
3. **Strike.** The option targets a delta of 0.5 up to 20% of the way out, falling in a straight line to 0.25 at the end: bolder means further out of the money. The strike is the price where Black-Scholes gives that delta, rounded to $250:

    ```latex
    K = S \cdot e^{-\sigma\sqrt{T}\,z + \frac{1}{2}\sigma^2 T}, \qquad z = N^{-1}(\Delta_{target})\ \text{(calls)},\ N^{-1}(1-\Delta_{target})\ \text{(puts)}
    ```

4. **Price.** Fair premium f and delta per 1 BTC of option, from Black-Scholes at K.
5. **Size.** The never-at-risk cash (stake − M) backs the perp at up to 20× leverage, measured at 80% delta, which caps the option size at 20 × (stake − M) ÷ (0.8 × S) BTC. The size is that cap times w, cut back if its budget would pass 60% of M, then rounded down to 0.00001 BTC.
6. **Budget.** Synthetic budget = 1.3 × size × f. The 1.3 cushion leaves room above the expected 1.2 × cost.
7. **Minimums.** A synthetic leg worth less than $10 of perp (size × delta × S) is dropped, since Hyperliquid's minimum order is $10. The same applies to a binary leg under $10.
8. **Binary.** M minus the budget buys whole Yes (bull) or No (bear) contracts by walking the order book level by level. The order is sent immediate-or-cancel, limited to the worst level reached plus $0.002.
9. **Max loss shown.** Binary cost + synthetic budget. This is the figure you see before you slide to stake.

On opening, the server also checks your Hyperliquid balances (live trading only), sets isolated leverage to the lowest of the exchange maximum, 20×, and what the size needs (never below 3×), buys the binary, then places the first perp order.

## Running the synthetic option

A controller checks every open position every 3 seconds and keeps its perp at the option's delta until the budget runs out or settlement is 5 minutes away.

![Every 3 seconds, each open position takes exactly one path](/assets/docs-controller-loop.png)

- **Target.** units × delta, recomputed from the current price, the strike, the time left and the σ fixed when the position opened. Long for a call, short for a put.
- **Band.** Trades only when the perp is off target by more than 5% of the option size, and the trade is worth at least $10. Smaller drifts wait, which saves fees.
- **Orders.** Immediate-or-cancel, priced 0.1% through the best bid or ask so they fill now or not at all. Each fill updates an average-cost record of the perp: size, entry price, realised P&L and fees (0.045% taker).
- **Budget stop.** When the synthetic leg's P&L reaches minus its budget, the perp is closed. This is what keeps the loss at the number you were shown.
- **Before settlement.** Five minutes before 06:00 UTC the perp is closed, so nothing is left running into the binary's settlement.
- **Isolated margin.** Each position's perp sits in isolated margin, so even a sudden gap can't use more than the margin posted for that position.

## Payoff

The figures in the app are what the position returns if BTC ends at a given price at 06:00, with the synthetic leg's running cost estimated at 1.2 × its fair premium.

```latex
\text{P\&L}(X) = \underbrace{n\cdot\mathbb{1}[\text{your side of the line}] - \text{binary cost}}_{\text{binary}} + \underbrace{\max\left(u\cdot\text{intrinsic}(X) - 1.2\,u f,\ -\text{budget}\right)}_{\text{synthetic option}}
```

Here n is the number of binary contracts, u the option size in BTC, f its fair premium, and intrinsic(X) = max(X − K, 0) for a call or max(K − X, 0) for a put.

- **"If BTC is up 3% by 06:00".** P&L(X) at today's price × 1.03 for bulls, or × 0.97 for bears, shown as a percentage of your stake. The Setups list shows the same figure for each setup.
- **Breakeven.** The price where P&L(X) crosses zero, drawn as a dashed line on the chart.
- **Never at risk.** Stake minus max loss. It is $0 at Moonshot.

These are estimates. The synthetic leg's real result depends on the path BTC takes, since every rebalance trades at that moment's price, so it can end better or worse than estimated. It can't lose more than its budget, apart from a sudden gap between two checks.

## Cash out, fees and settlement

You can cash out any time before 06:00 UTC at the live value shown, which already has every fee taken off; hold to 06:00 and there is no Cathena fee at all.

**Cashing out early.** The binary is sold at the best bid, with an immediate-or-cancel limit $0.01 below it. The perp is closed at the best price on the other side. The value shown while you slide is:

```latex
\text{net} = n \cdot \text{bid} - \text{binary cost} + \text{synthetic P\&L} - \text{perp taker fee} - \text{Cathena fee}
```

You get back what you had at risk plus that net, never less than zero.

| Fee | When | Rate |
| --- | --- | --- |
| Hyperliquid perp taker fee | Every perp fill: rebalances, closes | 0.045% of the amount traded (base tier) |
| Cathena fee, binary | Cashing out early | 0.5% of what the binary sells for |
| Cathena fee, perp | Cashing out early | 0.05% of the perp amount closed |

The Cathena fee is charged on-chain as a Hyperliquid builder fee, paid to Cathena's builder address on the closing orders. It is only charged when your one-time approval in Set up covers it, and Hyperliquid caps builder fees at 1% on outcomes and 0.1% on perps.

**Settlement.** The perp is closed 5 minutes before 06:00 UTC. At 06:00 Hyperliquid settles the binary: each winning contract pays $1 into your account, and losing ones pay nothing. Cathena records the result from BTC's one-minute candle at the settlement time.

## Your account and safety

Your money stays in your own Hyperliquid account the whole time, and Cathena can trade it but never withdraw it.

1. **Sign in.** Your wallet signs a message that proves it's yours and records that you accept the early-access terms. It costs no gas and can't move funds. Cathena keeps the signed message as the record of acceptance.
2. **Deposit.** USDC goes from your wallet on Arbitrum to Hyperliquid's bridge, which credits your Hyperliquid account. Each deposit must be at least $5, because the bridge loses anything smaller, and you need $10 in total to place a trade, Hyperliquid's smallest order. You also need a little ETH on Arbitrum (a few cents) for the transfer's network fee.
3. **Trading key.** Cathena creates a key just for you and stores it encrypted. Your wallet approves it once on Hyperliquid as an agent. Hyperliquid lets an agent place and cancel orders, but never withdraw or transfer.
4. **Fee approval.** Your wallet approves the Cathena builder fee once, up to 0.5%.
5. **Fund both legs.** Deposits land in your perp balance, but the binary is bought with spot USDC. Your wallet signs a transfer that moves part of it to spot.

**Withdrawing.** Set up → Withdraw to wallet. Your own wallet signs it, and the USDC goes back to the same address on Arbitrum, less Hyperliquid's $1 fee.

**If Cathena is offline.** Open app.hyperliquid.xyz with the same wallet: your positions and USDC are there, and you can close or withdraw directly. The binary settles at 06:00 on its own, and the perp sits in isolated margin.

**Early-access limits.** $20 per trade, at most $20 at risk across your open trades, and deposits of $5–20 in the app ($10 in total to trade). From $10 every setting except Moonshot can trade: below roughly $30 the position runs as the option alone, because the daily binary's smallest order is $10. At that size the option is sized by margin on your whole stake, and bolder settings use a deeper strike, so they risk more and pay more on any rise. Moonshot stays all-binary and starts at about $11. Accounts are limited in number, and an invite link may have an end date; after it you can still cash out and withdraw.

## Engine constants

Every number the engine runs on, as set in `app/engine/quote.py`, `app/book.py` and `app/engine/market.py`.

| Constant | Value | What it does |
| --- | --- | --- |
| ALL_IN | 95 | Dial distance where Moonshot starts: max loss climbs from 50% to 100% |
| CUSHION | 1.3 | Synthetic budget = 1.3 × fair premium × size |
| EXP_COST | 1.2 | Expected cost of running the synthetic, as a multiple of fair premium (fees and whipsaw) |
| LEV_CAP | 20× | Most leverage the never-at-risk cash backs |
| DELTA_CAP | 0.8 | Delta at which that leverage cap is measured |
| Synthetic share cap | 60% of max loss | Most of the max loss the synthetic budget may take |
| Target delta | 0.5 → 0.25 | Option delta at the mild end and at the far end of the dial |
| Small-stake target delta | 0.30 → 0.75 | Under ~$30 (option only): bolder = deeper in the money, so risk and payoff rise together |
| STRIKE_STEP | $250 | Strikes are rounded to this |
| MIN_NOTIONAL | $10 | Smallest order Hyperliquid accepts, for both legs |
| BAND | 5% of size | Perp drift tolerated before a rebalance |
| TICK | 3 s | How often the controller checks each position |
| PERP_SLIP | 0.1% | How far through the best price a perp order may fill |
| BIN_SLIP | $0.01 | How far under the bid a cash-out may sell the binary |
| CLOSE_BEFORE | 5 min | Perp closed this long before 06:00 UTC |
| MAX_QUOTE_DRIFT | 5% | Largest allowed gap between your quote and the server's |
| Perp taker fee | 0.045% | Hyperliquid base-tier fee used in all estimates |
| Volatility window | 32 daily candles, refreshed every 10 min | Realised volatility input |
