// Entry scene — abstract 3D brain made of neuron nodes + synapse lines
// Pure SVG/CSS pseudo-3D: points distributed on a brain-ish shape, rotating,
// connected by nearest-neighbor synapses. Click to zoom into the dashboard.

const { useState, useEffect, useRef } = React;

// Generate ~180 points roughly shaped like two brain hemispheres
function buildBrainPoints() {
  const pts = [];
  const N = 220;
  for (let i = 0; i < N; i++) {
    // two overlapping ellipsoidal hemispheres
    const side = i < N/2 ? -1 : 1;
    const u = Math.random(), v = Math.random();
    const theta = 2 * Math.PI * u;
    const phi = Math.acos(2 * v - 1);
    // radius with folds: modulate by sin for lobes
    const fold = 0.85 + 0.15 * Math.sin(phi * 6) * Math.cos(theta * 4);
    const rx = 1.1 * fold;
    const ry = 0.85 * fold;
    const rz = 0.95 * fold;
    let x = rx * Math.sin(phi) * Math.cos(theta);
    let y = ry * Math.sin(phi) * Math.sin(theta);
    let z = rz * Math.cos(phi);
    // shift hemispheres apart slightly on X to give brain cleft
    x = x * 0.7 + side * 0.35;
    // slight downward tilt
    y = y + Math.sin(x * 2) * 0.05;
    pts.push({ x, y, z, r: 1.1 + Math.random() * 1.8, seed: Math.random() });
  }
  return pts;
}

// Precompute edges — nearest 2 neighbors within range
function buildEdges(pts) {
  const edges = [];
  const seen = new Set();
  for (let i = 0; i < pts.length; i++) {
    const dists = [];
    for (let j = 0; j < pts.length; j++) {
      if (i === j) continue;
      const dx = pts[i].x - pts[j].x;
      const dy = pts[i].y - pts[j].y;
      const dz = pts[i].z - pts[j].z;
      const d = Math.sqrt(dx*dx + dy*dy + dz*dz);
      if (d < 0.35) dists.push([d, j]);
    }
    dists.sort((a, b) => a[0] - b[0]);
    for (let k = 0; k < Math.min(2, dists.length); k++) {
      const j = dists[k][1];
      const key = i < j ? `${i}-${j}` : `${j}-${i}`;
      if (!seen.has(key)) { seen.add(key); edges.push([i, j]); }
    }
  }
  return edges;
}

