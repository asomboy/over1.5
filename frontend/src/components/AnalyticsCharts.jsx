import React, { useMemo } from 'react';

function poissonPmf(k, lambda) {
  if (!Number.isFinite(lambda) || lambda <= 0) return k === 0 ? 1 : 0;
  let fact = 1;
  for (let i = 2; i <= k; i += 1) fact *= i;
  return (Math.pow(lambda, k) * Math.exp(-lambda)) / fact;
}

export function TeamFormPills({ form = [], compact = false }) {
  if (!Array.isArray(form) || form.length === 0) return null;
  const items = form.slice(0, 5);
  return (
    <span className="inline-flex items-center gap-0.5" title={`Last ${items.length} matches: ${items.join('-')}`}>
      {items.map((res, idx) => {
        const ch = String(res || '').toUpperCase();
        const cls =
          ch === 'W'
            ? 'bg-emerald-500/25 text-emerald-300 border-emerald-500/40'
            : ch === 'L'
            ? 'bg-rose-500/25 text-rose-300 border-rose-500/40'
            : 'bg-amber-500/20 text-amber-300 border-amber-500/35';
        return (
          <span
            key={`${ch}-${idx}`}
            className={`${
              compact ? 'w-3.5 h-3.5 text-[8px]' : 'w-4 h-4 text-[9px]'
            } rounded font-mono font-black border inline-flex items-center justify-center leading-none ${cls}`}
          >
            {ch}
          </span>
        );
      })}
    </span>
  );
}

