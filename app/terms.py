"""Early-access terms every user accepts at sign-in. The version and a hash of this text go into the message their
wallet signs, so acceptance is provable. Change the text → bump TERMS_VERSION (users accept again on next sign-in).

A starting draft, not legal advice: have it reviewed before opening to the public."""

import hashlib

TERMS_VERSION = "1"
TERMS = [
    ("Experimental software", "Cathena is an early-access prototype. It may have bugs, fail, or stop working without notice."),
    ("Real money, real losses", "Trades use real USDC on Hyperliquid. You can lose up to the max loss shown on each trade, and up to everything you deposit. "
     "Prices, fills and settlement are set by Hyperliquid, not Cathena."),
    ("Your account, your funds", "Your funds stay in your own Hyperliquid account. Cathena's trading key can open and close trades for you but can never withdraw. "
     "You are responsible for your wallet and its keys."),
    ("Limits and access", "Early access caps deposits in the app, the stake per trade and the total you can have at risk. Cathena may pause your account, "
     "close your positions, or end access (for example after a hackathon). You can always withdraw what's in your account."),
    ("Fees", "Cashing out early carries a Cathena fee of up to 0.5%, on top of Hyperliquid's own trading and withdrawal fees."),
    ("Not advice", "Nothing in Cathena is investment, financial, legal or tax advice."),
    ("Eligibility", "You are 18 or older, you are not a resident of or located in the United States or any country where these products are restricted "
     "or sanctioned, and you are not using a VPN or similar to get around this."),
    ("No warranty", "Cathena is provided as is, without warranties. To the fullest extent the law allows, Cathena and its contributors are not liable "
     "for any loss. You use it at your own risk."),
]
TERMS_HASH = hashlib.sha256("\n".join(f"{t}: {b}" for t, b in TERMS).encode()).hexdigest()[:16]
ACCEPT_LINE = f"I accept the Cathena early-access terms v{TERMS_VERSION} ({TERMS_HASH})."
