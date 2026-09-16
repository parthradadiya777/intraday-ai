# Intraday AI — Hosted

NSE intraday scanner with BUY / SELL / WAIT guidance and Telegram alerts.

## Deploy on Render

1. Upload these files to the `intraday-ai` GitHub repository.
2. In Render: New → Web Service → select the GitHub repo.
3. Render will use `render.yaml`, or set:
   - Build Command: `python -m py_compile app.py`
   - Start Command: `python app.py`
4. A paid web service is required for the persistent disk used to retain settings across restarts. The disk is mounted at `/var/data`.
5. Open the generated `onrender.com` URL.
6. Enter Telegram Bot Token and Chat ID, click SAVE ALERTS, then TEST PHONE.

Do not commit Telegram tokens or other secrets to GitHub.

## Data source

This version uses Yahoo Finance chart data through its public web endpoint. It is not exchange-grade real-time market data and can be delayed or unavailable. Do not treat signals as guaranteed profit or as automatic order instructions.
