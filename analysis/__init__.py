"""
Analysis package — strategy engine.

Quick start:
    from analysis.pipeline import analyse_ticker
    result = analyse_ticker("NVDA", daily_df, gex_result=gex_api_response)
"""
from analysis.confluence import ConfluenceResult, score_confluence
from analysis.gex_analysis import GexContext, extract_dex_bias, parse_gex_context
from analysis.levels import Level, compute_emas, get_key_levels
from analysis.patterns import PatternBias, PatternResult, PatternType, scan_patterns
from analysis.pipeline import analyse_ticker
from analysis.scorer import conviction_score, grade
from analysis.signals import SignalResult, run_all_signals
from analysis.trade_builder import TradeIdea, build_trade, compute_atr, format_trade_card

__all__ = [
    "analyse_ticker",
    "ConfluenceResult", "score_confluence",
    "GexContext", "extract_dex_bias", "parse_gex_context",
    "Level", "compute_emas", "get_key_levels",
    "PatternBias", "PatternResult", "PatternType", "scan_patterns",
    "conviction_score", "grade",
    "SignalResult", "run_all_signals",
    "TradeIdea", "build_trade", "compute_atr", "format_trade_card",
]
