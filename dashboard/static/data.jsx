// data.jsx — live data bridge to Flask API
// Sets globals to defaults, fetches all endpoints, fires 'naisu:data-loaded'
// so React re-renders with real values. Auto-refreshes every 60s.

// ── Layout constants (never change) ─────────────────────────────────────────

window.NEURONS = [
  { id: 'pulse',     title: 'Market Pulse',    kind: 'pulse' },
  { id: 'scan',      title: 'Scan Engine',     kind: 'scan' },
  { id: 'pl',        title: 'Weekly P&L',      kind: 'pl' },
  { id: 'positions', title: 'Open Positions',  kind: 'positions' },
  { id: 'chart',     title: 'Primary Tape',    kind: 'chart' },
  { id: 'ladder',    title: 'Strike Ladder',   kind: 'ladder' },
  { id: 'signals',   title: 'Signal Feed',     kind: 'signals' },
  { id: 'scanner',   title: 'Top 10 Scanner',  kind: 'scanner' },
  { id: 'closed',    title: 'Closed Trades',   kind: 'closed' },
];

window.EDGES = [
  ['pulse','scan'], ['scan','pl'],
  ['pulse','positions'], ['positions','chart'],
  ['chart','ladder'], ['chart','signals'], ['ladder','signals'],
  ['chart','scanner'], ['scanner','signals'], ['scanner','closed'],
  ['signals','closed'], ['pl','scanner'],
  ['scan','signals'], ['positions','scanner'], ['pulse','ladder'],
];

// ── Loading-state defaults ───────────────────────────────────────────────────

window.TICKERS   = [{sym:'SPY',px:0,ch:0},{sym:'QQQ',px:0,ch:0},{sym:'VIX',px:0,ch:0}];
window.SCAN      = { next: '--:--', cycle: 600, fired: 0, cap: 20 };
window.WEEKLY    = { pl:0, plp:0, trades:0, wins:0, losses:0, winRate:0, avgWin:0, avgLoss:0, byDay:[0,0,0,0,0] };
window.POSITIONS = [];
window.CANDLES   = Array.from({length:30}, (_,i) => ({o:500,c:500,hi:501,lo:499,i}));
window.LEVELS    = [];
window.SIGNALS   = [];
window.SCANNER   = [];
window.CLOSED    = [];

// ── Helpers ──────────────────────────────────────────────────────────────────

function secsToMSS(secs) {
  if (secs == null || isNaN(secs)) return '--:--';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return m + ':' + s.toString().padStart(2, '0');
}

function fmt2(n) {
  return n != null ? parseFloat(parseFloat(n).toFixed(2)) : 0;
}

// Group closed positions by weekday, return [Mon..Fri] approximate $ P&L totals
function byDayTotals(positions) {
  const days = [0, 0, 0, 0, 0];
  for (const p of positions) {
    if (!p.exit_date) continue;
    const d   = new Date(p.exit_date).getDay(); // 0=Sun,1=Mon...5=Fri
    const idx = (d >= 1 && d <= 5) ? d - 1 : null;
    if (idx == null) continue;
    // approximate $ P&L = pct * entry_price (1-share basis — directionally correct)
    days[idx] += fmt2((p.final_pnl_pct || 0) * (p.entry_price || 100) / 100);
  }
  return days;
}

// ── Main fetch ───────────────────────────────────────────────────────────────

