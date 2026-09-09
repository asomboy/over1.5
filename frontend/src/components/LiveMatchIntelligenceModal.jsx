import React, { useState, useEffect, useRef } from 'react';
import {
  X,
  Activity,
  Zap,
  Clock,
  Shield,
  Flag,
  Square,
  UserCheck,
  AlertTriangle,
  RefreshCw,
  TrendingUp,
  CheckCircle2,
  Layers,
  ChevronRight,
  Crosshair,
  BarChart3,
  Calendar,
  Sparkles,
  Radio,
  FileText
} from 'lucide-react';

export default function LiveMatchIntelligenceModal({ fixtureId, isOpen, onClose, apiRequest, darkMode }) {
  const [liveData, setLiveData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('overview'); // 'overview' | 'goals' | 'corners' | 'cards' | 'shots' | 'timeline' | 'model'
  const [tickerTime, setTickerTime] = useState(Date.now());
  const activeFixtureIdRef = useRef(fixtureId);
  const requestIdRef = useRef(0);
  const abortControllerRef = useRef(null);

  // Keep ref synchronized
  useEffect(() => {
    activeFixtureIdRef.current = fixtureId;
  }, [fixtureId]);

  // Clock tick every 2 seconds for exact freshness calculation
  useEffect(() => {
    if (!isOpen) return;
    const ticker = setInterval(() => {
      setTickerTime(Date.now());
    }, 2000);
    return () => clearInterval(ticker);
  }, [isOpen]);

  // Fixture change & initial fetch: abort any in-flight request, clear previous state immediately
  useEffect(() => {
    if (isOpen && fixtureId) {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      setLiveData(null);
      setActiveTab('overview');
      fetchLiveMatchData(false, fixtureId);
    } else {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      setLiveData(null);
    }

    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, [isOpen, fixtureId]);

  // Single controlled polling loop every 30 seconds
  useEffect(() => {
    if (!isOpen || !fixtureId) return;

    // Do not poll if match already finished
    if (liveData?.is_completed || liveData?.status === 'FINISHED' || liveData?.live_state?.status === 'FINISHED') {
      return;
    }

    const interval = setInterval(() => {
      if (liveData?.is_completed || liveData?.status === 'FINISHED' || liveData?.live_state?.status === 'FINISHED') return;
      fetchLiveMatchData(true, fixtureId);
    }, 30000);

    return () => clearInterval(interval);
  }, [isOpen, fixtureId, liveData?.is_completed, liveData?.status, liveData?.live_state?.status]);

  const fetchLiveMatchData = async (silent = false, targetId = fixtureId) => {
    if (!targetId) return;
    if (!silent) setLoading(true);

    const thisRequestId = ++requestIdRef.current;
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      // Fetch canonical live match endpoint with abort signal
      const res = await apiRequest('get', `/api/fixtures/${targetId}/live`, null, { signal: controller.signal });
      
      // Strict Cross-Fixture Race Condition Verification:
      // 1. Current open fixture in ref must strictly match targetId
      // 2. Request sequence must be the latest request initiated
      // 3. Response payload fixture.id must strictly equal targetId
      if (
        activeFixtureIdRef.current !== targetId ||
        requestIdRef.current !== thisRequestId ||
        (res.data?.fixture?.id && res.data.fixture.id !== targetId)
      ) {
        return; // Discard late / stale / cross-fixture response
      }

      if (res.data) {
        setLiveData(res.data);
      }
    } catch (err) {
      if (err?.name === 'CanceledError' || err?.name === 'AbortError') {
        return; // Request was aborted due to fixture switch or modal close
      }
      // Fallback to live-intelligence endpoint if canonical live not ready
      try {
        const fbRes = await apiRequest('get', `/api/fixtures/${targetId}/live-intelligence`, null, { signal: controller.signal });
        if (
          activeFixtureIdRef.current !== targetId ||
          requestIdRef.current !== thisRequestId ||
          (fbRes.data?.fixture_id && fbRes.data.fixture_id !== targetId)
        ) {
          return;
        }
        if (fbRes.data) {
          // Normalize to canonical shape
          setLiveData({
            fixture: {
              id: targetId,
              home_team: { name: 'Home' },
              away_team: { name: 'Away' }
            },
            live_state: {
              minute: fbRes.data.match_state?.minute ?? null,
              display_clock: fbRes.data.display_clock || (fbRes.data.match_state?.minute != null ? `${fbRes.data.match_state.minute}'` : '—'),
              period: fbRes.data.match_state?.period || 'PRE',
              status: fbRes.data.status || 'SCHEDULED',
              score: {
                home: fbRes.data.match_state?.home_score ?? null,
                away: fbRes.data.match_state?.away_score ?? null
              }
            },
            statistics: fbRes.data.observed || {},
            events: fbRes.data.events || [],
            narrative: fbRes.data.narrative || [],
            data_quality: fbRes.data.confidence || {},
            predictions: {
              goals: fbRes.data.live_goals,
              corners: fbRes.data.live_corners,
              cards: fbRes.data.live_cards,
              diagnostics: fbRes.data.diagnostics
            },
            signals: fbRes.data.live_signals || [],
            best_signal: fbRes.data.best_live_signal,
            retrieved_at: fbRes.data.retrieved_at,
            status: fbRes.data.status || 'SCHEDULED',
            is_completed: fbRes.data.is_completed || false
          });
        }
      } catch (fbErr) {
        if (fbErr?.name !== 'CanceledError' && fbErr?.name !== 'AbortError') {
          console.error('Error fetching live match data:', fbErr);
        }
      }
    } finally {
      if (!silent && requestIdRef.current === thisRequestId) {
        setLoading(false);
      }
    }
  };

  if (!isOpen) return null;

  // Extracted Canonical Fixture Identity
  const fixture = liveData?.fixture || {};
  const homeTeam = fixture.home_team || { name: 'Home Team' };
  const awayTeam = fixture.away_team || { name: 'Away Team' };
  const compName = fixture.competition || 'League Match';
  const country = fixture.country;

  const liveState = liveData?.live_state || {};
  const stats = liveData?.statistics || {};
  const events = liveData?.events || [];
  const narrative = liveData?.narrative || [];
  const predictions = liveData?.predictions || {};
  const goals = predictions.goals || {};
  const corners = predictions.corners || {};
  const cards = predictions.cards || {};
  const diag = predictions.diagnostics || {};
  const signals = liveData?.signals || [];
  const bestSignal = liveData?.best_signal || { label: 'NO_SIGNAL' };
  const dataQuality = liveData?.data_quality || {};

  // Freshness Calculation
  const retrievedAtMs = liveData?.retrieved_at ? new Date(liveData.retrieved_at).getTime() : 0;
  const ageSec = retrievedAtMs > 0 ? Math.max(0, Math.round((tickerTime - retrievedAtMs) / 1000)) : 999;
  
  let freshnessState = 'FRESH';
  let freshnessColor = 'text-emerald-400 bg-emerald-500/15 border-emerald-500/40';
  let freshnessLabel = `LIVE / FRESH (${ageSec}s ago)`;

  if (!liveData?.retrieved_at || liveData?.data_status === 'UNAVAILABLE') {
    freshnessState = 'UNAVAILABLE';
    freshnessColor = 'text-slate-400 bg-slate-800/40 border-slate-700';
    freshnessLabel = 'LIVE DATA UNAVAILABLE';
  } else if (ageSec > 180 || liveData?.data_status === 'VERY_STALE') {
    freshnessState = 'VERY_STALE';
    freshnessColor = 'text-rose-400 bg-rose-500/15 border-rose-500/40';
    freshnessLabel = `FEED STALE (Last: ${Math.round(ageSec / 60)}m ago)`;
  } else if (ageSec > 60 || liveData?.data_status === 'STALE') {
    freshnessState = 'STALE';
    freshnessColor = 'text-amber-400 bg-amber-500/15 border-amber-500/40';
    freshnessLabel = `FEED DELAYED (${ageSec}s ago)`;
  }

  const isCompleted = liveData?.is_completed || liveState.status === 'FINISHED' || liveState.period === 'FT';

  const getSignalBadgeColor = (label) => {
    switch (label) {
      case 'STRONG': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40';
      case 'MODERATE': return 'bg-amber-500/20 text-amber-400 border-amber-500/40';
      case 'WATCH': return 'bg-cyan-500/20 text-cyan-400 border-cyan-500/40';
      default: return 'bg-slate-500/20 text-slate-400 border-slate-500/40';
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-slate-950/85 backdrop-blur-md animate-fadeIn">
      <div className={`w-full max-w-3xl rounded-3xl border shadow-2xl overflow-hidden flex flex-col max-h-[94vh] transition-colors ${
        darkMode ? 'bg-slate-900 border-slate-800 text-white' : 'bg-white border-slate-200 text-slate-900'
      }`}>
        
        {/* ========================================================================= */}
        {/* LIVE HEADER: FIXTURE IDENTITY & REAL MATCH CLOCK */}
        {/* ========================================================================= */}
        <div className="p-4 sm:p-5 border-b border-slate-800 bg-gradient-to-r from-slate-950 via-slate-900 to-rose-950/50">
          <div className="flex items-start justify-between gap-3">
            
            {/* Competition & Live Clock Badges */}
            <div className="space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 px-2 py-0.5 rounded-md bg-slate-800/80 border border-slate-700/60">
                  {country ? `${country} • ` : ''}{compName}
                </span>

                {isCompleted ? (
                  <span className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-wider text-slate-300 px-2.5 py-0.5 rounded-full bg-slate-800 border border-slate-700">
                    <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                    FULL TIME (FT)
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-wider text-rose-400 px-2.5 py-0.5 rounded-full bg-rose-500/15 border border-rose-500/40">
                    <span className="w-2 h-2 rounded-full bg-rose-500 animate-ping inline-block" />
                    LIVE {liveState.display_clock || (liveState.minute != null ? `${liveState.minute}'` : 'PRE')}
                  </span>
                )}

                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${freshnessColor}`}>
                  {freshnessLabel}
                </span>
              </div>

              {/* Match Teams & Real Scoreline */}
              <div className="flex items-center gap-3 pt-1">
                <div className="flex items-center gap-2">
                  {homeTeam.logo_url && (
                    <img src={homeTeam.logo_url} alt="" className="w-6 h-6 object-contain rounded-full bg-slate-800 p-0.5" />
                  )}
                  <span className="text-base sm:text-lg font-black text-white">{homeTeam.name}</span>
                </div>

                <div className="px-3 py-1 rounded-xl bg-slate-950/80 border border-slate-800 text-lg sm:text-2xl font-black text-emerald-400 tracking-tight font-mono">
                  {liveState.score?.home != null ? liveState.score.home : '—'} — {liveState.score?.away != null ? liveState.score.away : '—'}
                </div>

                <div className="flex items-center gap-2">
                  <span className="text-base sm:text-lg font-black text-white">{awayTeam.name}</span>
                  {awayTeam.logo_url && (
                    <img src={awayTeam.logo_url} alt="" className="w-6 h-6 object-contain rounded-full bg-slate-800 p-0.5" />
                  )}
                </div>
              </div>
            </div>

            {/* Actions: Refresh & Close */}
            <div className="flex items-center gap-2">
              <button
                onClick={() => fetchLiveMatchData(false, fixtureId)}
                className="p-2 rounded-2xl bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white transition-colors"
                title="Refresh live provider feed"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-emerald-400' : ''}`} />
              </button>
              <button
                onClick={onClose}
                className="p-2 rounded-2xl bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>

        {/* ========================================================================= */}
        {/* OBSERVED VS MODEL-DERIVED LEGEND & FRESHNESS BAR */}
        {/* ========================================================================= */}
        <div className="px-4 py-2 bg-slate-950/80 border-b border-slate-800 flex items-center justify-between text-xs flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 rounded text-[9px] font-black uppercase tracking-wider bg-cyan-500/15 text-cyan-400 border border-cyan-500/30 flex items-center gap-1">
              <CheckCircle2 className="w-2.5 h-2.5" />
              OBSERVED FACT
            </span>
            <span className="px-2 py-0.5 rounded text-[9px] font-black uppercase tracking-wider bg-purple-500/15 text-purple-300 border border-purple-500/30 flex items-center gap-1">
              <Sparkles className="w-2.5 h-2.5" />
              MODEL-DERIVED
            </span>
          </div>

          <div className="flex items-center gap-3 text-[10px] text-slate-400 font-medium">
            <span>Coverage: <strong className="text-white">{dataQuality.coverage || 'PARTIAL'}</strong></span>
            <span>•</span>
            <span>Data Quality: <strong className="text-white">{dataQuality.score || dataQuality.live_data_quality || 50}%</strong></span>
            <span>•</span>
            <span>Prior/Live: <strong className="text-cyan-400">{Math.round((diag.prior_weight || 0.7) * 100)}% / {Math.round((diag.live_weight || 0.3) * 100)}%</strong></span>
          </div>
        </div>

        {/* ========================================================================= */}
        {/* NAVIGATION TABS */}
        {/* ========================================================================= */}
        <div className="p-2 border-b border-slate-800 bg-slate-950/90 flex items-center gap-1.5 overflow-x-auto custom-scrollbar">
          {[
            { id: 'overview', label: 'LIVE OVERVIEW' },
            { id: 'goals', label: 'LIVE GOALS' },
            { id: 'corners', label: 'LIVE CORNERS' },
            { id: 'cards', label: 'LIVE CARDS' },
            { id: 'shots', label: 'SHOTS & SOT' },
            { id: 'timeline', label: `EVENTS (${events.length})` },
            { id: 'model', label: 'MODEL & DIAGNOSTICS' }
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-1.5 rounded-xl text-xs font-black transition-all whitespace-nowrap ${
                activeTab === tab.id
                  ? 'bg-rose-600 text-white shadow-lg shadow-rose-600/25 scale-[1.02]'
                  : 'bg-slate-800/40 text-slate-400 hover:text-white hover:bg-slate-800'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* ========================================================================= */}
        {/* MODAL BODY */}
        {/* ========================================================================= */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-4">
          {loading && !liveData ? (
            <div className="py-20 text-center space-y-3">
              <RefreshCw className="w-8 h-8 text-rose-400 animate-spin mx-auto" />
              <p className="text-xs font-semibold text-slate-400">Streaming verified live match data from provider feed...</p>
            </div>
          ) : (
            <>
              {/* TAB 1: LIVE OVERVIEW */}
              {activeTab === 'overview' && (
                <div className="space-y-4 animate-fadeIn">
                  
                  {/* FACTUAL LIVE NARRATIVE FEED */}
                  {narrative.length > 0 && (
                    <div className="p-3.5 rounded-2xl bg-slate-950/70 border border-slate-800 space-y-2">
                      <div className="flex items-center justify-between pb-1 border-b border-slate-800/60">
                        <div className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-wider text-cyan-400">
                          <Radio className="w-3.5 h-3.5 animate-pulse" />
                          <span>Verified In-Play Narrative</span>
                        </div>
                        <span className="text-[9px] text-slate-500 font-bold">FACTUAL UPDATES</span>
                      </div>
                      <div className="space-y-1.5">
                        {narrative.slice(0, 4).map((item, idx) => (
                          <div key={item.id || idx} className="flex items-start gap-2 text-xs">
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-slate-800 text-slate-300">
                              {item.minute ? `${item.minute}'` : 'LIVE'}
                            </span>
                            <span className="text-slate-200 font-medium">{item.statement}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* OBSERVED IN-PLAY BOXSCORE COMPARISON */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800/80 pb-2">
                      <span className="text-[10px] font-black uppercase tracking-wider text-cyan-400 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        OBSERVED MATCH STATISTICS
                      </span>
                      <span className="text-[10px] text-slate-500 font-bold">
                        {homeTeam.name} vs {awayTeam.name}
                      </span>
                    </div>

                    <div className="space-y-2.5 text-xs">
                      {[
                        { label: 'Total Shots', h: stats.shots?.home, a: stats.shots?.away },
                        { label: 'Shots on Target', h: stats.shots_on_target?.home, a: stats.shots_on_target?.away },
                        { label: 'Corner Kicks', h: stats.corners?.home, a: stats.corners?.away },
                        { label: 'Possession %', h: stats.possession?.home != null ? `${Math.round(stats.possession.home)}%` : null, a: stats.possession?.away != null ? `${Math.round(stats.possession.away)}%` : null, rawH: stats.possession?.home, rawA: stats.possession?.away },
                        { label: 'Fouls Committed', h: stats.fouls?.home, a: stats.fouls?.away },
                        { label: 'Goalkeeper Saves', h: stats.saves?.home, a: stats.saves?.away },
                        { label: 'Yellow Cards', h: stats.cards?.home_yellow, a: stats.cards?.away_yellow },
                        { label: 'Red Cards', h: stats.cards?.home_red, a: stats.cards?.away_red }
                      ].map((item, idx) => {
                        const hVal = item.h != null ? item.h : '—';
                        const aVal = item.a != null ? item.a : '—';
                        const isPoss = item.label === 'Possession %' && item.rawH != null;
                        const hasData = (item.h != null && item.a != null) || item.rawH != null;
                        const sumVal = (Number(item.h) || 0) + (Number(item.a) || 0);
                        const hPercent = isPoss
                          ? item.rawH
                          : (hasData && sumVal > 0 ? (Number(item.h) / sumVal) * 100 : 50);

                        return (
                          <div key={idx} className="space-y-1">
                            <div className="flex items-center justify-between text-xs">
                              <span className="font-bold text-white font-mono">{hVal}</span>
                              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wide">{item.label}</span>
                              <span className="font-bold text-white font-mono">{aVal}</span>
                            </div>
                            <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden flex">
                              {hasData ? (
                                <>
                                  <div className="bg-cyan-500 h-full transition-all duration-500" style={{ width: `${hPercent}%` }} />
                                  <div className="bg-rose-500 h-full transition-all duration-500" style={{ width: `${100 - hPercent}%` }} />
                                </>
                              ) : (
                                <div className="bg-slate-700/40 h-full w-full" title="Statistic not reported by provider" />
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* BEST LIVE SIGNAL HERO CARD (MODEL-DERIVED) */}
                  <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-r from-slate-950 via-slate-900 to-rose-950/40 border border-rose-500/40 space-y-3 shadow-xl">
                    <div className="flex items-center justify-between border-b border-slate-800/80 pb-2.5">
                      <div className="flex items-center gap-2">
                        <Zap className="w-4 h-4 text-amber-400" />
                        <span className="text-xs font-black uppercase tracking-wider text-white">Live Opportunity (Model-Derived)</span>
                      </div>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase border ${getSignalBadgeColor(bestSignal.label)}`}>
                        {bestSignal.label} SIGNAL
                      </span>
                    </div>

                    {bestSignal.market ? (
                      <div className="space-y-3 pt-1">
                        <div className="flex items-baseline justify-between">
                          <div>
                            <h3 className="text-lg sm:text-xl font-black text-white">{bestSignal.market}</h3>
                            <span className="text-[11px] text-slate-400 block font-medium mt-0.5">
                              {bestSignal.rationale}
                            </span>
                          </div>
                          <div className="text-right">
                            <span className="text-2xl font-black text-emerald-400 block">{Math.round(bestSignal.probability * 100)}%</span>
                            <span className="text-[10px] text-slate-400 font-bold block">Live Model Conviction</span>
                          </div>
                        </div>

                        <div className="grid grid-cols-3 gap-2 pt-1 text-center">
                          <div className="p-2 rounded-xl bg-slate-900 border border-slate-800">
                            <span className="text-[10px] text-slate-400 font-bold block">Signal Score</span>
                            <span className="text-sm font-black text-white">{bestSignal.signal_score}/100</span>
                          </div>
                          <div className="p-2 rounded-xl bg-slate-900 border border-slate-800">
                            <span className="text-[10px] text-slate-400 font-bold block">Time Window</span>
                            <span className="text-sm font-black text-cyan-400">~{Math.round(bestSignal.time_remaining_minutes || 0)} mins</span>
                          </div>
                          <div className="p-2 rounded-xl bg-slate-900 border border-slate-800">
                            <span className="text-[10px] text-slate-400 font-bold block">Market Cat</span>
                            <span className="text-sm font-black text-amber-400 uppercase">{bestSignal.category || 'GOALS'}</span>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="py-4 text-center space-y-1">
                        <p className="text-xs font-bold text-slate-300">No high-conviction live opportunity at this minute.</p>
                        <p className="text-[11px] text-slate-500">
                          {bestSignal.rationale || "The model recommends monitoring rather than forcing a low-edge prediction."}
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 2: LIVE GOALS */}
              {activeTab === 'goals' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* OBSERVED GOAL STATE */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-[10px] font-black uppercase tracking-wider text-cyan-400 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        [OBSERVED FACT] SCORELINE & EVENTS
                      </span>
                      <span className="text-xs font-black text-emerald-400 font-mono">
                        {homeTeam.name} {liveState.score?.home != null ? liveState.score.home : '—'} — {liveState.score?.away != null ? liveState.score.away : '—'} {awayTeam.name}
                      </span>
                    </div>

                    {/* Goal Events Timeline */}
                    <div className="space-y-1.5">
                      {events.filter(e => e.type === 'GOAL').length > 0 ? (
                        events.filter(e => e.type === 'GOAL').map((g, idx) => (
                          <div key={idx} className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-between text-xs">
                            <div className="flex items-center gap-2">
                              <span className="text-base">⚽</span>
                              <span className="font-bold text-white">{g.player || 'Goal'}</span>
                              <span className="text-slate-400 text-[10px]">({g.team})</span>
                              {g.assist_or_sub && <span className="text-slate-500 text-[9px]">Assist: {g.assist_or_sub}</span>}
                            </div>
                            <span className="font-mono font-bold text-emerald-400">{g.display_clock}</span>
                          </div>
                        ))
                      ) : (
                        <p className="text-xs text-slate-500 py-1 text-center">No goals recorded in this match yet.</p>
                      )}
                    </div>
                  </div>

                  {/* MODEL-DERIVED REMAINING XG */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-[10px] font-black uppercase tracking-wider text-purple-300 flex items-center gap-1.5">
                        <Sparkles className="w-3.5 h-3.5" />
                        [MODEL-DERIVED] EXPECTED GOALS (REMAINING)
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">{homeTeam.name} Rem</span>
                        <span className="text-lg font-black text-white">{goals.remaining_xg?.home?.toFixed(2) ?? '0.00'}</span>
                        <span className="text-[9px] text-slate-500 block">Pre: {goals.pre_match_xg?.home?.toFixed(2) ?? '0.00'}</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-rose-950/30 border border-rose-500/30">
                        <span className="text-[10px] text-rose-400 block font-bold">Total Remaining</span>
                        <span className="text-xl font-black text-rose-400">{goals.remaining_xg?.total?.toFixed(2) ?? '0.00'}</span>
                        <span className="text-[9px] text-rose-500/80 block">Pre: {goals.pre_match_xg?.total?.toFixed(2) ?? '0.00'}</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">{awayTeam.name} Rem</span>
                        <span className="text-lg font-black text-white">{goals.remaining_xg?.away?.toFixed(2) ?? '0.00'}</span>
                        <span className="text-[9px] text-slate-500 block">Pre: {goals.pre_match_xg?.away?.toFixed(2) ?? '0.00'}</span>
                      </div>
                    </div>
                  </div>

                  {/* DYNAMIC GOAL PROBABILITIES */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">[MODEL-DERIVED] Match Goal Lines</span>
                    <div className="space-y-2">
                      {[
                        { label: 'At Least 1 More Goal', obj: goals.at_least_1_more_goal },
                        { label: 'At Least 2 More Goals', obj: goals.at_least_2_more_goals },
                        { label: 'Full Match Over 1.5 Goals', obj: goals.full_match_over_1_5 },
                        { label: 'Full Match Over 2.5 Goals', obj: goals.full_match_over_2_5 },
                        { label: 'Full Match Over 3.5 Goals', obj: goals.full_match_over_3_5 },
                        { label: 'Both Teams To Score (BTTS)', obj: goals.btts_yes }
                      ].map((m, idx) => (
                        <div key={idx} className="p-3 rounded-xl bg-slate-900 border border-slate-800/80 flex items-center justify-between text-xs">
                          <span className="font-bold text-slate-200">{m.label}</span>
                          {m.obj?.status === 'already_resolved' ? (
                            <span className="flex items-center gap-1 text-[11px] font-black text-emerald-400 px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              {m.obj.resolved_result ? 'Achieved' : 'Resolved'}
                            </span>
                          ) : (
                            <span className="font-black text-emerald-400 text-sm">
                              {Math.round((m.obj?.probability || 0) * 100)}%
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 3: LIVE CORNERS */}
              {activeTab === 'corners' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* OBSERVED CORNERS STATE */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-[10px] font-black uppercase tracking-wider text-cyan-400 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        [OBSERVED FACT] CORNER KICKS
                      </span>
                      <span className="text-xs font-black text-emerald-400">
                        Total Observed: {stats.corners?.home != null && stats.corners?.away != null ? stats.corners.home + stats.corners.away : '—'}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-center text-xs">
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Observed Corners (H / A)</span>
                        <span className="text-base font-black text-white font-mono">{stats.corners?.home ?? '—'} — {stats.corners?.away ?? '—'}</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-purple-950/30 border border-purple-500/30">
                        <span className="text-[10px] text-purple-300 block font-bold">[MODEL-DERIVED] Expected Rem</span>
                        <span className="text-base font-black text-purple-300">+{corners.remaining_expected_corners?.total?.toFixed(1) ?? '0.0'}</span>
                      </div>
                    </div>
                  </div>

                  {/* MODEL CORNER PROBABILITIES */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">[MODEL-DERIVED] Corner Lines</span>
                    {[
                      { label: 'Over 7.5 Corners', obj: corners.over_7_5 },
                      { label: 'Over 8.5 Corners', obj: corners.over_8_5 },
                      { label: 'Over 9.5 Corners', obj: corners.over_9_5 },
                      { label: 'Over 10.5 Corners', obj: corners.over_10_5 },
                      { label: 'Over 11.5 Corners', obj: corners.over_11_5 }
                    ].map((m, idx) => (
                      <div key={idx} className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 flex items-center justify-between text-xs">
                        <span className="font-bold text-slate-200">{m.label}</span>
                        {m.obj?.status === 'already_resolved' ? (
                          <span className="flex items-center gap-1 text-[11px] font-black text-emerald-400 px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30">
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            Achieved
                          </span>
                        ) : (
                          <span className="font-black text-emerald-400 text-sm">
                            {Math.round((m.obj?.probability || 0) * 100)}%
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* TAB 4: LIVE CARDS */}
              {activeTab === 'cards' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* OBSERVED CARDS STATE */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-[10px] font-black uppercase tracking-wider text-cyan-400 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        [OBSERVED FACT] DISCIPLINARY ACTIONS
                      </span>
                      <span className="text-xs font-black text-amber-400">
                        Yellows: {stats.cards?.home_yellow != null && stats.cards?.away_yellow != null ? stats.cards.home_yellow + stats.cards.away_yellow : '—'} | Reds: {stats.cards?.home_red != null && stats.cards?.away_red != null ? stats.cards.home_red + stats.cards.away_red : '—'}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-center text-xs">
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Observed Yellows (H/A)</span>
                        <span className="text-base font-black text-white">{stats.cards?.home_yellow ?? '—'} — {stats.cards?.away_yellow ?? '—'}</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-rose-950/30 border border-rose-500/30">
                        <span className="text-[10px] text-rose-400 block font-bold">Observed Reds (H/A)</span>
                        <span className="text-base font-black text-rose-400">{stats.cards?.home_red ?? '—'} — {stats.cards?.away_red ?? '—'}</span>
                      </div>
                    </div>

                    {/* Card Events */}
                    <div className="space-y-1.5 pt-1">
                      {events.filter(e => e.type === 'YELLOW_CARD' || e.type === 'RED_CARD').length > 0 ? (
                        events.filter(e => e.type === 'YELLOW_CARD' || e.type === 'RED_CARD').map((c, idx) => (
                          <div key={idx} className="p-2 rounded-xl bg-slate-900/80 border border-slate-800 flex items-center justify-between text-xs">
                            <div className="flex items-center gap-2">
                              <span>{c.type === 'RED_CARD' ? '🟥' : '🟨'}</span>
                              <span className="font-bold text-white">{c.player || 'Player'}</span>
                              <span className="text-slate-400 text-[10px]">({c.team})</span>
                            </div>
                            <span className="font-mono font-bold text-slate-400">{c.display_clock}</span>
                          </div>
                        ))
                      ) : (
                        <p className="text-xs text-slate-500 py-1 text-center">No cards recorded in this match yet.</p>
                      )}
                    </div>
                  </div>

                  {/* MODEL-DERIVED CARDS PROJECTIONS */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">[MODEL-DERIVED] Card Markets</span>
                    {[
                      { label: 'At Least 1 More Card', obj: cards.at_least_1_more_card },
                      { label: 'Over Current + 1.5 Cards', obj: cards.over_current_plus_1_5 },
                      { label: 'Any Red Card in Match', obj: cards.any_red_card }
                    ].map((m, idx) => (
                      <div key={idx} className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 flex items-center justify-between text-xs">
                        <span className="font-bold text-slate-200">{m.label}</span>
                        <span className="font-black text-amber-400 text-sm">
                          {Math.round((m.obj?.probability || 0) * 100)}%
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* TAB 5: SHOTS & SOT */}
              {activeTab === 'shots' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* OBSERVED SHOTS CARD */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-[10px] font-black uppercase tracking-wider text-cyan-400 flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        [OBSERVED FACT] SHOT TOTALS
                      </span>
                      <span className="text-xs font-black text-emerald-400">
                        Total Shots: {stats.shots?.home != null && stats.shots?.away != null ? stats.shots.home + stats.shots.away : '—'}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-center text-xs">
                      <div className="p-3 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Total Shots (H / A)</span>
                        <span className="text-xl font-black text-white font-mono">{stats.shots?.home ?? '—'} — {stats.shots?.away ?? '—'}</span>
                      </div>
                      <div className="p-3 rounded-xl bg-cyan-950/30 border border-cyan-500/30">
                        <span className="text-[10px] text-cyan-300 block font-bold">Shots on Target (H / A)</span>
                        <span className="text-xl font-black text-cyan-400 font-mono">{stats.shots_on_target?.home ?? '—'} — {stats.shots_on_target?.away ?? '—'}</span>
                      </div>
                    </div>

                    {/* Shooting Accuracy breakdown */}
                    {stats.shots?.home && stats.shots_on_target?.home && (
                      <div className="p-2.5 rounded-xl bg-slate-900/60 border border-slate-800 text-xs flex justify-between text-slate-400">
                        <span>{homeTeam.name} Accuracy: <strong className="text-white">{Math.round((stats.shots_on_target.home / stats.shots.home) * 100)}%</strong></span>
                        {stats.shots?.away && stats.shots_on_target?.away && (
                          <span>{awayTeam.name} Accuracy: <strong className="text-white">{Math.round((stats.shots_on_target.away / stats.shots.away) * 100)}%</strong></span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 6: EVENT TIMELINE */}
              {activeTab === 'timeline' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-[10px] font-black uppercase tracking-wider text-cyan-400 flex items-center gap-1.5 border-b border-slate-800 pb-2">
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      CHRONOLOGICAL EVENT TIMELINE ({events.length})
                    </span>

                    {events.length > 0 ? (
                      <div className="space-y-2">
                        {events.map((ev, idx) => (
                          <div key={ev.id || idx} className="p-3 rounded-xl bg-slate-900/90 border border-slate-800 flex items-center justify-between text-xs">
                            <div className="flex items-center gap-3">
                              <span className="px-2 py-0.5 rounded font-mono font-bold text-[10px] bg-slate-800 text-cyan-400 border border-slate-700">
                                {ev.display_clock || `${ev.minute}'`}
                              </span>
                              <div>
                                <div className="flex items-center gap-2">
                                  <span className="text-sm">
                                    {ev.type === 'GOAL' ? '⚽' : (ev.type === 'RED_CARD' ? '🟥' : (ev.type === 'YELLOW_CARD' ? '🟨' : '🔄'))}
                                  </span>
                                  <span className="font-bold text-white">{ev.player || ev.type}</span>
                                  <span className="text-[10px] text-slate-400">({ev.team})</span>
                                </div>
                                {ev.assist_or_sub && (
                                  <span className="text-[10px] text-slate-500 block pl-6">
                                    {ev.type === 'SUBSTITUTION' ? `Replaced: ${ev.assist_or_sub}` : `Assist: ${ev.assist_or_sub}`}
                                  </span>
                                )}
                              </div>
                            </div>
                            <span className="text-[10px] font-bold text-slate-500 uppercase">{ev.type}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-6 text-center">No match events (goals, cards, substitutions) recorded yet.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 7: MODEL DIAGNOSTICS */}
              {activeTab === 'model' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Prior vs Live Bayesian Fusion</span>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-slate-300">Pre-Match Weight ({Math.round((diag.prior_weight || 0.7) * 100)}%)</span>
                        <span className="text-slate-300">Live Weight ({Math.round((diag.live_weight || 0.3) * 100)}%)</span>
                      </div>
                      <div className="w-full h-2.5 rounded-full bg-slate-800 overflow-hidden flex">
                        <div className="bg-cyan-500 h-full transition-all" style={{ width: `${(diag.prior_weight || 0.7) * 100}%` }} />
                        <div className="bg-rose-500 h-full transition-all" style={{ width: `${(diag.live_weight || 0.3) * 100}%` }} />
                      </div>
                    </div>
                  </div>

                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Live Dynamic Multipliers</span>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Score State Effect</span>
                        <span className="text-xs font-black text-white">
                          H: {diag.score_state_adjustment?.home_multiplier ?? 1.0}x | A: {diag.score_state_adjustment?.away_multiplier ?? 1.0}x
                        </span>
                        <span className="text-[9px] text-slate-500 capitalize">{diag.score_state_adjustment?.state_label?.replace('_', ' ') || 'level'}</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Momentum Pressure</span>
                        <span className="text-xs font-black text-white">
                          H: {diag.momentum_adjustment?.home_pressure_multiplier ?? 1.0}x | A: {diag.momentum_adjustment?.away_pressure_multiplier ?? 1.0}x
                        </span>
                        <span className="text-[9px] text-slate-500">{diag.momentum_adjustment?.momentum_available ? 'Live Boxscore' : 'Neutral Prior'}</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
