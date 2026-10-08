#!/usr/bin/env bash
# Export the static front end for Vercel into ./frontend (its own git repo → github.com/RNDMFrontend/cathena).
#   scripts/build_frontend.sh api.cathena.xyz
# Vercel serves the page; /v1, /media and /watch are proxied to the backend so the sign-in cookie stays first-party.
set -euo pipefail
cd "$(dirname "$0")/.."
API="${1:?usage: build_frontend.sh <api domain, e.g. api.cathena.xyz | - to skip vercel.json>}"
# refuse to ship a page whose script doesn't parse (one syntax error blanks the whole app)
python3 - <<'PY'
import re, subprocess, sys, tempfile
for i, s in enumerate(re.findall(r"<script>(.*?)</script>", open("web/index.html").read(), re.S)):
    f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False); f.write(s); f.close()
    r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"web/index.html script {i} has a syntax error:\n{r.stderr}")
PY
mkdir -p frontend
# replace everything except the git history
find frontend -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp web/index.html frontend/index.html
cp -R web/assets frontend/assets
[ "$API" = "-" ] || cat > frontend/vercel.json <<JSON
{
  "\$schema": "https://openapi.vercel.sh/vercel.json",
  "rewrites": [
    { "source": "/v1/:path*", "destination": "https://${API}/v1/:path*" },
    { "source": "/media/:path*", "destination": "https://${API}/media/:path*" },
    { "source": "/watch", "destination": "https://${API}/watch" },
    { "source": "/buildercode", "destination": "https://${API}/buildercode" },
    { "source": "/docs", "destination": "https://${API}/docs" },
    { "source": "/docs.md", "destination": "https://${API}/docs.md" }
  ],
  "headers": [
    { "source": "/(.*)", "headers": [
      { "key": "X-Content-Type-Options", "value": "nosniff" },
      { "key": "Referrer-Policy", "value": "strict-origin-when-cross-origin" },
      { "key": "Strict-Transport-Security", "value": "max-age=31536000" }
    ] },
    { "source": "/", "headers": [{ "key": "Cache-Control", "value": "no-cache" }] }
  ]
}
JSON
WHERE=$([ "$API" = "-" ] && echo "the Cathena backend" || echo "the Cathena backend at \`https://${API}\`")
cat > frontend/README.md <<MD
# Cathena · front end

Options, made easy. One slider, max loss shown upfront, cash out any time, on Hyperliquid.

Static site for Vercel. The API, videos and \`/watch\` page are served by ${WHERE}
and proxied through Vercel (see \`vercel.json\`, added once the API domain is set), so everything runs on one domain.

- \`index.html\` – the app (mobile + desktop)
- \`assets/\` – logo and favicon

Deploy: import this repo in Vercel (framework preset: Other, no build command), then add your domain.
Generated from the main project with \`scripts/build_frontend.sh\`; edit there, not here.
MD
printf '.DS_Store\n.vercel\n' > frontend/.gitignore
ls -la frontend | awk 'NR>1{print $5, $9}'
