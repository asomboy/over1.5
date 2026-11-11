import React, { useMemo, useState } from 'react';
import {
  Activity,
  ArrowUpRight,
  BellRing,
  CheckCircle2,
  Clock,
  Filter,
  Flame,
  Plus,
  Radio,
  RefreshCw,
  ShieldCheck,
  Star,
} from 'lucide-react';
import { extractQuickSlipLeg } from '../utils/alertUtils';

const formatPct = (prob) => {
  if (prob === null || prob === undefined || Number.isNaN(Number(prob))) return '—';
  return `${Math.round(Number(prob) * 100)}%`;
};

const formatXg = (val) => {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return '—';
  return Number(val).toFixed(2);
};

const getSignalMeta = (fixture) => {
  const goalSignals = Array.isArray(fixture?.goal_signals) ? fixture.goal_signals : [];
  const best = fixture?.best_signal || {};
  const picked =
    goalSignals.find((s) => s && s.market && s.market !== 'NO_SIGNAL') ||
    (best && best.market && best.market !== 'NO_SIGNAL' ? best : null);

  if (!picked) {
    return {
      isActive: false,
      strength: 'MONITORING',
      label: best?.reason ? 'Monitoring Gates' : 'Monitoring',
      prob: null,
      timeRem: null,
    };
  }

  return {
    isActive: true,
    strength: String(picked.signal_strength || picked.label || 'WATCH').toUpperCase(),
    label: picked.market,
    prob: picked.probability,
    timeRem: picked.time_remaining_minutes,
  };
};

const getMarketPillClass = (node, darkMode) => {
  if (!node || node.probability === null || node.probability === undefined) {
    return darkMode
      ? 'bg-slate-900/60 border-slate-800 text-slate-400'
      : 'bg-slate-50 border-slate-200 text-slate-500';
  }
  if (node.status === 'already_resolved') {
    return darkMode
      ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-300'
      : 'bg-emerald-50 border-emerald-200 text-emerald-700';
  }
  const p = Number(node.probability);
  if (p >= 0.72) {
    return darkMode
      ? 'bg-emerald-500/15 border-emerald-500/35 text-emerald-300'
      : 'bg-emerald-50 border-emerald-200 text-emerald-700';
  }
  if (p >= 0.55) {
    return darkMode
      ? 'bg-cyan-500/15 border-cyan-500/30 text-cyan-300'
      : 'bg-cyan-50 border-cyan-200 text-cyan-700';
  }
  if (p >= 0.35) {
    return darkMode
      ? 'bg-amber-500/10 border-amber-500/30 text-amber-300'
      : 'bg-amber-50 border-amber-200 text-amber-700';
  }
  return darkMode
    ? 'bg-slate-900/70 border-slate-800 text-slate-300'
    : 'bg-slate-50 border-slate-200 text-slate-700';
};