const BrainEntry = ({ onEnter }) => {
  const [points] = useState(buildBrainPoints);
  const [edges] = useState(() => buildEdges(points));
  const [t, setT] = useState(0);
  const [hovered, setHovered] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const rafRef = useRef(0);
  const startRef = useRef(performance.now());

  useEffect(() => {
    const loop = () => {
      setT((performance.now() - startRef.current) / 1000);
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, []);

  const ry = t * 0.35 + (hovered ? 0.15 : 0);
  const rx = Math.sin(t * 0.18) * 0.15 - 0.1;

  // rotate & project
  const cosX = Math.cos(rx), sinX = Math.sin(rx);
  const cosY = Math.cos(ry), sinY = Math.sin(ry);

  const projected = points.map((p, i) => {
    // rotate Y
    let x = p.x * cosY + p.z * sinY;
    let z = -p.x * sinY + p.z * cosY;
    // rotate X
    let y = p.y * cosX - z * sinX;
    z = p.y * sinX + z * cosX;
    // perspective
    const d = 3.5;
    const persp = d / (d + z);
    const sx = x * persp;
    const sy = y * persp;
    // depth-based color/size
    const depth = (z + 1.5) / 3; // 0..1
    return { sx, sy, z, persp, i, depth, r: p.r, seed: p.seed };
  });

  const size = 640;
  const cx = size/2, cy = size/2;
  const scale = size * 0.28;

  const svgPts = projected.map(p => ({ ...p, ax: cx + p.sx * scale, ay: cy + p.sy * scale }));

  const handleClick = () => {
    if (leaving) return;
    setLeaving(true);
    setTimeout(onEnter, 1100);
  };

  return (
    <div className={`brain-entry ${leaving ? 'leaving' : ''}`}>
      <div className="brain-bg" />
      <div className="brain-eyebrow">
        <span className="brand-dot" /> NAISU · neural tape <span style={{color:'var(--ink-3)'}}>// v0.9.042</span>
      </div>
      <div className="brain-title">
        <div className="brain-kicker">Cognitive Trading Surface</div>
        <h1>Enter the network.</h1>
        <p>Nine neurons. Fifteen synapses. One live tape of the market.</p>
      </div>

      <div
        className={`brain-stage ${hovered ? 'hot' : ''}`}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onClick={handleClick}
      >
        <div className="brain-halo" />
        <div className="brain-halo brain-halo-2" />
        <svg viewBox={`0 0 ${size} ${size}`} width="640" height="640" style={{display:'block'}}>
          <defs>
            <radialGradient id="glow-core" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="oklch(0.82 0.14 215)" stopOpacity="0.5" />
              <stop offset="60%" stopColor="oklch(0.55 0.18 275)" stopOpacity="0.15" />
              <stop offset="100%" stopColor="transparent" />
            </radialGradient>
            <radialGradient id="node-grad" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#eaf7ff" stopOpacity="1" />
              <stop offset="60%" stopColor="oklch(0.82 0.14 215)" stopOpacity="1" />
              <stop offset="100%" stopColor="oklch(0.55 0.18 275)" stopOpacity="0" />
            </radialGradient>
            <filter id="soft-glow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="2" />
            </filter>
          </defs>
          {/* core glow */}
          <circle cx={cx} cy={cy} r={size * 0.28} fill="url(#glow-core)" />

          {/* edges sorted back-to-front */}
          {edges
            .map(([a, b]) => {
              const pa = svgPts[a], pb = svgPts[b];
              const avgZ = (pa.z + pb.z) / 2;
              const depth = (avgZ + 1.5) / 3;
              return { pa, pb, avgZ, depth };
            })
            .sort((e1, e2) => e1.avgZ - e2.avgZ)
            .map((e, i) => {
              const opacity = 0.08 + e.depth * 0.45;
              const width = 0.3 + e.depth * 0.6;
              const hue = 215 + (e.depth - 0.5) * 60;
              return (
                <line
                  key={i}
                  x1={e.pa.ax} y1={e.pa.ay}
                  x2={e.pb.ax} y2={e.pb.ay}
                  stroke={`oklch(${0.6 + e.depth * 0.25} 0.15 ${hue})`}
                  strokeWidth={width}
                  strokeOpacity={opacity}
                />
              );
            })
          }

          {/* nodes sorted back-to-front */}
          {svgPts
            .slice()
            .sort((a, b) => a.z - b.z)
            .map((p, k) => {
              const r = p.r * (0.5 + p.depth * 1.2);
              const pulsePhase = Math.sin(t * 1.2 + p.seed * 6.28) * 0.5 + 0.5;
              const opacity = 0.35 + p.depth * 0.55 + pulsePhase * 0.1;
              const hue = 210 + p.depth * 40 + (pulsePhase - 0.5) * 30;
              return (
                <g key={p.i}>
                  {p.depth > 0.55 && (
                    <circle cx={p.ax} cy={p.ay} r={r * 2.2}
                      fill={`oklch(0.75 0.18 ${hue})`}
                      opacity={0.15 * pulsePhase}
                      filter="url(#soft-glow)"
                    />
                  )}
                  <circle
                    cx={p.ax} cy={p.ay} r={r}
                    fill={`oklch(${0.7 + p.depth * 0.25} 0.15 ${hue})`}
                    opacity={opacity}
                  />
                </g>
              );
            })
          }

          {/* traveling synapse pulses (few) */}
          {edges.slice(0, 18).map(([a, b], i) => {
            const pa = svgPts[a], pb = svgPts[b];
            const d = `M ${pa.ax} ${pa.ay} L ${pb.ax} ${pb.ay}`;
            const dur = 2 + (i % 4);
            return (
              <circle key={'pulse' + i} r="1.6" fill="#eaf7ff" opacity="0.85">
                <animateMotion dur={`${dur}s`} repeatCount="indefinite" path={d} />
              </circle>
            );
          })}
        </svg>
      </div>

      <div className="brain-cta" onClick={handleClick}>
        <span className="brain-cta-dot" />
        <span>ENGAGE</span>
        <span className="brain-cta-caret">⟶</span>
      </div>

      <div className="brain-footer">
        <span>◎ 412 symbols observed</span>
        <span>◎ 9 active neurons</span>
        <span>◎ feed heartbeat 0.48s</span>
      </div>
    </div>
  );
};

window.BrainEntry = BrainEntry;
