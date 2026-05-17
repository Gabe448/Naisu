"""
Chart renderer — daily candles with 20/50 MA, GEX levels, weekly pivots.
Outputs a BytesIO PNG to send directly to Discord as a file attachment.

Layout:
  Main panel: OHLCV candlesticks + 20MA (blue) + 50MA (orange)
  Overlays:   GEX key levels (dashed red), weekly pivots (dashed gray), GEX flip (solid yellow)
  Bottom:     Volume bars
"""
import io
from typing import Optional

import matplotlib
matplotlib.use("Agg")   # non-interactive backend for Discord/headless
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import mplfinance as mpf
import pandas as pd


def render_chart(
    ticker:     str,
    daily_df:   pd.DataFrame,
    gex:        dict,
    pivots:     dict,
    signal:     Optional[dict] = None,
) -> io.BytesIO:
    """
    Returns a BytesIO PNG of the chart.
    signal dict is optional — if present, marks entry/stop/target.
    """
    if daily_df.empty:
        return _empty_chart(ticker)

    df = daily_df.copy()

    # mplfinance needs columns: Open High Low Close Volume, DatetimeIndex
    df = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })
    # Drop timezone for mplfinance compatibility
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    # Only use last 60 bars
    df = df.tail(60)[["Open", "High", "Low", "Close", "Volume"]]

    # ── Addplot: 20MA and 50MA ────────────────────────────────────────────────
    ma20 = df["Close"].rolling(20).mean()
    ma50 = df["Close"].rolling(50).mean()

    add_plots = [
        mpf.make_addplot(ma20, color="#4A90D9", width=1.5, label="20 MA"),
        mpf.make_addplot(ma50, color="#E8A838", width=1.5, label="50 MA"),
    ]

    # ── Horizontal lines ──────────────────────────────────────────────────────
    hlines_values  = []
    hlines_colors  = []
    hlines_styles  = []
    hlines_widths  = []

    price_range_low  = df["Low"].min()
    price_range_high = df["High"].max()

    def in_range(val):
        return val and price_range_low * 0.95 <= val <= price_range_high * 1.05

    # GEX key levels
    for level in gex.get("key_levels", [])[:5]:
        strike = level.get("strike")
        if in_range(strike):
            hlines_values.append(strike)
            hlines_colors.append("#FF4444")
            hlines_styles.append("--")
            hlines_widths.append(1.0)

    # GEX flip
    flip = gex.get("gex_flip")
    if in_range(flip):
        hlines_values.append(flip)
        hlines_colors.append("#FFD700")
        hlines_styles.append("-")
        hlines_widths.append(1.5)

    # Weekly pivots
    for key, color in [("P", "#AAAAAA"), ("R1", "#88CC88"), ("S1", "#CC8888"),
                       ("R2", "#66BB66"), ("S2", "#BB6666")]:
        val = pivots.get(key)
        if in_range(val):
            hlines_values.append(val)
            hlines_colors.append(color)
            hlines_styles.append(":")
            hlines_widths.append(0.8)

    # ── Entry / stop / target lines ───────────────────────────────────────────
    if signal:
        for val, color in [
            (signal.get("entry"),  "#00FF88"),
            (signal.get("stop"),   "#FF4444"),
            (signal.get("target"), "#44AAFF"),
        ]:
            if in_range(val):
                hlines_values.append(val)
                hlines_colors.append(color)
                hlines_styles.append("-")
                hlines_widths.append(1.2)

    # ── Style ─────────────────────────────────────────────────────────────────
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(
            up="#26A69A", down="#EF5350",
            edge="inherit", wick="inherit", volume="in",
        ),
        figcolor="#1a1a2e",
        facecolor="#1a1a2e",
        gridcolor="#2d2d4e",
    )

    buf = io.BytesIO()
    fig, axes = mpf.plot(
        df,
        type="candle",
        style=style,
        addplot=add_plots,
        volume=True,
        title=f"\n{ticker} — Daily Chart",
        returnfig=True,
        figsize=(14, 8),
    )

    # Draw horizontal lines manually — mplfinance hlines kwarg has version issues
    ax = axes[0]
    for val, color, style_, width in zip(hlines_values, hlines_colors, hlines_styles, hlines_widths):
        ax.axhline(y=val, color=color, linestyle=style_, linewidth=width, alpha=0.8)

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_items = [
        mpatches.Patch(color="#4A90D9", label="20 MA"),
        mpatches.Patch(color="#E8A838", label="50 MA"),
        mpatches.Patch(color="#FFD700", label="GEX Flip"),
        mpatches.Patch(color="#FF4444", label="GEX Levels"),
        mpatches.Patch(color="#AAAAAA", label="Weekly Pivot"),
    ]
    if signal:
        legend_items += [
            mpatches.Patch(color="#00FF88", label="Entry"),
            mpatches.Patch(color="#FF4444", label="Stop"),
            mpatches.Patch(color="#44AAFF", label="Target"),
        ]
    axes[0].legend(handles=legend_items, loc="upper left", fontsize=8,
                   facecolor="#2d2d4e", labelcolor="white")

    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def _empty_chart(ticker: str) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(10, 6), facecolor="#1a1a2e")
    ax.set_facecolor("#1a1a2e")
    ax.text(0.5, 0.5, f"No data for {ticker}", ha="center", va="center",
            color="white", fontsize=14, transform=ax.transAxes)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf
