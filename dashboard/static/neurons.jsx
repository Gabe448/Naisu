// Content renderers for each neuron in its COMPACT (node) form

const NeuronPulse = () => (
  <>
    <div className="n-head">
      <span>Market Pulse</span>
      <span className="id">N-01 <span className="dot" style={{display:'inline-block', marginLeft:4}}/></span>
    </div>
    <div className="n-title">All channels synced</div>
    <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:'6px 14px', marginTop:2}}>
      {TICKERS.slice(0, 6).map(t => (
        <div className="kv" key={t.sym}>
          <span className="k">{t.sym}</span>
          <span className="v"><span className={t.ch>=0?'up-c':'dn-c'}>{t.ch>=0?'+':''}{t.ch.toFixed(2)}%</span></span>
        </div>
      ))}
    </div>
  </>
);

const NeuronScan = () => {
  const parts = SCAN.next.split(':');
  const mins  = parseInt(parts[0]) || 0;
  const secs  = parseInt(parts[1]) || 0;
  const totalSecs = mins * 60 + secs;
  const ringVal = SCAN.cycle > 0 ? Math.max(0, Math.min(100, (1 - totalSecs / SCAN.cycle) * 100)) : 0;
  return (
    <>
      <div className="n-head">
        <span>Scan Engine</span>
        <span className="id">N-02</span>
      </div>
      <div style={{display:'flex', alignItems:'center', gap:14}}>
        <Ring value={ringVal} label="live" />
        <div style={{flex:1}}>
          <div className="kv"><span className="k">Next Cycle</span><span className="v">{SCAN.next}</span></div>
          <div className="kv"><span className="k">Signals</span><span className="v">{SCAN.fired}/{SCAN.cap}</span></div>
          <div className="kv"><span className="k">Interval</span><span className="v">{SCAN.cycle}s</span></div>
        </div>
      </div>
      <Pill kind={SCAN.fired < SCAN.cap ? 'info' : 'warn'}>◉ {SCAN.fired < SCAN.cap ? 'scanning active' : 'weekly cap reached'}</Pill>
    </>
  );
};

const NeuronPL = () => {
  const max = Math.max(...WEEKLY.byDay.map(Math.abs), 1); // guard against all-zero
  return (
    <>
      <div className="n-head">
        <span>Weekly P&amp;L</span>
        <span className="id">N-03</span>
      </div>
      <div style={{display:'flex', alignItems:'center', gap:14}}>
        <Ring value={WEEKLY.winRate} label={WEEKLY.winRate+'%'} color="var(--up)" />
        <div style={{flex:1}}>
          <div style={{fontFamily:'JetBrains Mono', fontSize:20, color:'var(--up)', fontWeight:600}}>+${WEEKLY.pl.toLocaleString()}</div>
          <div style={{fontFamily:'JetBrains Mono', fontSize:10.5, color:'var(--ink-2)', marginTop:2}}>
            <span className="up-c">+{WEEKLY.plp}%</span> · {WEEKLY.trades} trades
          </div>
        </div>
      </div>
      <svg viewBox="0 0 100 20" preserveAspectRatio="none" style={{width:'100%', height:22}}>
        {WEEKLY.byDay.map((v, i) => {
          const up = v >= 0;
          const h = Math.abs(v) / max * 16;
          return <rect key={i} x={i * 20 + 2} y={up ? 10 - h : 10} width="16" height={h} fill={up ? 'var(--up)' : 'var(--down)'} opacity="0.85" />;
        })}
        <line x1="0" x2="100" y1="10" y2="10" stroke="oklch(0.4 0.08 250 / 0.5)" strokeWidth="0.4" />
      </svg>
    </>
  );
};

const NeuronPositions = () => (
  <>
    <div className="n-head">
      <span>Open Positions</span>
      <span className="id">N-04 · {POSITIONS.length} active</span>
    </div>
    <div style={{display:'flex', flexDirection:'column', gap:5, marginTop:-2}}>
      {POSITIONS.map(p => (
        <div key={p.sym} style={{display:'grid', gridTemplateColumns:'50px 44px 1fr auto', gap:8, alignItems:'center', fontFamily:'JetBrains Mono', fontSize:11}}>
          <span style={{color:'var(--ink-0)', fontWeight:600}}>{p.sym}</span>
          <span className={p.side==='LONG'?'up-c':'dn-c'} style={{fontSize:9.5, letterSpacing:'0.1em'}}>{p.side}</span>
          <span style={{color:'var(--ink-2)'}}>{p.qty} @ {p.entry.toFixed(2)}</span>
          <span className={p.pl>=0?'up-c':'dn-c'}>{p.pl>=0?'+':''}${p.pl}</span>
        </div>
      ))}
    </div>
  </>
);