export default function LiveGoalRadarTab({
  liveFixtures = [],
  loadingLive = false,
  darkMode = true,
  onOpenLiveModal,
  onOpenDetailModal,
  onRefreshLive,
  watchlistIds = [],
  onToggleWatchlist,
  alertSettings = null,
  onOpenAlertSettings,
  onAddToBetSlip,
  livePerfData = null,
}) {
  const [signalsOnly, setSignalsOnly] = useState(false);
  const [starredOnly, setStarredOnly] = useState(false);

  const watchlistSet = useMemo(() => new Set((watchlistIds || []).map(Number)), [watchlistIds]);

  const passesCustomGate = (fix, meta) => {
    if (!meta?.isActive) return false;
    if (!alertSettings) {
      return meta.strength === 'STRONG' || meta.strength === 'MODERATE';
    }
    const prob = Number(meta.prob || 0);
    const conf = Number(fix?.confidence || 0);
    const rem = Number(meta.timeRem ?? 15);
    return (
      prob >= Number(alertSettings.minProbabilityPct || 74) / 100 &&
      conf >= Number(alertSettings.minConfidence || 70) &&
      rem >= Number(alertSettings.minTimeRemaining || 10)
    );
  };

  const stats = useMemo(() => {
    const activeSignals = liveFixtures.filter((f) => {
      const m = getSignalMeta(f);
      if (!m.isActive) return false;
      if (alertSettings) {
        return (
          Number(m.prob || 0) >= Number(alertSettings.minProbabilityPct || 74) / 100 &&
          Number(f?.confidence || 0) >= Number(alertSettings.minConfidence || 70) &&
          Number(m.timeRem ?? 15) >= Number(alertSettings.minTimeRemaining || 10)
        );
      }
      return m.strength === 'STRONG' || m.strength === 'MODERATE';
    }).length;
    const highPlusOne = liveFixtures.filter(
      (f) => Number(f?.live_goals?.at_least_1_more_goal?.probability || 0) >= 0.7
    ).length;
    const remXgValues = liveFixtures
      .map((f) => f?.live_goals?.remaining_xg?.total)
      .filter((v) => v !== null && v !== undefined && !Number.isNaN(Number(v)))
      .map(Number);
    const avgRemXg =
      remXgValues.length > 0
        ? remXgValues.reduce((acc, v) => acc + v, 0) / remXgValues.length
        : null;
    return { activeSignals, highPlusOne, avgRemXg };
  }, [liveFixtures, alertSettings]);

  const visibleFixtures = useMemo(() => {
    return liveFixtures.filter((f) => {
      if (starredOnly && !watchlistSet.has(Number(f.fixture_id))) return false;
      if (signalsOnly) {
        const m = getSignalMeta(f);
        if (!m.isActive) return false;
      }
      return true;
    });
  }, [liveFixtures, signalsOnly, starredOnly, watchlistSet]);

  const cardSurface = darkMode
    ? 'bg-slate-900/80 border-slate-800/90 hover:border-rose-500/40'
    : 'bg-white border-slate-200 hover:border-rose-300 shadow-sm';

  return (
    <div className="space-y-6">
      {/* Header Banner & KPIs */}
      <section
        className={`rounded-2xl border p-5 sm:p-6 transition-colors ${
          darkMode
            ? 'bg-gradient-to-br from-slate-900 via-slate-900 to-rose-950/20 border-slate-800'
            : 'bg-gradient-to-br from-white via-white to-rose-50/40 border-slate-200 shadow-sm'
        }`}
      >
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
          <div className="space-y-1.5">
            <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider bg-rose-500/15 text-rose-400 border border-rose-500/30">
              <Radio className="w-3.5 h-3.5 animate-pulse" />
              Real-Time Bayesian &amp; Poisson Goal Engine
            </div>
            <h2
              className={`text-xl sm:text-2xl font-extrabold tracking-tight ${
                darkMode ? 'text-white' : 'text-slate-900'
              }`}
            >
              Live Matches &amp; Goal Probability Radar
            </h2>
            <p className={`text-sm ${darkMode ? 'text-slate-400' : 'text-slate-600'}`}>
              Auto-detects in-play matches from ESPN, dynamically updates remaining xG from match
              clock and momentum, and surfaces validated live goal opportunities.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <button
              type="button"
              onClick={() => setStarredOnly((v) => !v)}
              className={`inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-bold border transition-colors ${
                starredOnly
                  ? 'bg-amber-500/20 border-amber-500/40 text-amber-300'
                  : darkMode
                  ? 'bg-slate-800/80 border-slate-700 text-slate-300 hover:border-slate-600'
                  : 'bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200'
              }`}
            >
              <Star className={`w-3.5 h-3.5 ${starredOnly ? 'fill-amber-400 text-amber-400' : ''}`} />
              Starred ({watchlistIds.length})
            </button>

            <button
              type="button"
              onClick={() => setSignalsOnly((v) => !v)}
              className={`inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-bold border transition-colors ${
                signalsOnly
                  ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300'
                  : darkMode
                  ? 'bg-slate-800/80 border-slate-700 text-slate-300 hover:border-slate-600'
                  : 'bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200'
              }`}
            >
              <Filter className="w-3.5 h-3.5" />
              {signalsOnly ? 'Active Signals Only' : 'Filter: All Live'}
            </button>

            {onOpenAlertSettings && (
              <button
                type="button"
                onClick={onOpenAlertSettings}
                className={`inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-bold border transition-colors ${
                  darkMode
                    ? 'bg-cyan-500/15 border-cyan-500/35 text-cyan-300 hover:bg-cyan-500/25'
                    : 'bg-cyan-50 border-cyan-200 text-cyan-700 hover:bg-cyan-100'
                }`}
              >
                <BellRing className="w-3.5 h-3.5" />
                Alerts ({alertSettings?.minProbabilityPct || 74}%+)
              </button>
            )}

            <button
              type="button"
              onClick={onRefreshLive}
              disabled={loadingLive}
              className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold border transition-colors ${
                darkMode
                  ? 'bg-rose-500/15 border-rose-500/35 text-rose-300 hover:bg-rose-500/25 disabled:opacity-50'
                  : 'bg-rose-50 border-rose-200 text-rose-700 hover:bg-rose-100 disabled:opacity-50'
              }`}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loadingLive ? 'animate-spin' : ''}`} />
              {loadingLive ? 'Scanning...' : 'Refresh Live'}
            </button>
          </div>
        </div>

        {/* KPI Grid */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-5">
          <div
            className={`rounded-xl border p-3.5 ${
              darkMode ? 'bg-slate-950/60 border-slate-800' : 'bg-slate-50 border-slate-200'
            }`}
          >
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              In-Play Matches
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className={`text-2xl font-black ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                {liveFixtures.length}
              </span>
              <span className="text-xs font-semibold text-rose-400">LIVE</span>
            </div>
          </div>

          <div
            className={`rounded-xl border p-3.5 ${
              darkMode ? 'bg-slate-950/60 border-slate-800' : 'bg-slate-50 border-slate-200'
            }`}
          >
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Goal Alerts Active
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-2xl font-black text-emerald-400">{stats.activeSignals}</span>
              <span className="text-xs text-slate-400">Strong / Moderate</span>
            </div>
          </div>

          <div
            className={`rounded-xl border p-3.5 ${
              darkMode ? 'bg-slate-950/60 border-slate-800' : 'bg-slate-50 border-slate-200'
            }`}
          >
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              +1 Goal &ge; 70%
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-2xl font-black text-cyan-400">{stats.highPlusOne}</span>
              <span className="text-xs text-slate-400">Matches</span>
            </div>
          </div>

          <div
            className={`rounded-xl border p-3.5 ${
              darkMode ? 'bg-slate-950/60 border-slate-800' : 'bg-slate-50 border-slate-200'
            }`}
          >
            <div className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
              Avg Remaining xG
            </div>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-2xl font-black text-amber-400">
                {stats.avgRemXg !== null ? stats.avgRemXg.toFixed(2) : '—'}
              </span>
              <span className="text-xs text-slate-400">Expected Goals Left</span>
            </div>
          </div>
        </div>

        {/* Settled Live Hit-Rate Strip */}
        {livePerfData && (livePerfData.settled_evaluations > 0 || (livePerfData.market_breakdown || []).length > 0) && (
          <div
            className={`mt-4 pt-3.5 border-t flex flex-wrap items-center justify-between gap-2 text-xs ${
              darkMode ? 'border-slate-800/80 text-slate-300' : 'border-slate-200 text-slate-700'
            }`}
          >
            <div className="flex items-center gap-2 font-bold">
              <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>
                Settled Live Accuracy:{' '}
                <strong className="text-emerald-400">
                  {livePerfData.overall_accuracy !== null && livePerfData.overall_accuracy !== undefined
                    ? `${Math.round(Number(livePerfData.overall_accuracy) * 100)}%`
                    : '—'}
                </strong>{' '}
                <span className="text-slate-400 font-normal">
                  ({livePerfData.settled_evaluations} graded signals across {livePerfData.settled_fixtures_count || 0} finished matches)
                </span>
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {(livePerfData.market_breakdown || []).slice(0, 4).map((mb) => (
                <span
                  key={mb.market}
                  className={`px-2 py-0.5 rounded-full text-[11px] font-bold border ${
                    darkMode
                      ? 'bg-slate-900 border-slate-700 text-cyan-300'
                      : 'bg-white border-slate-300 text-cyan-700'
                  }`}
                >
                  {mb.market.replace(/^live_/, '').replace(/_/g, ' ').toUpperCase()}:{' '}
                  {mb.accuracy !== null && mb.accuracy !== undefined ? `${Math.round(mb.accuracy * 100)}%` : '—'} ({mb.sample_size})
                </span>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* Live Fixtures Feed */}
      {loadingLive && liveFixtures.length === 0 ? (
        <div
          className={`rounded-2xl border p-10 text-center ${
            darkMode ? 'bg-slate-900/60 border-slate-800 text-slate-400' : 'bg-white border-slate-200 text-slate-500'
          }`}
        >
          <RefreshCw className="w-7 h-7 animate-spin mx-auto mb-3 text-rose-400" />
          <p className="text-sm font-semibold">Scanning active matches &amp; computing live goal curves...</p>
        </div>
      ) : visibleFixtures.length === 0 ? (
        <div
          className={`rounded-2xl border p-10 text-center ${
            darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200'
          }`}
        >
          <Radio className="w-8 h-8 mx-auto mb-3 text-slate-500" />
          <h3 className={`text-base font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`}>
            {starredOnly
              ? 'No Starred Matches Currently In-Play'
              : signalsOnly
              ? 'No Strong Live Goal Signals Passing Safety Gates Yet'
              : 'No Matches Currently In-Play'}
          </h3>
          <p className={`text-xs mt-1.5 max-w-md mx-auto ${darkMode ? 'text-slate-400' : 'text-slate-600'}`}>
            {starredOnly
              ? 'Star upcoming or live fixtures to monitor them in your dedicated watchlist feed.'
              : signalsOnly
              ? 'Safety gates suppress signals near full-time (<8 mins) or when live momentum & data quality thresholds are unmet.'
              : 'Our live tracker scans ESPN automatically every 30 seconds and promotes kicked-off matches here with live goal probabilities.'}
          </p>
          {(signalsOnly || starredOnly) && (
            <button
              type="button"
              onClick={() => {
                setSignalsOnly(false);
                setStarredOnly(false);
              }}
              className="mt-4 px-4 py-2 rounded-xl text-xs font-bold bg-slate-800 text-slate-200 hover:bg-slate-700"
            >
              Show All In-Play Matches ({liveFixtures.length})
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {visibleFixtures.map((fix) => {
            const signal = getSignalMeta(fix);
            const customHit = passesCustomGate(fix, signal);
            const isStarred = watchlistSet.has(Number(fix.fixture_id));
            const lg = fix.live_goals || {};
            const remTotal = lg.remaining_xg?.total;
            const preTotal = lg.pre_match_xg?.total;
            const pPlus1 = lg.at_least_1_more_goal;
            const pO15 = lg.full_match_over_1_5;
            const pO25 = lg.full_match_over_2_5;
            const pBtts = lg.btts_yes;
            const nextGoal = lg.next_goal || {};

            return (
              <article
                key={fix.fixture_id}
                className={`rounded-2xl border p-4 sm:p-5 transition-all ${cardSurface} ${
                  customHit ? 'ring-1 ring-emerald-500/40' : ''
                }`}
              >
                {/* Top Meta Row */}
                <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-800/60">
                  <div className="flex items-center gap-2 min-w-0">
                    {onToggleWatchlist && (
                      <button
                        type="button"
                        onClick={() => onToggleWatchlist(fix.fixture_id)}
                        title={isStarred ? 'Remove from Watchlist' : 'Add to Watchlist'}
                        className="p-1 rounded-md text-slate-400 hover:text-amber-400 transition-colors shrink-0"
                      >
                        <Star className={`w-4 h-4 ${isStarred ? 'fill-amber-400 text-amber-400' : ''}`} />
                      </button>
                    )}
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-extrabold bg-rose-500/20 text-rose-300 border border-rose-500/40">
                      <span className="w-1.5 h-1.5 rounded-full bg-rose-400 animate-ping" />
                      {fix.live_clock || `${fix.minute || 0}'`}
                      {fix.period ? ` · ${fix.period}` : ''}
                    </span>
                    <span
                      className={`text-xs font-semibold truncate ${
                        darkMode ? 'text-slate-400' : 'text-slate-600'
                      }`}
                    >
                      {fix.league_name || 'International Match'}
                      {fix.league_country ? ` (${fix.league_country})` : ''}
                    </span>
                  </div>

                  {/* Signal Pill */}
                  {signal.isActive ? (
                    <span
                      className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-extrabold border ${
                        signal.strength === 'STRONG'
                          ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                          : signal.strength === 'MODERATE'
                          ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/40'
                          : 'bg-amber-500/15 text-amber-300 border-amber-500/35'
                      }`}
                    >
                      <Flame className="w-3 h-3" />
                      {signal.strength}: {signal.label} ({formatPct(signal.prob)})
                    </span>
                  ) : (
                    <span
                      className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold border ${
                        darkMode
                          ? 'bg-slate-800/80 text-slate-400 border-slate-700'
                          : 'bg-slate-100 text-slate-600 border-slate-200'
                      }`}
                    >
                      <Clock className="w-3 h-3" />
                      {signal.label}
                    </span>
                  )}
                </div>

                {/* Teams & Scoreboard */}
                <div className="my-4 flex items-center justify-between gap-3">
                  <div className="flex-1 min-w-0 flex items-center gap-2.5">
                    {fix.home_team_logo ? (
                      <img
                        src={fix.home_team_logo}
                        alt={fix.home_team_name}
                        className="w-7 h-7 rounded-full object-contain bg-white/5 p-0.5 shrink-0"
                        loading="lazy"
                      />
                    ) : (
                      <div className="w-7 h-7 rounded-full bg-slate-800 flex items-center justify-center text-[10px] font-bold text-slate-300 shrink-0">
                        {(fix.home_team_name || 'H').slice(0, 2).toUpperCase()}
                      </div>
                    )}
                    <span
                      className={`font-bold text-sm sm:text-base truncate ${
                        darkMode ? 'text-white' : 'text-slate-900'
                      }`}
                    >
                      {fix.home_team_name}
                    </span>
                  </div>

                  <div className="px-3.5 py-1.5 rounded-xl bg-rose-500/15 border border-rose-500/35 text-center shrink-0">
                    <div className="text-lg sm:text-xl font-black tracking-tight text-white tabular-nums">
                      {fix.home_score ?? 0} - {fix.away_score ?? 0}
                    </div>
                  </div>

                  <div className="flex-1 min-w-0 flex items-center justify-end gap-2.5">
                    <span
                      className={`font-bold text-sm sm:text-base truncate text-right ${
                        darkMode ? 'text-white' : 'text-slate-900'
                      }`}
                    >
                      {fix.away_team_name}
                    </span>
                    {fix.away_team_logo ? (
                      <img
                        src={fix.away_team_logo}
                        alt={fix.away_team_name}
                        className="w-7 h-7 rounded-full object-contain bg-white/5 p-0.5 shrink-0"
                        loading="lazy"
                      />
                    ) : (
                      <div className="w-7 h-7 rounded-full bg-slate-800 flex items-center justify-center text-[10px] font-bold text-slate-300 shrink-0">
                        {(fix.away_team_name || 'A').slice(0, 2).toUpperCase()}
                      </div>
                    )}
                  </div>
                </div>

                {/* Live Goal Markets Grid */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  <div className={`rounded-xl border p-2.5 ${getMarketPillClass(pPlus1, darkMode)}`}>
                    <div className="text-[10px] font-bold uppercase tracking-wider opacity-80">
                      +1 More Goal
                    </div>
                    <div className="mt-0.5 text-base font-black tabular-nums">
                      {formatPct(pPlus1?.probability)}
                    </div>
                  </div>

                  <div className={`rounded-xl border p-2.5 ${getMarketPillClass(pO15, darkMode)}`}>
                    <div className="text-[10px] font-bold uppercase tracking-wider opacity-80 flex items-center gap-1">
                      Full O1.5
                      {pO15?.status === 'already_resolved' && <CheckCircle2 className="w-3 h-3" />}
                    </div>
                    <div className="mt-0.5 text-base font-black tabular-nums">
                      {pO15?.status === 'already_resolved' ? 'HIT ✓' : formatPct(pO15?.probability)}
                    </div>
                  </div>

                  <div className={`rounded-xl border p-2.5 ${getMarketPillClass(pO25, darkMode)}`}>
                    <div className="text-[10px] font-bold uppercase tracking-wider opacity-80 flex items-center gap-1">
                      Full O2.5
                      {pO25?.status === 'already_resolved' && <CheckCircle2 className="w-3 h-3" />}
                    </div>
                    <div className="mt-0.5 text-base font-black tabular-nums">
                      {pO25?.status === 'already_resolved' ? 'HIT ✓' : formatPct(pO25?.probability)}
                    </div>
                  </div>

                  <div className={`rounded-xl border p-2.5 ${getMarketPillClass(pBtts, darkMode)}`}>
                    <div className="text-[10px] font-bold uppercase tracking-wider opacity-80 flex items-center gap-1">
                      BTTS Yes
                      {pBtts?.status === 'already_resolved' && <CheckCircle2 className="w-3 h-3" />}
                    </div>
                    <div className="mt-0.5 text-base font-black tabular-nums">
                      {pBtts?.status === 'already_resolved' ? 'HIT ✓' : formatPct(pBtts?.probability)}
                    </div>
                  </div>
                </div>

                {/* xG & Next Goal + Footer Actions */}
                <div className="mt-3.5 pt-3 border-t border-slate-800/60 flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-3 text-xs">
                    <span className={darkMode ? 'text-slate-400' : 'text-slate-600'}>
                      Rem xG:{' '}
                      <strong className="text-emerald-400 font-extrabold">{formatXg(remTotal)}</strong>
                      {preTotal !== undefined && preTotal !== null && (
                        <span className="opacity-75"> (Pre: {formatXg(preTotal)})</span>
                      )}
                    </span>
                    {nextGoal && nextGoal.home !== undefined && (
                      <span className={darkMode ? 'text-slate-400' : 'text-slate-600'}>
                        Next Goal:{' '}
                        <strong className={darkMode ? 'text-slate-200' : 'text-slate-800'}>
                          H {formatPct(nextGoal.home)} · A {formatPct(nextGoal.away)}
                        </strong>
                      </span>
                    )}
                    <span className="inline-flex items-center gap-1 text-slate-400">
                      <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
                      Conf {fix.confidence ?? '—'}%
                    </span>
                  </div>

                  <div className="flex items-center gap-2">
                    {onAddToBetSlip && (
                      <button
                        type="button"
                        onClick={() => {
                          const leg = extractQuickSlipLeg({ id: fix.fixture_id }, 'live', fix);
                          if (leg) onAddToBetSlip(leg);
                        }}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-extrabold bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/35 transition-colors"
                        title="Add live market signal to +EV Bet Slip"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        Slip
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => onOpenLiveModal && onOpenLiveModal(fix.fixture_id)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-extrabold bg-rose-500 hover:bg-rose-600 text-white transition-colors"
                    >
                      <Activity className="w-3.5 h-3.5" />
                      Live Intel
                    </button>
                    <button
                      type="button"
                      onClick={() => onOpenDetailModal && onOpenDetailModal(fix.fixture_id)}
                      className={`inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-bold border transition-colors ${
                        darkMode
                          ? 'bg-slate-800 border-slate-700 text-slate-200 hover:border-slate-600'
                          : 'bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200'
                      }`}
                    >
                      Details
                      <ArrowUpRight className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

