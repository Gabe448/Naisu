const { useState, useEffect, useRef, useMemo, useCallback } = React;

// defaults for tweaks
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accentHue": 255,
  "showEdges": true,
  "showParticles": true,
  "density": "dense"
}/*EDITMODE-END*/;

const TopBar = () => {
  const [time, setTime] = useState(new Date());
  useEffect(() => { const t = setInterval(() => setTime(new Date()), 1000); return () => clearInterval(t); }, []);
  return (
    <div className="topbar">
      <div className="brand">
        <span className="brand-dot" />
        NAISU <span style={{color:'var(--ink-3)', letterSpacing:'0.05em', fontWeight:400}}>· neural tape</span>
      </div>
      <div className="ticker">
        {TICKERS.map(t => (
          <div className="tick" key={t.sym}>
            <span className="sym">{t.sym}</span>
            <span className="px">{t.px.toLocaleString()}</span>
            <span className={`ch ${t.ch>=0?'up-c':'dn-c'}`}>{t.ch>=0?'+':''}{t.ch.toFixed(2)}%</span>
          </div>
        ))}
      </div>
      <div className="session">
        <span className="live"><span className="live-dot"/> LIVE</span>
        <span>SESSION · RTH</span>
        <span>{time.toTimeString().slice(0, 8)}</span>
      </div>
    </div>
  );
};

const SubBar = ({ active, setActive }) => (
  <div className="subbar">
    <div className="crumbs">
      <span className={`crumb ${!active ? 'active' : ''}`} onClick={() => setActive(null)}>CONSTELLATION</span>
      {active && <>
        <span className="crumb-sep">/</span>
        <span className="crumb active">{NEURONS.find(n => n.id === active)?.title.toUpperCase()}</span>
      </>}
    </div>
    <div style={{display:'flex', gap:18}}>
      <span>NODES {NEURONS.length}</span>
      <span>EDGES {EDGES.length}</span>
      <span style={{color:'var(--cyan)'}}>◉ SYNCED</span>
    </div>
  </div>
);

const StatusBar = () => (
  <div className="statusbar">
    <div>naisu // neural-tape v0.9 // spx-bot</div>
    <div className="right">
      <span className="ok">⬤ api live</span>
      <span>signals {SCAN.fired}/{SCAN.cap}</span>
      <span>next scan {SCAN.next}</span>
      <span style={{color: POSITIONS.length > 0 ? 'var(--cyan)' : 'var(--ink-3)'}}>
        {POSITIONS.length > 0 ? `${POSITIONS.length} open` : 'no positions'}
      </span>
    </div>
  </div>
);