const NeuronChart = () => {
  const last  = CANDLES.length ? CANDLES[CANDLES.length - 1] : null;
  const prev  = CANDLES.length > 1 ? CANDLES[CANDLES.length - 2] : null;
  const price = last ? last.c : 0;
  const chg   = (last && prev) ? last.c - prev.c : 0;
  const chgPct = (prev && prev.c) ? (chg / prev.c * 100) : 0;
  return (
    <>
      <div className="n-head">
        <span>Primary Tape · SPY</span>
        <span className="id">N-05 · 1D</span>
      </div>
      <div style={{display:'flex', justifyContent:'space-between', alignItems:'baseline'}}>
        <div style={{fontFamily:'JetBrains Mono', fontSize:22, color:'var(--ink-0)', fontWeight:600}}>${price.toFixed(2)}</div>
        <div style={{fontFamily:'JetBrains Mono', fontSize:11}} className={chg>=0?'up-c':'dn-c'}>
          {chg>=0?'▲':'▼'} {Math.abs(chg).toFixed(2)} ({Math.abs(chgPct).toFixed(2)}%)
        </div>
      </div>
      <MiniCandles />
      {last && (
        <div style={{display:'flex', gap:6, fontFamily:'JetBrains Mono', fontSize:9.5, color:'var(--ink-3)'}}>
          <span>O {last.o.toFixed(2)}</span>
          <span>H {last.hi.toFixed(2)}</span>
          <span>L {last.lo.toFixed(2)}</span>
          {LEVELS.length > 0 && <span style={{color:'var(--cyan)'}}>GEX {LEVELS.length} lvls</span>}
        </div>
      )}
    </>
  );
};

const NeuronLadder = () => (
  <>
    <div className="n-head">
      <span>Strike Ladder</span>
      <span className="id">N-06</span>
    </div>
    <div style={{display:'flex', flexDirection:'column', gap:3}}>
      {LEVELS.slice(0, 8).map((l, i) => {
        const kindClass = l.kind;
        const colorVar = l.kind==='cw'?'var(--up)':l.kind==='pw'?'var(--down)':l.kind==='flip'?'var(--warn)':'var(--cyan)';
        return (
          <div key={i} className={`ladder-row ${kindClass}`}>
            <span className="tag">{l.tag}</span>
            <div className="bar"><span style={{width: `${40 + Math.random()*50}%`, background: `linear-gradient(90deg, ${colorVar}, transparent)`}} /></div>
            <span className="px" style={{color: colorVar}}>{l.px.toFixed(2)}</span>
          </div>
        );
      })}
    </div>
  </>
);

const NeuronSignals = () => (
  <>
    <div className="n-head">
      <span>Signal Feed</span>
      <span className="id">N-07 · {SIGNALS.length} today</span>
    </div>
    <div style={{display:'flex', flexDirection:'column', gap:7}}>
      {SIGNALS.slice(0, 3).map((s, i) => (
        <div key={i} style={{display:'flex', gap:8, alignItems:'baseline', fontFamily:'JetBrains Mono', fontSize:10.5}}>
          <span style={{color:'var(--ink-3)', fontSize:9.5}}>{s.t.slice(0,5)}</span>
          <span style={{color:'var(--ink-0)', fontWeight:600, width:38}}>{s.sym}</span>
          <span className={s.dir==='LONG'?'up-c':'dn-c'} style={{fontSize:9.5}}>{s.dir}</span>
          <span style={{color:'var(--ink-2)', fontSize:10, flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{s.type}</span>
        </div>
      ))}
    </div>
  </>
);

const NeuronScanner = () => (
  <>
    <div className="n-head">
      <span>Top 10 Scanner</span>
      <span className="id">N-08 · next {SCAN.next}</span>
    </div>
    <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:'4px 16px'}}>
      {SCANNER.slice(0, 6).map(r => (
        <div key={r.sym} style={{display:'grid', gridTemplateColumns:'16px 48px 1fr auto', gap:6, alignItems:'center', fontFamily:'JetBrains Mono', fontSize:10.5}}>
          <span style={{color:'var(--ink-3)'}}>{r.rank}</span>
          <span style={{color:'var(--ink-0)', fontWeight:600}}>{r.sym}</span>
          <div className="score-bar" style={{width:'auto'}}><span style={{width: r.score+'%'}}/></div>
          <span className={r.up?'up-c':'dn-c'} style={{fontSize:9.5}}>{r.mom}</span>
        </div>
      ))}
    </div>
  </>
);

const NeuronClosed = () => (
  <>
    <div className="n-head">
      <span>Closed Trades</span>
      <span className="id">N-09 · this wk</span>
    </div>
    <div style={{display:'flex', flexDirection:'column', gap:4}}>
      {CLOSED.slice(0, 5).map((t, i) => (
        <div key={i} style={{display:'grid', gridTemplateColumns:'48px 20px 1fr auto', gap:6, alignItems:'center', fontFamily:'JetBrains Mono', fontSize:10.5}}>
          <span style={{color:'var(--ink-0)', fontWeight:600}}>{t.sym}</span>
          <span className={t.dir==='L'?'up-c':'dn-c'} style={{fontSize:9.5}}>{t.dir}</span>
          <span style={{color:'var(--ink-3)'}}>{t.entry.toFixed(2)}→{t.exit.toFixed(2)}</span>
          <span className={t.pl>=0?'up-c':'dn-c'}>{t.pl>=0?'+':''}${t.pl}</span>
        </div>
      ))}
    </div>
  </>
);

const NEURON_RENDERERS = {
  pulse: NeuronPulse,
  scan: NeuronScan,
  pl: NeuronPL,
  positions: NeuronPositions,
  chart: NeuronChart,
  ladder: NeuronLadder,
  signals: NeuronSignals,
  scanner: NeuronScanner,
  closed: NeuronClosed,
};

Object.assign(window, { NEURON_RENDERERS });
