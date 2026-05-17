// Expanded detail views — each neuron opens into one of these

const DHead = ({ eyebrow, title, desc, kpis }) => (
  <div className="dhead">
    <div>
      <div className="eyebrow">{eyebrow}</div>
      <h2>{title}</h2>
      {desc && <p>{desc}</p>}
    </div>
    {kpis && <div className="kpi-row">{kpis.map((k, i) => (
      <div className="kpi" key={i}>
        <div className="kl">{k.label}</div>
        <div className="kv">{k.value}</div>
        {k.delta && <div className="kd">{k.delta}</div>}
      </div>
    ))}</div>}
  </div>
);

const DetailPulse = () => (
  <>
    <DHead
      eyebrow="Neuron · N-01 · Live feed"
      title="Market Pulse"
      desc="Cross-asset readout of the indices, volatility, rates and digital assets we route through. Pulses fire into the Scan Engine as correlation inputs."
      kpis={(() => {
        const spy = TICKERS.find(t => t.sym === 'SPY');
        const qqq = TICKERS.find(t => t.sym === 'QQQ');
        const vix = TICKERS.find(t => t.sym === 'VIX');
        const vixVal = vix ? vix.px : 0;
        const regime = vixVal > 25 ? 'RISK-OFF' : vixVal > 18 ? 'CAUTION' : 'RISK-ON';
        const regimeKind = vixVal > 25 ? 'danger' : vixVal > 18 ? 'warn' : 'ok';
        return [
          {label:'SPY', value: spy ? `$${spy.px.toFixed(2)}` : '--', delta: spy ? <ArrowNum v={spy.ch} suffix="%" /> : ''},
          {label:'QQQ', value: qqq ? `$${qqq.px.toFixed(2)}` : '--', delta: qqq ? <ArrowNum v={qqq.ch} suffix="%" /> : ''},
          {label:'VIX', value: vixVal ? vixVal.toFixed(2) : '--', delta: vix ? <ArrowNum v={vix.ch} suffix="%" /> : ''},
          {label:'Regime', value: regime, delta: <Pill kind={regimeKind}>{vixVal ? `vix ${vixVal.toFixed(1)}` : 'loading'}</Pill>},
        ];
      })()}
    />
    <div className="grid-2">
      <div className="panel">
        <h3>Cross-Asset Monitor <span style={{color:'var(--ink-3)'}}>08 symbols</span></h3>
        <table className="data">
          <thead><tr><th>Symbol</th><th className="n">Last</th><th className="n">Chg</th><th>Sparkline</th></tr></thead>
          <tbody>
            {TICKERS.map(t => {
              const data = Array.from({length: 24}, () => t.px + (Math.random()-0.5)*t.px*0.01);
              return (
                <tr key={t.sym}>
                  <td className="sym">{t.sym}</td>
                  <td className="n">{t.px.toLocaleString()}</td>
                  <td className={`n ${t.ch>=0?'up-c':'dn-c'}`}>{t.ch>=0?'+':''}{t.ch.toFixed(2)}%</td>
                  <td style={{width:120}}><Sparkline data={data} w={120} h={20} color={t.ch>=0?'var(--up)':'var(--down)'} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="panel">
        <h3>Regime Flags</h3>
        <div style={{display:'flex', flexDirection:'column', gap:10, fontSize:12}}>
          {[
            ['Volatility', 'ELEVATED', 'warn', 'VOL printed 26+ for 3rd session'],
            ['Liquidity', 'HEALTHY', 'ok', 'Depth at NBBO ≥ 12bps'],
            ['Sentiment', 'BEARISH', 'danger', 'Put/call 1.32 · 5d skew widened'],
            ['Breadth', 'MIXED', 'info', '58% above 20-day MA'],
            ['Carry', 'NEUTRAL', 'info', '2s10s unchanged on week'],
          ].map(([k, v, kind, note]) => (
            <div key={k} style={{display:'flex', gap:12, alignItems:'center'}}>
              <Pill kind={kind}>{v}</Pill>
              <div>
                <div style={{color:'var(--ink-0)', fontWeight:600, fontSize:12}}>{k}</div>
                <div style={{color:'var(--ink-2)', fontSize:11}}>{note}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  </>
);

const DetailScan = () => (
  <>
    <DHead
      eyebrow="Neuron · N-02 · Control plane"
      title="Scan Engine"
      desc="Every 48 seconds we re-evaluate 412 symbols across 9 signal families. Fires are routed to the Signal Feed, reviewed, and staged for execution."
      kpis={[
        {label:'Next Cycle', value:SCAN.next, delta:`${SCAN.cycle}s interval`},
        {label:'Signals', value:`${SCAN.fired}/${SCAN.cap}`, delta: SCAN.fired >= SCAN.cap ? 'cap reached' : `${SCAN.cap - SCAN.fired} remaining`},
        {label:'Watchlist', value: SCANNER.length ? SCANNER.length + ' ranked' : '--', delta:'by yf_score'},
        {label:'Top Pick', value: SCANNER.length ? SCANNER[0].sym : '--', delta: SCANNER.length ? `score ${SCANNER[0].score}` : ''},
      ]}
    />
    <div className="grid-2">
      <div className="panel">
        <h3>Signal Families</h3>
        <div style={{display:'flex', flexDirection:'column', gap:10}}>
          {[
            ['BREAK', 'Breakout of CW/PW', 72, 'var(--cyan)'],
            ['MOMENTUM', 'RVOL + VWAP cross', 58, 'var(--blue)'],
            ['REVERSAL', 'Failed retest', 41, 'var(--indigo)'],
            ['FLIP', 'Pivot failure', 31, 'var(--warn)'],
            ['SQUEEZE', 'BB/KC compression', 47, 'var(--violet)'],
            ['GAP', 'Prior-day fill', 62, 'var(--cyan)'],
          ].map(([k, d, v, c]) => (
            <div key={k}>
              <div style={{display:'flex', justifyContent:'space-between', fontFamily:'JetBrains Mono', fontSize:11, marginBottom:4}}>
                <span style={{color:'var(--ink-0)'}}>{k} <span style={{color:'var(--ink-3)', fontSize:10}}>· {d}</span></span>
                <span style={{color:'var(--ink-2)'}}>{v}% conf</span>
              </div>
              <div className="score-bar" style={{width:'100%', height:5}}><span style={{width:v+'%', background:`linear-gradient(90deg, ${c}, var(--cyan))`}}/></div>
            </div>
          ))}
        </div>
      </div>
      <div className="panel">
        <h3>Cycle Log</h3>
        <div style={{fontFamily:'JetBrains Mono', fontSize:10.5, color:'var(--ink-2)', lineHeight:1.7}}>
          {[
            '[09:18:12] cycle 00041 · 412 syms · 0 fires',
            '[09:17:24] cycle 00040 · 412 syms · 0 fires',
            '[09:16:36] cycle 00039 · 412 syms · 1 fire · NRVA',
            '[09:15:48] cycle 00038 · 412 syms · 0 fires',
            '[09:15:00] cycle 00037 · 411 syms · 0 fires',
            '[09:14:12] cycle 00036 · 411 syms · 2 fires · CRVX · BRD',
            '[09:13:24] cycle 00035 · 411 syms · 0 fires',
            '[09:12:36] cycle 00034 · 411 syms · 0 fires',
            '[09:11:48] cycle 00033 · 411 syms · 1 fire · ZETH',
          ].map((l, i) => <div key={i} style={{color: l.includes('fire') && !l.includes('0 fires') ? 'var(--cyan)' : 'var(--ink-3)'}}>{l}</div>)}
        </div>
      </div>
    </div>
  </>
);

const DetailPL = () => (
  <>
    <DHead
      eyebrow="Neuron · N-03 · Performance"
      title="Weekly P&amp;L"
      desc="Week-to-date realized and unrealized. Breaks attribution down by setup family and symbol. Compounds into the monthly equity curve."
      kpis={[
        {label:'Week P&L', value:`${WEEKLY.pl >= 0 ? '+' : ''}$${Math.abs(WEEKLY.pl).toLocaleString()}`, delta:<ArrowNum v={WEEKLY.plp} suffix="%" />},
        {label:'Win Rate', value:`${WEEKLY.winRate}%`, delta:`${WEEKLY.wins}W / ${WEEKLY.losses}L`},
        {label:'Avg Win', value:`$${Math.abs(WEEKLY.avgWin).toFixed(0)}`, delta:<span className="up-c">per share basis</span>},
        {label:'Avg Loss', value:`$${Math.abs(WEEKLY.avgLoss).toFixed(0)}`, delta:<span className="dn-c">per share basis</span>},
      ]}
    />
    <div className="grid-2">
      <div className="panel">
        <h3>Daily Equity</h3>
        <svg viewBox="0 0 400 140" style={{width:'100%', height:180}}>
          {[0,1,2,3].map(i => <line key={i} x1="0" x2="400" y1={30*i+10} y2={30*i+10} stroke="oklch(0.3 0.06 250 / 0.3)" strokeWidth="0.5" strokeDasharray="3 4" />)}
          {(() => {
            let acc = 0;
            const cumPL = WEEKLY.byDay.map(v => { acc += v; return acc; });
            const maxAbs = Math.max(...cumPL.map(Math.abs), 1); // guard zero
            const pts = cumPL.map((v, i) => [i*80+40, 80 - (v / maxAbs * 70)]);
            const d = 'M ' + pts.map(p => p.join(' ')).join(' L ');
            const fill = d + ` L ${pts[pts.length-1][0]} 130 L ${pts[0][0]} 130 Z`;
            return (<>
              <path d={fill} fill="oklch(0.68 0.17 255 / 0.2)" />
              <path d={d} stroke="var(--cyan)" strokeWidth="1.5" fill="none" style={{filter:'drop-shadow(0 0 4px var(--cyan))'}} />
              {pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="3" fill="var(--cyan)" />)}
            </>);
          })()}
          {['MON','TUE','WED','THU','FRI'].map((d, i) => (
            <text key={d} x={i*80+40} y="138" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="9" fill="var(--ink-3)">{d}</text>
          ))}
        </svg>
        <div style={{display:'flex', justifyContent:'space-between', marginTop:10, fontFamily:'JetBrains Mono', fontSize:11}}>
          {WEEKLY.byDay.map((v, i) => (
            <span key={i} className={v>=0?'up-c':'dn-c'}>{v>=0?'+':''}${v}</span>
          ))}
        </div>
      </div>
      <div className="panel">
        <h3>Attribution</h3>
        <table className="data">
          <thead><tr><th>Setup</th><th className="n">Trades</th><th className="n">Win %</th><th className="n">Net</th></tr></thead>
          <tbody>
            {[
              ['BREAK', 7, 71, +1420],
              ['MOMENTUM', 5, 80, +820],
              ['REVERSAL', 3, 67, +380],
              ['FLIP', 2, 50, -88],
              ['GAP', 1, 100, +72],
            ].map(([k, n, w, p]) => (
              <tr key={k}>
                <td className="sym">{k}</td>
                <td className="n">{n}</td>
                <td className="n">{w}%</td>
                <td className={`n ${p>=0?'up-c':'dn-c'}`}>{p>=0?'+':''}${p}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  </>
);

const DetailPositions = () => (
  <>
    <DHead
      eyebrow="Neuron · N-04 · Book"
      title="Open Positions"
      desc="Live positions with entry, delta, theta, and attached stops. Risk is synchronized with the Strike Ladder's flip levels."
      kpis={[
        {label:'Exposure', value:'$158k', delta:'62% long · 38% short'},
        {label:'Open P&L', value:'+$1,227', delta:<ArrowNum v={+0.78} suffix="%"/>},
        {label:'Heat', value:'1.4R', delta:'of 3R budget'},
        {label:'Positions', value:POSITIONS.length, delta:'all at rule'},
      ]}
    />
    <div className="panel">
      <h3>Active Book</h3>
      <table className="data">
        <thead><tr><th>Sym</th><th>Side</th><th className="n">Qty</th><th className="n">Entry</th><th className="n">Mark</th><th className="n">P&L</th><th className="n">%</th><th>Stop</th><th>Target</th></tr></thead>
        <tbody>
          {POSITIONS.map(p => (
            <tr key={p.sym}>
              <td className="sym">{p.sym}</td>
              <td><Pill kind={p.side==='LONG'?'ok':'danger'}>{p.side}</Pill></td>
              <td className="n">{p.qty}</td>
              <td className="n">{p.entry.toFixed(2)}</td>
              <td className="n">{p.px.toFixed(2)}</td>
              <td className={`n ${p.pl>=0?'up-c':'dn-c'}`}>{p.pl>=0?'+':''}${p.pl}</td>
              <td className={`n ${p.plp>=0?'up-c':'dn-c'}`}>{p.plp>=0?'+':''}{p.plp}%</td>
              <td style={{color:'var(--warn)'}}>{(p.entry * 0.992).toFixed(2)}</td>
              <td style={{color:'var(--cyan)'}}>{(p.entry * 1.018).toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </>
);

const DetailChart = () => (
  <>
    <DHead
      eyebrow="Neuron · N-05 · BRD · 1-minute"
      title="Primary Tape"
      desc="Full candle view with MA-9, EMA-20 and Strike Ladder overlay. Crosshair data, volume profile and micro-signals render below."
      kpis={[
        {label:'Last', value:'$656.73', delta:<ArrowNum v={-0.33} suffix="%" />},
        {label:'High', value:'660.12', delta:'at 10:04'},
        {label:'Low', value:'655.20', delta:'at 13:47'},
        {label:'Volume', value:'48.2M', delta:'1.14× 20d'},
      ]}
    />
    <div className="panel">
      <h3>Tape · BRD 18 Feb 26 <span style={{color:'var(--ink-3)'}}>MA-9 · EMA-20 · Levels</span></h3>
      <BigCandles />
    </div>
    <div className="grid-3" style={{marginTop:16}}>
      <div className="panel">
        <h3>Volume Profile</h3>
        <div style={{display:'flex', flexDirection:'column', gap:3}}>
          {Array.from({length:12}, (_, i) => {
            const px = 662 - i;
            const w = 20 + Math.random() * 70;
            return (
              <div key={i} style={{display:'grid', gridTemplateColumns:'44px 1fr', gap:6, alignItems:'center', fontFamily:'JetBrains Mono', fontSize:10}}>
                <span style={{color:'var(--ink-3)'}}>{px}.00</span>
                <div style={{height:6, background:'linear-gradient(90deg, var(--blue), var(--cyan))', width:`${w}%`, borderRadius:2, opacity:0.7}}/>
              </div>
            );
          })}
        </div>
      </div>
      <div className="panel">
        <h3>Micro Signals</h3>
        <div style={{display:'flex', flexDirection:'column', gap:6, fontFamily:'JetBrains Mono', fontSize:11}}>
          <div className="kv"><span className="k">VWAP</span><span className="v">657.14</span></div>
          <div className="kv"><span className="k">Anchored VWAP</span><span className="v">658.42</span></div>
          <div className="kv"><span className="k">RVOL (5m)</span><span className="v up-c">1.42×</span></div>
          <div className="kv"><span className="k">Bid/Ask Ratio</span><span className="v dn-c">0.84</span></div>
          <div className="kv"><span className="k">Tick Distribution</span><span className="v">-124 / +87</span></div>
          <div className="kv"><span className="k">Squeeze</span><span className="v">off</span></div>
          <div className="kv"><span className="k">Trend Score</span><span className="v">-0.32</span></div>
        </div>
      </div>
      <div className="panel">
        <h3>Order Flow</h3>
        <div style={{display:'flex', flexDirection:'column', gap:5, fontFamily:'JetBrains Mono', fontSize:10.5}}>
          {[
            ['13:47:22', 'SELL', 18400, 655.22],
            ['13:46:58', 'BUY',  12200, 655.30],
            ['13:46:41', 'SELL',  8400, 655.18],
            ['13:46:12', 'SELL', 24100, 655.25],
            ['13:45:48', 'BUY',   6200, 655.40],
            ['13:45:22', 'BUY',  18800, 655.44],
            ['13:44:58', 'SELL', 32100, 655.20],
          ].map((r, i) => (
            <div key={i} style={{display:'grid', gridTemplateColumns:'60px 40px 1fr auto', gap:6}}>
              <span style={{color:'var(--ink-3)'}}>{r[0]}</span>
              <span className={r[1]==='BUY'?'up-c':'dn-c'}>{r[1]}</span>
              <span style={{color:'var(--ink-2)'}}>{r[2].toLocaleString()}</span>
              <span style={{color:'var(--ink-1)'}}>{r[3].toFixed(2)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  </>
);

const DetailLadder = () => (
  <>
    <DHead
      eyebrow="Neuron · N-06 · Structure"
      title="Strike Ladder"
      desc="Calls-written (CW), puts-written (PW), and FLIP pivots for the active underlying. The ladder is the scaffolding every signal locks onto."
      kpis={[
        {label:'Spot', value:'656.73', delta:'between PW 655 / CW 660'},
        {label:'Upside', value:'+3.27', delta:'to CW 660.00'},
        {label:'Downside', value:'-1.73', delta:'to PW 655.00'},
        {label:'FLIP', value:'640.00', delta:'trigger armed'},
      ]}
    />
    <div className="grid-2">
      <div className="panel">
        <h3>Level Map</h3>
        <div style={{display:'flex', flexDirection:'column', gap:4}}>
          {LEVELS.map((l, i) => (
            <div key={i} className={`level-pill ${l.kind}`}>
              <span style={{letterSpacing:'0.1em', fontSize:10.5}}>{l.tag}</span>
              <span style={{fontWeight:600}}>{l.px.toFixed(2)}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="panel">
        <h3>Reaction History</h3>
        <table className="data">
          <thead><tr><th>Level</th><th className="n">Tests</th><th className="n">Holds</th><th>Strength</th></tr></thead>
          <tbody>
            {[
              ['CW 660.00', 4, 3, 75],
              ['CW 661.00', 2, 2, 100],
              ['PW 655.00', 6, 4, 67],
              ['PW 654.00', 3, 2, 67],
              ['PW 650.00', 5, 5, 100],
              ['FLIP 640',  1, 0, 0],
            ].map(([l, t, h, s]) => (
              <tr key={l}>
                <td className="sym">{l}</td>
                <td className="n">{t}</td>
                <td className="n">{h}</td>
                <td><div className="score-bar" style={{width:80}}><span style={{width:s+'%'}}/></div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  </>
);

const DetailSignals = () => (
  <>
    <DHead
      eyebrow="Neuron · N-07 · Signal Feed"
      title="Signal Feed"
      desc="Fires routed from the Scan Engine. Each row is reviewable, size-able and stageable to the order router."
      kpis={(() => {
        const longs  = SIGNALS.filter(s => s.dir === 'LONG').length;
        const shorts = SIGNALS.filter(s => s.dir === 'SHORT').length;
        const top    = SIGNALS[0];
        return [
          {label:'Total Signals', value: SIGNALS.length, delta: SIGNALS.length ? 'all time' : 'none yet'},
          {label:'Long / Short', value: `${longs} / ${shorts}`, delta: longs + shorts ? `${Math.round(longs/(longs+shorts)*100)}% long` : '--'},
          {label:'Latest', value: top ? top.sym : '--', delta: top ? top.dir : ''},
          {label:'Next Scan', value: SCAN.next, delta: `${SCAN.fired}/${SCAN.cap} fired`},
        ];
      })()}
    />
    <div className="panel">
      <h3>Recent Fires</h3>
      <table className="data">
        <thead><tr><th>Time</th><th>Sym</th><th>Family</th><th>Dir</th><th>Note</th><th>Status</th></tr></thead>
        <tbody>
          {SIGNALS.length > 0 ? SIGNALS.map((s, i) => (
            <tr key={i}>
              <td style={{color:'var(--ink-3)'}}>{s.t}</td>
              <td className="sym">{s.sym}</td>
              <td><Pill kind={s.type==='REVERSAL'?'danger':s.type==='FLOW'?'warn':'info'}>{s.type}</Pill></td>
              <td className={s.dir==='LONG'?'up-c':'dn-c'}>{s.dir}</td>
              <td style={{color:'var(--ink-2)'}}>{s.note}</td>
              <td><Pill kind="info">LOGGED</Pill></td>
            </tr>
          )) : (
            <tr><td colSpan="6" style={{color:'var(--ink-3)', textAlign:'center', padding:'20px 0'}}>No signals yet this week</td></tr>
          )}
        </tbody>
      </table>
    </div>
  </>
);

const DetailScanner = () => (
  <>
    <DHead
      eyebrow="Neuron · N-08 · Rank"
      title="Top 10 Scanner"
      desc="Composite score across momentum, RVOL, setup quality and structure. Refreshes each cycle."
      kpis={(() => {
        const qualified = SCANNER.filter(r => r.score >= 50).length;
        const longBias  = SCANNER.filter(r => r.up).length;
        return [
          {label:'Scanned', value: SCANNER.length ? SCANNER.length : '--', delta:'by yf_score'},
          {label:'Score ≥ 50', value: qualified, delta:`${SCANNER.length ? Math.round(qualified/SCANNER.length*100) : 0}% pass`},
          {label:'Bullish', value: `${longBias} / ${SCANNER.length}`, delta:`${SCANNER.length ? Math.round(longBias/SCANNER.length*100) : 0}% long bias`},
          {label:'Next Refresh', value: SCAN.next, delta:'auto'},
        ];
      })()}
    />
    <div className="panel">
      <h3>Ranked Symbols</h3>
      <table className="data">
        <thead><tr><th>#</th><th>Sym</th><th>Score</th><th className="n">Price</th><th className="n">RVOL</th><th className="n">Mom</th><th>Trend</th><th>Signal</th></tr></thead>
        <tbody>
          {SCANNER.map(r => {
            const trend = Array.from({length:20}, () => Math.random() * 20 + (r.up ? 8 : -8) + 10);
            return (
              <tr key={r.sym}>
                <td style={{color:'var(--ink-3)'}}>{r.rank}</td>
                <td className="sym">{r.sym}</td>
                <td>
                  <span style={{fontFamily:'JetBrains Mono', marginRight:8, color:'var(--ink-0)'}}>{r.score}</span>
                  <div className="score-bar"><span style={{width: r.score+'%'}}/></div>
                </td>
                <td className="n">${r.px.toFixed(2)}</td>
                <td className="n">{r.vol.toFixed(1)}×</td>
                <td className={`n ${r.up?'up-c':'dn-c'}`}>{r.mom}</td>
                <td style={{width:100}}><Sparkline data={trend} w={100} h={18} color={r.up?'var(--up)':'var(--down)'} /></td>
                <td>{r.score >= 70 ? <Pill kind="info">BREAK</Pill> : r.score >= 60 ? <Pill kind="info">MOM</Pill> : <Pill>watch</Pill>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  </>
);

const DetailClosed = () => (
  <>
    <DHead
      eyebrow="Neuron · N-09 · Journal"
      title="Closed Trades"
      desc="Journal of closed positions for the week. Feeds the P&L attribution engine and hit-rate telemetry."
      kpis={(() => {
        const wins   = CLOSED.filter(c => c.pl > 0);
        const losses = CLOSED.filter(c => c.pl < 0);
        const net    = CLOSED.reduce((a, c) => a + c.pl, 0);
        const best   = wins.length   ? wins.reduce((a, c) => c.pl > a.pl ? c : a)   : null;
        const worst  = losses.length ? losses.reduce((a, c) => c.pl < a.pl ? c : a) : null;
        return [
          {label:'Total Trades', value: CLOSED.length, delta: `${wins.length}W · ${losses.length}L`},
          {label:'Net P&L', value: `${net >= 0 ? '+' : ''}$${Math.abs(net).toLocaleString()}`, delta: 'per-share basis'},
          {label:'Best', value: best ? `+$${best.pl}` : '--', delta: best ? best.sym : ''},
          {label:'Worst', value: worst ? `$${worst.pl}` : '--', delta: worst ? worst.sym : ''},
        ];
      })()}
    />
    <div className="panel">
      <h3>Trade Journal</h3>
      <table className="data">
        <thead><tr><th>Sym</th><th>Dir</th><th className="n">Entry</th><th className="n">Exit</th><th className="n">P&L</th><th>Date</th><th>Setup</th><th>Notes</th></tr></thead>
        <tbody>
          {CLOSED.map((t, i) => (
            <tr key={i}>
              <td className="sym">{t.sym}</td>
              <td className={t.dir==='L'?'up-c':'dn-c'}>{t.dir==='L'?'LONG':'SHORT'}</td>
              <td className="n">{t.entry.toFixed(2)}</td>
              <td className="n">{t.exit.toFixed(2)}</td>
              <td className={`n ${t.pl>=0?'up-c':'dn-c'}`}>{t.pl>=0?'+':''}${t.pl}</td>
              <td style={{color:'var(--ink-3)'}}>{t.date}</td>
              <td><Pill kind="info">{['BREAK','MOM','FLIP','REV','GAP'][i%5]}</Pill></td>
              <td style={{color:'var(--ink-2)'}}>{['Clean break of CW','RVOL 1.9 + trend','Failed retest','Gap-and-go','Held MA20'][i%5]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </>
);

const DETAIL_RENDERERS = {
  pulse: DetailPulse,
  scan: DetailScan,
  pl: DetailPL,
  positions: DetailPositions,
  chart: DetailChart,
  ladder: DetailLadder,
  signals: DetailSignals,
  scanner: DetailScanner,
  closed: DetailClosed,
};

Object.assign(window, { DETAIL_RENDERERS });
