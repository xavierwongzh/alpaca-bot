# Alpaca Paper Dashboard

A read-only web dashboard for the Alpaca **paper** trading account, deployable
to Vercel. It lives alongside the Python bot but is fully independent — it never
touches the bot's code and only **reads** from Alpaca.

> **Security model:** the Alpaca secret key never reaches the browser. The
> client only ever calls this app's own `/api/*` routes; those server-side route
> handlers read the keys from `process.env` and call Alpaca. No secret is ever
> sent to the client, and no `NEXT_PUBLIC_` prefix is used for any key.

## Stack

- Next.js 14 (App Router, TypeScript)
- Tailwind CSS (dark theme, responsive)
- Recharts (equity curve)
- No database — Alpaca is the only data source

## What it shows

- **Header cards:** total equity, day P&L ($ and %, from `equity` vs
  `last_equity`), buying power, cash.
- **Equity curve:** line/area chart from Alpaca portfolio history.
- **Open positions:** symbol, qty, avg entry, current price, market value,
  unrealized P&L ($ and %), green/red colored.
- **Recent orders:** symbol, side, type, qty, status, filled avg price,
  submitted time — bracket legs (entry / stop / target) are nested and tagged.
- **Auto-refresh:** the client polls every 60s with a subtle "last updated"
  timestamp, plus per-section loading and error states.
- **Performance tab:** strategy equity curve overlaid with SPY/QQQ buy-and-hold
  (normalized), metric cards (total return, excess vs QQQ, win rate, profit factor,
  max drawdown), per-tag breakdown tables (signal type, run mode, sector, confidence
  bucket), and a confidence-calibration table. Low-sample cells are de-emphasized and
  flagged so small samples aren't read as signal. Reads `/evaluation.json`.
- **Trade drill-down:** click any position or order row to open a modal with the
  bot's stored reasoning — rationale, confidence, the options-flow signal, the
  technicals at decision time, the VIX/market context, the chosen stop/target,
  run mode, model, and timestamp. Orders join to records by `client_order_id`
  (exact); positions join by symbol to the most recent `open`-mode entry. If no
  record exists (manual/older trade) the modal shows "No decision record for this
  trade." Records are loaded from `/decisions.json` (published by CI), tolerating
  a missing file.

## API routes (server-side proxies)

| Route | Alpaca endpoint |
|-------|-----------------|
| `GET /api/account` | `/v2/account` |
| `GET /api/positions` | `/v2/positions` |
| `GET /api/orders` | `/v2/orders?status=all&nested=true` (includes bracket legs) |
| `GET /api/portfolio-history` | `/v2/account/portfolio/history` |

Each returns `{ status: "ok", data }` or `{ status: "error", error }` and never
includes credentials.

## Run locally

```bash
cd dashboard
npm install
cp .env.local.example .env.local   # then edit with your PAPER keys
npm run dev
```

Open http://localhost:3000.

### Environment variables (server-side only)

| Var | Description |
|-----|-------------|
| `ALPACA_API_KEY` | Alpaca paper API key |
| `ALPACA_SECRET_KEY` | Alpaca paper secret |
| `ALPACA_BASE_URL` | Defaults to `https://paper-api.alpaca.markets` |

`.env.local` is git-ignored — never commit it.

## Deploy to Vercel

1. Push this repo to GitHub.
2. In Vercel, **Add New… → Project** and import the repo.
3. **Important:** set the project **Root Directory** to `dashboard` (this app is
   a subfolder of the bot repo).
4. Under **Settings → Environment Variables**, add the three variables above
   (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`). These live in
   Vercel project settings — **never commit them**.
5. Deploy. Vercel auto-detects Next.js; no extra build config needed.

> The route handlers are marked `dynamic = "force-dynamic"`, so responses are
> always fresh and never statically cached.

## Published artifacts

The GitHub Actions run copies these into `dashboard/public/` each run (no DB):

- `decisions.json` — cumulative decision records (last 500) for the drill-down.
- `latest-snapshot.json` — most recent portfolio snapshot.
- `flow-cache.json` — latest options-flow scan.
- `evaluation.json` — strategy metrics, benchmarks, breakdowns, calibration.

## Optional (not built yet)

Surface cumulative spend (from `data/cost_log.csv`) as a small card on the
dashboard so cost is visible alongside performance.