export function ScorelineHeatmap({ exactScores = [], xgHome = 1.4, xgAway = 1.1, homeName = 'Home', awayName = 'Away', darkMode = true }) {
  const grid = useMemo(() => {
    const map = {};
    for (const item of exactScores || []) {
      const s = String(item.score || '');
      const parts = s.split('-').map(Number);
      const h = Number.isFinite(item.home) ? item.home : parts[0];
      const a = Number.isFinite(item.away) ? item.away : parts[1];
      if (Number.isFinite(h) && Number.isFinite(a) && h >= 0 && h <= 4 && a >= 0 && a <= 4) {
        map[`${h}-${a}`] = Number(item.probability || 0);
      }
    }

    let maxProb = 0.01;
    const cells = [];
    const lh = Number.isFinite(Number(xgHome)) && Number(xgHome) > 0 ? Number(xgHome) : 1.35;
    const la = Number.isFinite(Number(xgAway)) && Number(xgAway) > 0 ? Number(xgAway) : 1.05;

    for (let h = 0; h <= 4; h += 1) {
      const row = [];
      for (let a = 0; a <= 4; a += 1) {
        const key = `${h}-${a}`;
        const prob = map[key] !== undefined ? map[key] : poissonPmf(h, lh) * poissonPmf(a, la);
        if (prob > maxProb) maxProb = prob;
        row.push({ h, a, key, prob });
      }
      cells.push(row);
    }
    return { cells, maxProb };
  }, [exactScores, xgHome, xgAway]);

  return (
    <div className={`p-4 rounded-2xl border space-y-2.5 ${darkMode ? 'bg-slate-950/70 border-slate-800' : 'bg-white border-slate-200'}`}>
      <div className="flex items-center justify-between gap-2">
        <span className={`text-xs font-black uppercase tracking-wider ${darkMode ? 'text-white' : 'text-slate-900'}`}>
          2D Scoreline Probability Matrix (0–4 Goals)
        </span>
        <span className="text-[10px] font-semibold text-slate-400">
          Rows: {homeName} · Cols: {awayName}
        </span>
      </div>

      <div className="overflow-x-auto">
        <div className="min-w-[280px] grid grid-cols-6 gap-1 text-center text-[10px] font-mono">
          <div className="py-1 text-slate-500 font-bold">H \ A</div>
          {[0, 1, 2, 3, 4].map((a) => (
            <div key={`col-${a}`} className="py-1 font-black text-cyan-400">
              {a}
            </div>
          ))}

          {grid.cells.map((row, h) => (
            <React.Fragment key={`row-${h}`}>
              <div className="py-2 font-black text-emerald-400 flex items-center justify-center">{h}</div>
              {row.map((cell) => {
                const intensity = Math.min(1, Math.max(0.06, cell.prob / grid.maxProb));
                const isPeak = Math.abs(cell.prob - grid.maxProb) < 0.0005;
                const pct = (cell.prob * 100).toFixed(1);
                return (
                  <div
                    key={cell.key}
                    style={{
                      backgroundColor: isPeak
                        ? 'rgba(16, 185, 129, 0.38)'
                        : `rgba(6, 182, 212, ${(intensity * 0.32).toFixed(2)})`,
                    }}
                    className={`py-2 px-1 rounded-lg border transition-all ${
                      isPeak
                        ? 'border-emerald-400 font-black text-emerald-200 ring-1 ring-emerald-400/50'
                        : darkMode
                        ? 'border-slate-800/80 text-slate-200'
                        : 'border-slate-200 text-slate-800'
                    }`}
                    title={`${homeName} ${cell.h} - ${cell.a} ${awayName}: ${pct}%`}
                  >
                    <div className="text-[9px] opacity-70">{cell.key}</div>
                    <div className="font-bold">{pct}%</div>
                  </div>
                );
              })}
            </React.Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}

export function BankrollEquityChart({ equityCurve = [], marketBreakdown = [], darkMode = true }) {
  const hasCurve = Array.isArray(equityCurve) && equityCurve.length >= 2;
  const chartGeom = useMemo(() => {
    if (!hasCurve) return null;
    const width = 520;
    const height = 130;
    const padX = 24;
    const padY = 18;
    const vals = equityCurve.map((pt) => Number(pt.equity || 0));
    const minVal = Math.min(0, ...vals);
    const maxVal = Math.max(1, ...vals);
    const span = Math.max(1, maxVal - minVal);

    const toX = (idx) => padX + (idx / Math.max(1, equityCurve.length - 1)) * (width - padX * 2);
    const toY = (v) => height - padY - ((v - minVal) / span) * (height - padY * 2);

    const zeroY = toY(0);
    const pts = equityCurve.map((pt, idx) => ({
      x: Number(toX(idx).toFixed(1)),
      y: Number(toY(Number(pt.equity || 0)).toFixed(1)),
      equity: Number(pt.equity || 0),
      label: pt.label || `#${idx}`,
    }));
    const linePath = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ');
    const areaPath = `${linePath} L ${pts[pts.length - 1].x} ${zeroY.toFixed(1)} L ${pts[0].x} ${zeroY.toFixed(1)} Z`;
    const endPositive = pts[pts.length - 1].equity >= 0;
    return { width, height, zeroY, pts, linePath, areaPath, endPositive };
  }, [equityCurve, hasCurve]);

  if (!hasCurve && (!marketBreakdown || marketBreakdown.length === 0)) return null;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {chartGeom && (
        <div className={`p-3.5 rounded-2xl border ${darkMode ? 'bg-slate-950/70 border-slate-800' : 'bg-white border-slate-200'}`}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-black uppercase tracking-wider text-slate-400">
              Bankroll Equity Curve ({equityCurve.length - 1} Settled)
            </span>
            <span
              className={`text-xs font-mono font-black ${
                chartGeom.endPositive ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              {chartGeom.pts[chartGeom.pts.length - 1].equity >= 0
                ? `+$${chartGeom.pts[chartGeom.pts.length - 1].equity}`
                : `-$${Math.abs(chartGeom.pts[chartGeom.pts.length - 1].equity)}`}
            </span>
          </div>
          <svg viewBox={`0 0 ${chartGeom.width} ${chartGeom.height}`} className="w-full h-28 overflow-visible">
            <line
              x1="16"
              y1={chartGeom.zeroY}
              x2={chartGeom.width - 16}
              y2={chartGeom.zeroY}
              stroke="rgba(148, 163, 184, 0.3)"
              strokeDasharray="4 4"
              strokeWidth="1"
            />
            <path
              d={chartGeom.areaPath}
              fill={chartGeom.endPositive ? 'rgba(16, 185, 129, 0.16)' : 'rgba(244, 63, 94, 0.16)'}
            />
            <path
              d={chartGeom.linePath}
              fill="none"
              stroke={chartGeom.endPositive ? '#10b981' : '#f43f5e'}
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {chartGeom.pts.map((p, idx) => (
              <circle
                key={`${p.label}-${idx}`}
                cx={p.x}
                cy={p.y}
                r="3.2"
                fill={p.equity >= 0 ? '#34d399' : '#fb7185'}
              >
                <title>{`${p.label}: ${p.equity >= 0 ? `+$${p.equity}` : `-$${Math.abs(p.equity)}`}`}</title>
              </circle>
            ))}
          </svg>
        </div>
      )}

      {Array.isArray(marketBreakdown) && marketBreakdown.length > 0 && (
        <div className={`p-3.5 rounded-2xl border space-y-2 ${darkMode ? 'bg-slate-950/70 border-slate-800' : 'bg-white border-slate-200'}`}>
          <span className="text-[10px] font-black uppercase tracking-wider text-slate-400 block">
            Settled Hit Rate by Market
          </span>
          <div className="space-y-1.5">
            {marketBreakdown.slice(0, 4).map((m) => (
              <div key={m.market} className="space-y-0.5 text-[11px]">
                <div className="flex items-center justify-between font-bold">
                  <span className="truncate pr-2">{m.market}</span>
                  <span className="font-mono text-emerald-400">
                    {m.hit_rate_pct}% ({m.won}W/{m.lost}L)
                  </span>
                </div>
                <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-emerald-500 to-cyan-400 rounded-full"
                    style={{ width: `${Math.min(100, Math.max(4, m.hit_rate_pct))}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function CalibrationReliabilityChart({ buckets = [], darkMode = true }) {
  const validPoints = useMemo(() => {
    return (buckets || [])
      .filter((b) => Number(b.predictions_count || 0) > 0 && b.avg_predicted_prob != null && b.actual_event_rate != null)
      .map((b) => ({
        pred: Math.max(0, Math.min(1, Number(b.avg_predicted_prob))),
        actual: Math.max(0, Math.min(1, Number(b.actual_event_rate))),
        count: Number(b.predictions_count || 1),
        range: b.bucket_range,
      }));
  }, [buckets]);

  if (validPoints.length === 0) return null;

  const size = 240;
  const pad = 28;
  const toX = (v) => pad + v * (size - pad * 2);
  const toY = (v) => size - pad - v * (size - pad * 2);
  const polyPath = validPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${toX(p.pred).toFixed(1)} ${toY(p.actual).toFixed(1)}`).join(' ');

  return (
    <div className={`p-4 rounded-2xl border ${darkMode ? 'bg-slate-950/60 border-slate-800' : 'bg-white border-slate-200'}`}>
      <div className="flex items-center justify-between mb-2">
        <span className={`text-xs font-black uppercase tracking-wider ${darkMode ? 'text-white' : 'text-slate-900'}`}>
          Reliability Curve (Predicted vs Observed Hit Rate)
        </span>
        <span className="text-[10px] font-semibold text-slate-400">Dashed diagonal = Perfect Calibration</span>
      </div>
      <div className="flex justify-center">
        <svg viewBox={`0 0 ${size} ${size}`} className="w-full max-w-[260px] h-auto">
          <line x1={pad} y1={size - pad} x2={size - pad} y2={size - pad} stroke="rgba(148,163,184,0.35)" strokeWidth="1" />
          <line x1={pad} y1={pad} x2={pad} y2={size - pad} stroke="rgba(148,163,184,0.35)" strokeWidth="1" />
          <line
            x1={pad}
            y1={size - pad}
            x2={size - pad}
            y2={pad}
            stroke="rgba(148,163,184,0.5)"
            strokeDasharray="4 4"
            strokeWidth="1.2"
          />
          {validPoints.length >= 2 && (
            <path d={polyPath} fill="none" stroke="#06b6d4" strokeWidth="2.2" strokeLinecap="round" />
          )}
          {validPoints.map((pt, i) => {
            const r = Math.min(7, Math.max(3.5, Math.log2(pt.count + 1)));
            return (
              <circle key={`${pt.range}-${i}`} cx={toX(pt.pred)} cy={toY(pt.actual)} r={r} fill="#10b981" stroke="#020617" strokeWidth="1">
                <title>{`${pt.range}: Pred ${(pt.pred * 100).toFixed(1)}% vs Actual ${(pt.actual * 100).toFixed(1)}% (n=${pt.count})`}</title>
              </circle>
            );
          })}
          <text x={size / 2} y={size - 6} textAnchor="middle" fill="#94a3b8" fontSize="8.5" fontWeight="bold">
            Predicted Probability (0% – 100%)
          </text>
          <text
            x="10"
            y={size / 2}
            textAnchor="middle"
            fill="#94a3b8"
            fontSize="8.5"
            fontWeight="bold"
            transform={`rotate(-90 10 ${size / 2})`}
          >
            Observed Hit %
          </text>
        </svg>
      </div>
    </div>
  );
}
