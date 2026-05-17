"""
Flask dashboard — localhost:5000

Run standalone:  python -m dashboard.app
Or via main.py:  python main.py --dashboard
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, render_template, jsonify
from dotenv import load_dotenv
load_dotenv()

from dashboard import state

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    return jsonify(state.get_bot_status())


@app.route("/api/market")
def api_market():
    return jsonify(state.get_market_context())


@app.route("/api/watchlist")
def api_watchlist():
    return jsonify(state.get_watchlist())


@app.route("/api/positions")
def api_positions():
    return jsonify(state.get_positions())


@app.route("/api/signals")
def api_signals():
    return jsonify(state.get_recent_signals())


@app.route("/api/scorecard")
def api_scorecard():
    return jsonify(state.get_scorecard())


@app.route("/api/chart/<ticker>")
def api_chart(ticker: str):
    ticker = ticker.upper()
    if ticker not in ("SPY", "QQQ", "AAPL", "TSLA", "NVDA", "AMZN", "META",
                      "MSFT", "GOOGL", "AMD", "AVGO", "META"):
        # Allow any ticker but sanitize
        ticker = "".join(c for c in ticker if c.isalpha() or c == "-")[:6]
    return jsonify(state.get_chart_data(ticker))


if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", 5000))
    print(f"[dashboard] Running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
