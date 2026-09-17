# Intraday AI — Hosted

NSE intraday scanner with AI/ML-based BUY / SELL / WAIT guidance, target/stop monitoring and Telegram alerts.

## Deployment

Render uses `render.yaml` and starts the service with `python launcher.py`. The launcher loads the live NSE scanner and connects the AI engine in `ai_engine.py`.

## AI signal

The AI layer uses recent 5-minute market history and a machine-learning probability model, with a technical fallback when historical training data is insufficient. Signals are probabilistic and are not guaranteed profit or automatic order instructions.

## Alerts

Telegram alerts can be configured from the web UI. Do not commit Telegram tokens or other secrets to GitHub.

## Data source

NSE data is accessed through the `nsemine` package. Availability and latency can vary because public/unofficial access may be rate-limited. Do not treat the feed as exchange-grade execution data.
