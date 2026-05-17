// Reusable primitives

const Ring = ({ value, max = 100, label, size = 52, color = 'var(--cyan)' }) => {
  const r = size / 2 - 5;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(1, value / max));
  return (
    <div className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle cx={size/2} cy={size/2} r={r} stroke="oklch(0.3 0.08 250)" strokeWidth="3" fill="none" />
        <circle
          cx={size/2} cy={size/2} r={r}
          stroke={color} strokeWidth="3" fill="none"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          strokeLinecap="round"
          style={{ filter: 'drop-shadow(0 0 4px ' + color + ')' }}
        />
      </svg>
      <div className="ring-label">{label}</div>
    </div>
  );
};

const Sparkline = ({ data, w = 200, h = 28, color = 'var(--cyan)' }) => {
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const step = w / (data.length - 1);
  const pts = data.map((v, i) => [i * step, h - ((v - min) / range) * (h - 4) - 2]);
  const d = 'M ' + pts.map(p => p.join(' ')).join(' L ');
  const fill = d + ` L ${w} ${h} L 0 ${h} Z`;
  return (
    <svg className="sparkline" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <path d={fill} className="spark-fill" />
      <path d={d} className="spark-line" style={{ stroke: color }} />
    </svg>
  );
};

// Mini candlestick chart for the chart neuron preview
const MiniCandles = ({ w = 330, h = 70, candles = CANDLES.slice(-30) }) => {
  const all = candles.flatMap(c => [c.hi, c.lo]);
  const min = Math.min(...all), max = Math.max(...all);
  const range = max - min || 1;
  const step = w / candles.length;
  const body = step * 0.55;
  const y = (v) => ((max - v) / range) * h;
  return (
    <svg className="mini-chart" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      {candles.map((c, i) => {
        const up = c.c >= c.o;
        const x = i * step + step / 2;
        return (
          <g key={i}>
            <line x1={x} x2={x} y1={y(c.hi)} y2={y(c.lo)} stroke={up ? 'var(--up)' : 'var(--down)'} strokeWidth="0.6" opacity="0.8" />
            <rect
              x={x - body / 2}
              y={y(Math.max(c.o, c.c))}
              width={body}
              height={Math.max(1, Math.abs(y(c.o) - y(c.c)))}
              fill={up ? 'var(--up)' : 'var(--down)'}
            />
          </g>
        );
      })}
    </svg>
  );
};

// Full candle chart for expanded view
const BigCandles = ({ candles = CANDLES, levels = LEVELS }) => {
  const W = 820, H = 360, P = { t: 20, r: 70, b: 28, l: 40 };
  const iw = W - P.l - P.r, ih = H - P.t - P.b;
  const allPx = [...candles.flatMap(c => [c.hi, c.lo]), ...levels.map(l => l.px)];
  const min = Math.min(...allPx) - 1;
  const max = Math.max(...allPx) + 1;
  const range = max - min;
  const step = iw / candles.length;
  const body = step * 0.6;
  const y = (v) => P.t + ((max - v) / range) * ih;
  const x = (i) => P.l + i * step + step / 2;

  // simple moving averages
  const sma = (n) => candles.map((_, i) => {
    if (i < n - 1) return null;
    const slice = candles.slice(i - n + 1, i + 1);
    return slice.reduce((a, c) => a + c.c, 0) / n;
  });
  const ma9 = sma(9), ema20 = sma(20);
  const linePath = (arr) => {
    let d = '';
    arr.forEach((v, i) => {
      if (v == null) return;
      d += (d ? ' L ' : 'M ') + x(i) + ' ' + y(v);
    });
    return d;
  };

  // price grid lines
  const ticks = 6;
  const gridVals = Array.from({length: ticks+1}, (_, i) => min + (range * i / ticks));

  return (
    <svg className="candles" viewBox={`0 0 ${W} ${H}`}>
      {gridVals.map((v, i) => (
        <g key={i}>
          <line x1={P.l} x2={W - P.r} y1={y(v)} y2={y(v)} className="grid-line" strokeDasharray="2 4" />
          <text x={P.l - 6} y={y(v) + 3} textAnchor="end" className="price-label">{v.toFixed(0)}</text>
        </g>
      ))}

      {/* levels */}
      {levels.map((l, i) => {
        const color = l.kind === 'cw' ? 'var(--up)' : l.kind === 'pw' ? 'var(--down)' : l.kind === 'flip' ? 'var(--warn)' : 'var(--cyan)';
        return (
          <g key={i}>
            <line x1={P.l} x2={W - P.r} y1={y(l.px)} y2={y(l.px)} stroke={color} strokeWidth={l.kind==='spot'?1.4:0.8} opacity={l.kind==='spot'?0.7:0.35} strokeDasharray={l.kind==='flip'?'4 3':'none'} />
            <rect x={W - P.r + 2} y={y(l.px) - 8} width={62} height={16} fill={color} opacity="0.15" stroke={color} strokeOpacity="0.5" rx="2" />
            <text x={W - P.r + 8} y={y(l.px) + 3.5} fill={color} className="price-label" style={{fontWeight: 600}}>{l.tag} {l.px.toFixed(2)}</text>
          </g>
        );
      })}

      {candles.map((c, i) => {
        const up = c.c >= c.o;
        return (
          <g key={i}>
            <line x1={x(i)} x2={x(i)} y1={y(c.hi)} y2={y(c.lo)} stroke={up?'var(--up)':'var(--down)'} strokeWidth="1" />
            <rect
              x={x(i) - body/2}
              y={y(Math.max(c.o, c.c))}
              width={body}
              height={Math.max(1, Math.abs(y(c.o)-y(c.c)))}
              fill={up?'var(--up)':'var(--down)'}
              opacity={up?0.9:0.95}
            />
          </g>
        );
      })}

      <path d={linePath(ma9)} className="ma-line" />
      <path d={linePath(ema20)} className="ema-line" />
    </svg>
  );
};

const Pill = ({ children, kind = 'info' }) => (
  <span className={`badge ${kind}`}>{children}</span>
);

const ArrowNum = ({ v, prefix = '', suffix = '', digits = 2 }) => {
  const up = v >= 0;
  return (
    <span className={up ? 'up-c' : 'dn-c'}>
      {up ? '▲' : '▼'} {prefix}{Math.abs(v).toFixed(digits)}{suffix}
    </span>
  );
};

Object.assign(window, { Ring, Sparkline, MiniCandles, BigCandles, Pill, ArrowNum });