// The constellation view — responsive fit using direct pixel positions
const Constellation = ({ onSelect, hovered, setHovered }) => {
  const wrapRef = useRef(null);
  const [box, setBox] = useState({ w: 900, h: 600 });

  useEffect(() => {
    const update = () => {
      if (!wrapRef.current) return;
      const r = wrapRef.current.getBoundingClientRect();
      setBox({ w: r.width, h: r.height });
    };
    update();
    const ro = new ResizeObserver(update);
    if (wrapRef.current) ro.observe(wrapRef.current);
    window.addEventListener('resize', update);
    return () => { ro.disconnect(); window.removeEventListener('resize', update); };
  }, []);

  const W = box.w, H = box.h;
  const PAD = 12, GAP = 12;
  const colW = (W - PAD * 2 - GAP * 2) / 3;
  const leftW = Math.max(220, colW * 0.78);
  const midW = Math.max(280, colW * 1.22);
  const rightW = Math.max(220, colW);
  const totalW = leftW + midW + rightW + GAP * 2;
  const startX = (W - totalW) / 2;
  const rowH = (H - PAD * 2 - GAP * 2) / 3;

  const L = {
    pulse:     { x: startX, y: PAD, w: leftW, h: rowH },
    scan:      { x: startX, y: PAD + rowH + GAP, w: leftW, h: rowH },
    pl:        { x: startX, y: PAD + (rowH + GAP) * 2, w: leftW, h: rowH },
    positions: { x: startX + leftW + GAP, y: PAD, w: midW, h: rowH * 0.7 },
    chart:     { x: startX + leftW + GAP, y: PAD + rowH * 0.7 + GAP, w: midW, h: rowH * 1.3 + GAP },
    scanner:   { x: startX + leftW + GAP, y: PAD + (rowH + GAP) * 2, w: midW, h: rowH },
    ladder:    { x: startX + leftW + midW + GAP * 2, y: PAD, w: rightW, h: rowH * 1.5 + GAP },
    signals:   { x: startX + leftW + midW + GAP * 2, y: PAD + rowH * 1.5 + GAP * 2, w: rightW, h: rowH * 0.5 },
    closed:    { x: startX + leftW + midW + GAP * 2, y: PAD + (rowH + GAP) * 2, w: rightW, h: rowH },
  };

  const posOf = (n) => {
    const p = L[n.id];
    return { left: p.x, top: p.y, w: p.w, h: p.h, cx: p.x + p.w / 2, cy: p.y + p.h / 2 };
  };

  const nodeIndex = Object.fromEntries(NEURONS.map(n => [n.id, n]));

  const edgeData = EDGES.map(([a, b]) => {
    const A = posOf(nodeIndex[a]);
    const B = posOf(nodeIndex[b]);
    const dx = B.cx - A.cx, dy = B.cy - A.cy;
    const horiz = Math.abs(dx) > Math.abs(dy);
    let x1, y1, x2, y2;
    if (horiz) {
      if (dx > 0) { x1 = A.left + A.w; y1 = A.cy; x2 = B.left; y2 = B.cy; }
      else { x1 = A.left; y1 = A.cy; x2 = B.left + B.w; y2 = B.cy; }
    } else {
      if (dy > 0) { x1 = A.cx; y1 = A.top + A.h; x2 = B.cx; y2 = B.top; }
      else { x1 = A.cx; y1 = A.top; x2 = B.cx; y2 = B.top + B.h; }
    }
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    const cx = mx + (horiz ? 0 : (dx * 0.15));
    const cy = my + (horiz ? (dy * 0.15) : 0);
    const d = `M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`;
    const active = hovered && (a === hovered || b === hovered);
    return { a, b, d, active, x1, y1, x2, y2 };
  });

  return (
    <div ref={wrapRef} style={{ position:'absolute', inset:0, overflow:'hidden' }}>
    <div className="constellation" style={{ width: '100%', height: '100%', position:'relative' }}>
      <svg className="edges" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{width:'100%', height:'100%', position:'absolute', inset:0}}>
        <defs>
          <radialGradient id="glow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="var(--cyan)" stopOpacity="0.9" />
            <stop offset="100%" stopColor="var(--cyan)" stopOpacity="0" />
          </radialGradient>
        </defs>
        {edgeData.map((e, i) => (
          <g key={i}>
            <path d={e.d} className={`edge ${e.active ? 'active' : ''}`} />
            <circle cx={e.x1} cy={e.y1} r={e.active ? 2.5 : 1.5} fill={e.active ? 'var(--cyan)' : 'oklch(0.5 0.12 240 / 0.7)'} />
            <circle cx={e.x2} cy={e.y2} r={e.active ? 2.5 : 1.5} fill={e.active ? 'var(--cyan)' : 'oklch(0.5 0.12 240 / 0.7)'} />
          </g>
        ))}
        {edgeData.filter((_, i) => i % 2 === 0).map((e, i) => (
          <circle key={'p'+i} r="2" className="pulse-dot">
            <animateMotion dur={`${4 + (i % 3)}s`} repeatCount="indefinite" path={e.d} />
          </circle>
        ))}
      </svg>

      {NEURONS.map(n => {
        const p = posOf(n);
        const R = NEURON_RENDERERS[n.kind];
        return (
          <div
            key={n.id}
            className={`neuron ${hovered===n.id?'active':''}`}
            style={{ left: p.left, top: p.top, width: p.w, height: p.h }}
            onClick={() => onSelect(n.id)}
            onMouseEnter={() => setHovered(n.id)}
            onMouseLeave={() => setHovered(null)}
          >
            <span className="synapse t" />
            <span className="synapse b" />
            <span className="synapse l" />
            <span className="synapse r" />
            <R />
          </div>
        );
      })}
    </div>
    </div>
  );
};

