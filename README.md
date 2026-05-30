# CryptoLens Enhanced

CryptoLens Enhanced is a live crypto dashboard built in Python and HTML/JavaScript. It shows coin prices, sentiment, ETF flow, options open interest, BTC dominance, and a MyBuddy assistant in one frame.

## Features

- Live coin list with search and pagination.
- Real-time selected-coin snapshot in USD and INR.
- Price chart with SMA20, SMA50, and EMA20 overlays.
- Fear & Greed index with improved bearish-aware scoring.
- BTC dominance impact on altcoin sentiment.
- Live ETF inflow/outflow panel for BTC and ETH.
- Options open interest for supported assets.
- MyBuddy AI assistant for crypto questions.
- Auto refresh with pause/resume control.
- Runtime logs and collapsible sections.

## What Changed

This version focuses on making the dashboard more truthful in bearish market conditions.

- BTC dominance now lowers altcoin sentiment more aggressively.
- ETF inflow/outflow is fetched live for the selected BTC/ETH coin.
- Fear & Greed scoring was rebalanced to avoid inflated bullish readings.
- Broken pipe and browser-open errors are handled safely.
- API responses now include better debugging context.

## Files

- `crypto_analyzer.py` - Python backend and API server.
- `dashboard.html` - Single-page frontend dashboard.

## Requirements

- Python 3.10+
- Internet access for live APIs
- Optional: `LLM_API_KEY` for MyBuddy AI

## Run

```bash
python3 crypto_analyzer.py
```

Then open:

```text
http://localhost:8765
```

## Environment Variables

- `LLM_ENABLED=1` - Enable MyBuddy assistant.
- `LLM_API_KEY=...` - API key for the assistant.
- `LLM_MODEL=...` - Assistant model name.
- `LLM_ENDPOINT=...` - Assistant API endpoint.

## Notes

- ETF flow is only live for BTC and ETH because those are the supported public ETF feeds.
- If the selected coin is not BTC or ETH, the ETF section explains that no flow source exists.
- Fear & Greed is a composite research score, not a financial signal.

## Project Structure

```text
cryptolens-enhanced/
├── crypto_analyzer.py
├── dashboard.html
└── README.md
```

## Troubleshooting

- If the browser does not open automatically, visit `http://localhost:8765` manually.
- If ETF data is unavailable, the upstream page may have changed or rate-limited the request.
- If MyBuddy does not respond, check `LLM_API_KEY` and network access.

## License

Use for internal or personal research unless you add a separate license.