async function loadAll() {
  try {
    const [status, market, watchlist, positions, signals, scorecard, chart] =
      await Promise.all([
        fetch('/api/status').then(r => r.json()),
        fetch('/api/market').then(r => r.json()),
        fetch('/api/watchlist').then(r => r.json()),
        fetch('/api/positions').then(r => r.json()),
        fetch('/api/signals').then(r => r.json()),
        fetch('/api/scorecard').then(r => r.json()),
        fetch('/api/chart/SPY').then(r => r.json()),
      ]);

    // ── TICKERS: SPY/QQQ/VIX + top scanner names ─────────────────────────────
    const tickers = [];
    for (const sym of ['SPY', 'QQQ', 'VIX']) {
      const d = market[sym];
      if (d && d.price != null)
        tickers.push({ sym, px: d.price, ch: d.change_pct || 0 });
    }
    for (const c of (watchlist || []).slice(0, 6)) {
      if (!tickers.find(t => t.sym === c.ticker) && c.price)
        tickers.push({ sym: c.ticker, px: fmt2(c.price), ch: fmt2(c.momentum_10d) });
      if (tickers.length >= 8) break;
    }
    if (tickers.length) window.TICKERS = tickers;

    // ── SCAN: bot heartbeat ───────────────────────────────────────────────────
    if (status) {
      window.SCAN = {
        next:  secsToMSS(status.next_scan_seconds),
        cycle: 600,
        fired: status.signal_count_this_week || 0,
        cap:   status.max_signals_per_week   || 20,
      };
    }

    // ── WEEKLY: stats from closed positions (7-day window) ───────────────────
    const allClosed = (positions && positions.closed) || [];
    const weekAgo   = Date.now() - 7 * 24 * 60 * 60 * 1000;
    const recent    = allClosed.filter(p => p.exit_date && new Date(p.exit_date).getTime() >= weekAgo);
    const wWins     = recent.filter(p => (p.final_pnl_pct || 0) > 0);
    const wLosses   = recent.filter(p => (p.final_pnl_pct || 0) <= 0);
    const dollarPL  = recent.reduce((acc, p) => acc + (p.final_pnl_pct || 0) * (p.entry_price || 100) / 100, 0);
    const avgWinDollar  = wWins.length   ? wWins.reduce((a, p)   => a + (p.final_pnl_pct || 0) * (p.entry_price || 100) / 100, 0) / wWins.length   : 0;
    const avgLossDollar = wLosses.length ? wLosses.reduce((a, p) => a + (p.final_pnl_pct || 0) * (p.entry_price || 100) / 100, 0) / wLosses.length : 0;
    const winRate  = recent.length ? Math.round(wWins.length / recent.length * 100) : 0;
    const totalPct = recent.reduce((a, p) => a + (p.final_pnl_pct || 0), 0);

    if (recent.length > 0 || (scorecard && scorecard.trades > 0)) {
      window.WEEKLY = {
        pl:      Math.round(dollarPL),
        plp:     fmt2(totalPct),
        trades:  recent.length  || (scorecard && scorecard.trades)    || 0,
        wins:    wWins.length   || (scorecard && scorecard.wins)      || 0,
        losses:  wLosses.length || (scorecard && scorecard.losses)    || 0,
        winRate: winRate        || Math.round((scorecard && scorecard.win_rate) || 0),
        avgWin:  fmt2(avgWinDollar  || (scorecard && scorecard.avg_win)  || 0),
        avgLoss: fmt2(avgLossDollar || (scorecard && scorecard.avg_loss) || 0),
        byDay:   byDayTotals(recent),
      };
    }

    // ── POSITIONS: open trades ────────────────────────────────────────────────
    window.POSITIONS = ((positions && positions.open) || []).map(p => {
      const entry = p.entry_price || 0;
      return {
        sym:  p.ticker,
        side: (p.direction || 'long').toUpperCase(),
        qty:  1,
        entry,
        px:   p.current_price || entry,
        pl:   Math.round((p.pnl_pct || 0) * entry / 100),
        plp:  fmt2(p.pnl_pct),
      };
    });

    // ── CLOSED: recent closed trades (newest first) ───────────────────────────
    if (allClosed.length) {
      window.CLOSED = allClosed.slice(-15).reverse().map(p => ({
        sym:   p.ticker,
        dir:   (p.direction || 'L')[0].toUpperCase(),
        entry: fmt2(p.entry_price || 0),
        exit:  fmt2(p.exit_price || p.entry_price || 0),
        pl:    Math.round((p.final_pnl_pct || 0) * (p.entry_price || 100) / 100),
        date:  p.exit_date ? p.exit_date.slice(5, 10) : '--',
      }));
    }

    // ── SIGNALS: recent high-conviction signals ───────────────────────────────
    if (signals && signals.length) {
      window.SIGNALS = signals.slice(0, 10).map(s => {
        const posted  = s.posted_at ? new Date(s.posted_at) : null;
        const timeStr = posted ? posted.toTimeString().slice(0, 8) : '--:--:--';
        const lines   = (s.thesis || '').split('\n').filter(Boolean);
        // Second line of thesis is usually the confluence summary
        const note    = (lines[1] || ('Score ' + s.conviction_score + ' · RR ' + fmt2(s.rr_ratio))).slice(0, 70);
        const sigType = s.gex_regime === 'negative' ? 'REVERSAL'
                      : s.flow_bias  === 'bearish'  ? 'FLOW'
                      : 'MOMENTUM';
        return {
          t:    timeStr,
          sym:  s.ticker,
          type: sigType,
          dir:  (s.direction || 'LONG').toUpperCase(),
          note,
        };
      });
    }

    // ── SCANNER: top 10 watchlist candidates by yf_score ─────────────────────
    if (watchlist && watchlist.length) {
      window.SCANNER = watchlist.slice(0, 10).map((c, i) => {
        const mom = c.momentum_10d || 0;
        return {
          rank:  i + 1,
          sym:   c.ticker,
          score: c.yf_score || 0,
          px:    c.price ? fmt2(c.price) : 0,
          vol:   c.rel_volume ? parseFloat(c.rel_volume.toFixed(1)) : 0,
          mom:   (mom >= 0 ? '+' : '') + mom.toFixed(1) + '%',
          up:    mom >= 0,
        };
      });
    }

    // ── CANDLES + LEVELS: SPY 3-month chart with GEX overlays ────────────────
    if (chart && chart.candles && chart.candles.length) {
      const ma20map = {};
      const ma50map = {};
      for (const p of (chart.ma20 || [])) ma20map[p.time] = p.value;
      for (const p of (chart.ma50 || [])) ma50map[p.time] = p.value;

      window.CANDLES = chart.candles.map((c, i) => ({
        o: c.open, c: c.close, hi: c.high, lo: c.low, i,
        time: c.time,
        ma20: ma20map[c.time] || null,
        ma50: ma50map[c.time] || null,
      }));

      const lvls = [];
      for (const lv of (chart.gex_levels || [])) {
        lvls.push({
          tag:  lv.type === 'call_wall' ? 'CW' : 'PW',
          px:   lv.price,
          kind: lv.type === 'call_wall' ? 'cw' : 'pw',
        });
      }
      if (chart.gex_flip) lvls.push({ tag: 'FLIP', px: chart.gex_flip, kind: 'flip' });
      const spot = chart.candles[chart.candles.length - 1].close;
      if (spot) lvls.push({ tag: 'SPOT', px: spot, kind: 'spot' });
      lvls.sort((a, b) => b.px - a.px);
      window.LEVELS = lvls;
    }

    window.dispatchEvent(new CustomEvent('naisu:data-loaded'));

  } catch (err) {
    console.error('[naisu] data load failed:', err);
  }
}

loadAll();
setInterval(loadAll, 60000);