const Detail = ({ active, setActive }) => {
  const Comp = active ? DETAIL_RENDERERS[active] : null;
  return (
    <div className={`detail ${active ? 'open' : ''}`}>
      <div className="minimap">
        <h4>
          Neurons
          <button className="mm-close" onClick={() => setActive(null)}>◀ back</button>
        </h4>
        {NEURONS.map((n, i) => (
          <div
            key={n.id}
            className={`mm-item ${active===n.id?'active':''}`}
            onClick={() => setActive(n.id)}
          >
            <span className="idx">N-{(i+1).toString().padStart(2,'0')}</span>
            <span className="mm-dot" />
            <span>{n.title}</span>
          </div>
        ))}
      </div>
      <div className="detail-stage">
        {Comp && <Comp />}
      </div>
    </div>
  );
};

const Tweaks = () => {
  const [on, setOn] = useState(false);
  const [vals, setVals] = useState(TWEAK_DEFAULTS);

  useEffect(() => {
    const handler = (e) => {
      if (e.data?.type === '__activate_edit_mode') setOn(true);
      if (e.data?.type === '__deactivate_edit_mode') setOn(false);
    };
    window.addEventListener('message', handler);
    window.parent.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', handler);
  }, []);

  const apply = (patch) => {
    const next = { ...vals, ...patch };
    setVals(next);
    window.parent.postMessage({ type: '__edit_mode_set_keys', edits: patch }, '*');
  };

  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--blue', `oklch(0.68 0.17 ${vals.accentHue})`);
    root.style.setProperty('--indigo', `oklch(0.55 0.18 ${parseInt(vals.accentHue)+20})`);
    root.style.setProperty('--cyan', `oklch(0.82 0.14 ${Math.max(180, parseInt(vals.accentHue)-40)})`);
    document.body.dataset.edges = vals.showEdges ? '1' : '0';
    document.body.dataset.density = vals.density;
  }, [vals]);

  return (
    <div className={`tweaks ${on ? 'on' : ''}`}>
      <h4>◉ Tweaks</h4>
      <div className="tweak-row">
        <label>Accent hue</label>
        <input type="range" min="200" max="300" step="5" value={vals.accentHue}
          onChange={e => apply({ accentHue: parseInt(e.target.value) })} />
      </div>
      <div className="tweak-row">
        <label>Edge glow</label>
        <select value={vals.showEdges ? 'on' : 'off'} onChange={e => apply({ showEdges: e.target.value === 'on' })}>
          <option value="on">on</option><option value="off">off</option>
        </select>
      </div>
      <div className="tweak-row">
        <label>Particles</label>
        <select value={vals.showParticles ? 'on' : 'off'} onChange={e => apply({ showParticles: e.target.value === 'on' })}>
          <option value="on">on</option><option value="off">off</option>
        </select>
      </div>
      <div className="tweak-row">
        <label>Density</label>
        <select value={vals.density} onChange={e => apply({ density: e.target.value })}>
          <option value="comfy">comfy</option>
          <option value="dense">dense</option>
        </select>
      </div>
    </div>
  );
};

const App = () => {
  const [entered, setEntered] = useState(() => {
    try { return sessionStorage.getItem('naisu.entered') === '1'; } catch { return false; }
  });
  const [active, setActive] = useState(() => {
    try { return localStorage.getItem('naisu.active') || null; } catch { return null; }
  });
  const [hovered, setHovered] = useState(null);
  // increments whenever live data arrives, forcing a re-render so all
  // components pick up the updated window.TICKERS / CANDLES / etc.
  const [dataVersion, setDataVersion] = useState(0);

  useEffect(() => {
    const onData = () => setDataVersion(v => v + 1);
    window.addEventListener('naisu:data-loaded', onData);
    return () => window.removeEventListener('naisu:data-loaded', onData);
  }, []);

  useEffect(() => {
    try {
      if (active) localStorage.setItem('naisu.active', active);
      else localStorage.removeItem('naisu.active');
    } catch {}
  }, [active]);

  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') setActive(null); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);

  const handleEnter = () => {
    try { sessionStorage.setItem('naisu.entered', '1'); } catch {}
    setEntered(true);
  };

  if (!entered) {
    return <BrainEntry onEnter={handleEnter} />;
  }

  return (
    <div className="app from-brain" data-screen-label="Dashboard">
      <TopBar />
      <SubBar active={active} setActive={setActive} />
      <div className="viewport">
        <div className={`canvas ${active ? 'fade-out' : ''}`}>
          <Constellation onSelect={setActive} hovered={hovered} setHovered={setHovered} />
        </div>
        <Detail active={active} setActive={setActive} />
      </div>
      <StatusBar />
      <Tweaks />
    </div>
  );
};

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
