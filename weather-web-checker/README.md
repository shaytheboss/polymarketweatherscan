# Polymarket Weather Checker

Compare **ECMWF IFS** (physics model) and **GFS-GraphCast** (AI model) temperature forecasts against Polymarket weather markets — in your browser, one click.

## What it does

| Feature | Detail |
|---|---|
| **Model 1** | ECMWF IFS 0.25° — European Centre for Medium-Range Weather Forecasts |
| **Model 2** | GFS-GraphCast (NOAA × Google DeepMind AI) — falls back to ECMWF AIFS if unavailable |
| **Forecast source** | [Open-Meteo](https://open-meteo.com/) — free, no API key |
| **Market source** | Polymarket Gamma API — live YES prices |
| **Edge calculation** | Gaussian probability model (σ = 2.5°C) vs market price → edge in percentage points |

## How to use

**Tab 1 — By City:**  Enter any city or airport name + date → see forecasts + all matching Polymarket markets for that city.

**Tab 2 — By Polymarket URL:**  Paste a `https://polymarket.com/event/...` URL → city and date extracted automatically → forecasts fetched and compared.

## Output

- Max/min temperature from each model in °C and °F
- Consensus (average of available models)
- Per-market edge:
  - `+10pp+` → strong BUY YES signal
  - `-10pp-` → strong BUY NO signal
  - Bucket parsed from question (shown in small text under each market)

## Deploy to GitHub Pages

1. Create a new GitHub repository (e.g. `polymarketweatherassistwebpage`)
2. Upload all files from this folder to the repo root
3. Go to **Settings → Pages → Source → Deploy from branch → main → / (root)**
4. Your site will be live at `https://yourusername.github.io/repo-name/`

No build step, no dependencies, no server.

## Run tests locally

Open `tests.html` in a browser (via a local server, e.g. `python -m http.server 8080`):

```
cd weather-web-checker
python -m http.server 8080
# Open http://localhost:8080/tests.html
```

All 30+ unit tests for the pure parsing and probability functions will run automatically.

## Notes

- **GraphCast on Open-Meteo:** The primary model ID tried is `gfs_graphcast` (NOAA's operational GraphCast run). If Open-Meteo doesn't yet serve this model, the fallback is `ecmwf_aifs025_single` (ECMWF's AI model with identical graph-neural-network architecture). The label in the UI shows which model was actually used.
- **Forecast horizon:** Open-Meteo provides up to 16 days ahead.
- **Polymarket CORS:** Polymarket's Gamma API allows browser requests. If you see CORS errors, you may need to proxy through a Cloudflare Worker (see the tennis bot's cloudflare-worker.js for reference).
