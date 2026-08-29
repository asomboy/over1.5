import React, { useState, useEffect } from 'react';
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
  ChevronRight
} from 'lucide-react';

export default function LiveMatchIntelligenceModal({ fixtureId, isOpen, onClose, apiRequest, darkMode }) {
  const [liveData, setLiveData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('overview'); // 'overview' | 'goals' | 'corners' | 'cards' | 'model'
  const [lastRefreshed, setLastRefreshed] = useState(new Date());

  useEffect(() => {
    if (isOpen && fixtureId) {
      fetchLiveIntelligence();
      setActiveTab('overview');
    }
  }, [isOpen, fixtureId]);

  // Periodic live refresh every 15 seconds when modal is open
  useEffect(() => {
    if (!isOpen || !fixtureId) return;
    const interval = setInterval(() => {
      fetchLiveIntelligence(true);
    }, 15000);
    return () => clearInterval(interval);
  }, [isOpen, fixtureId]);

  const fetchLiveIntelligence = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await apiRequest('get', `/api/fixtures/${fixtureId}/live-intelligence`);
      if (res.data) {
        setLiveData(res.data);
        setLastRefreshed(new Date());
      }
    } catch (err) {
      console.error('Error fetching live intelligence:', err);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  if (!isOpen) return null;

  const st = liveData?.match_state || {};
  const goals = liveData?.live_goals || {};
  const corners = liveData?.live_corners || {};
  const cards = liveData?.live_cards || {};
  const signals = liveData?.live_signals || [];
  const bestSignal = liveData?.best_live_signal || { label: 'NO_SIGNAL' };
  const conf = liveData?.confidence || { overall_confidence: 50, live_data_quality: 50, label: 'moderate' };
  const diag = liveData?.diagnostics || {};

  const getSignalBadgeColor = (label) => {
    switch (label) {
      case 'STRONG': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40';
      case 'MODERATE': return 'bg-amber-500/20 text-amber-400 border-amber-500/40';
      case 'WATCH': return 'bg-cyan-500/20 text-cyan-400 border-cyan-500/40';
      default: return 'bg-slate-500/20 text-slate-400 border-slate-500/40';
    }
  };

  const getConfidenceBadgeColor = (label) => {
    switch (label) {
      case 'strong': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40';
      case 'good': return 'bg-cyan-500/20 text-cyan-400 border-cyan-500/40';
      case 'moderate': return 'bg-amber-500/20 text-amber-400 border-amber-500/40';
      default: return 'bg-rose-500/20 text-rose-400 border-rose-500/40';
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-slate-950/85 backdrop-blur-md animate-fadeIn">
      <div className={`w-full max-w-2xl rounded-3xl border shadow-2xl overflow-hidden flex flex-col max-h-[92vh] transition-colors ${
        darkMode ? 'bg-slate-900 border-slate-800 text-white' : 'bg-white border-slate-200 text-slate-900'
      }`}>
        
        {/* LIVE HEADER */}
        <div className="p-4 sm:p-5 border-b border-slate-800 bg-gradient-to-r from-rose-950/70 via-slate-900 to-amber-950/70 flex items-center justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1.5 text-[11px] font-black uppercase tracking-wider text-rose-400 px-2.5 py-0.5 rounded-full bg-rose-500/15 border border-rose-500/40">
                <span className="w-2 h-2 rounded-full bg-rose-500 animate-ping inline-block" />
                LIVE {st.minute ? `${st.minute}'` : 'IN-PLAY'}
              </span>
              <span className="text-[10px] font-bold text-slate-400 uppercase">
                {st.period || '1H'}
              </span>
              <span className="text-[10px] text-slate-400 font-medium">
                • Updated {Math.max(0, Math.round((new Date() - lastRefreshed) / 1000))}s ago
              </span>
            </div>
            
            {/* Live Scoreline Display */}
            <div className="flex items-center gap-3 pt-0.5">
              <span className="text-xl sm:text-2xl font-black text-white tracking-tight">
                {st.home_score ?? 0} — {st.away_score ?? 0}
              </span>
              <div className="flex items-center gap-1.5 text-xs text-slate-300 font-bold">
                <span>Rem: ~{diag.effective_remaining_minutes ? Math.round(diag.effective_remaining_minutes) : 45}m</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => fetchLiveIntelligence()}
              className="p-2 rounded-2xl bg-slate-800/60 hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
              title="Refresh live calculation"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-emerald-400' : ''}`} />
            </button>
            <button
              onClick={onClose}
              className="p-2 rounded-2xl bg-slate-800/60 hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Live Confidence & Data Quality Bar */}
        <div className="px-4 py-2 bg-slate-950/60 border-b border-slate-800/80 flex items-center justify-between text-xs">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-slate-400 uppercase">Live Confidence:</span>
            <span className={`px-2 py-0.5 rounded-lg text-[10px] font-black uppercase border ${getConfidenceBadgeColor(conf.label)}`}>
              {conf.label} ({conf.overall_confidence}%)
            </span>
          </div>
          <div className="flex items-center gap-2 text-[10px] text-slate-400">
            <span>Live Data Quality: <strong className="text-white">{conf.live_data_quality}%</strong></span>
            <span>•</span>
            <span>Prior/Live: <strong className="text-cyan-400">{Math.round((diag.prior_weight || 0.7) * 100)}% / {Math.round((diag.live_weight || 0.3) * 100)}%</strong></span>
          </div>
        </div>

        {/* NAVIGATION TABS */}
        <div className="p-2 border-b border-slate-800 bg-slate-950/70 flex items-center gap-1.5 overflow-x-auto custom-scrollbar">
          {[
            { id: 'overview', label: 'LIVE OVERVIEW' },
            { id: 'goals', label: 'LIVE GOALS' },
            { id: 'corners', label: 'LIVE CORNERS' },
            { id: 'cards', label: 'LIVE CARDS' },
            { id: 'model', label: 'MODEL & DIAGNOSTICS' }
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-1.5 rounded-xl text-xs font-black transition-all whitespace-nowrap ${
                activeTab === tab.id
                  ? 'bg-rose-600 text-white shadow-lg shadow-rose-600/20 scale-[1.02]'
                  : 'bg-slate-800/40 text-slate-400 hover:text-white hover:bg-slate-800'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* MODAL BODY */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-4">
          {loading && !liveData ? (
            <div className="py-16 text-center space-y-3">
              <RefreshCw className="w-8 h-8 text-rose-400 animate-spin mx-auto" />
              <p className="text-xs font-semibold text-slate-400">Synthesizing live dynamic probabilities...</p>
            </div>
          ) : (
            <>
              {/* TAB 1: LIVE OVERVIEW */}
              {activeTab === 'overview' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* BEST LIVE SIGNAL HERO CARD */}
                  <div className="p-4 sm:p-5 rounded-2xl bg-gradient-to-r from-slate-950 via-slate-900 to-rose-950/40 border border-rose-500/40 space-y-3 shadow-xl">
                    <div className="flex items-center justify-between border-b border-slate-800/80 pb-2.5">
                      <div className="flex items-center gap-2">
                        <Zap className="w-4 h-4 text-amber-400" />
                        <span className="text-xs font-black uppercase tracking-wider text-white">Best Live Opportunity</span>
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
                            <span className="text-[10px] text-slate-400 font-bold block">Model Conviction</span>
                          </div>
                        </div>

                        <div className="grid grid-cols-3 gap-2 pt-1 text-center">
                          <div className="p-2 rounded-xl bg-slate-900 border border-slate-800">
                            <span className="text-[10px] text-slate-400 font-bold block">Signal Score</span>
                            <span className="text-sm font-black text-white">{bestSignal.signal_score}/100</span>
                          </div>
                          <div className="p-2 rounded-xl bg-slate-900 border border-slate-800">
                            <span className="text-[10px] text-slate-400 font-bold block">Time Window</span>
                            <span className="text-sm font-black text-cyan-400">~{Math.round(bestSignal.time_remaining_minutes)} mins</span>
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

                  {/* ACTIVE LIVE OPPORTUNITIES LIST */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">
                      Active Live Opportunities ({signals.length})
                    </span>

                    {signals.length > 0 ? (
                      <div className="space-y-2">
                        {signals.map((sig, idx) => (
                          <div key={idx} className="p-3 rounded-xl bg-slate-900/90 border border-slate-800 flex items-center justify-between">
                            <div className="space-y-0.5">
                              <div className="flex items-center gap-2">
                                <span className="text-xs font-black text-white">{sig.market}</span>
                                <span className={`px-1.5 py-0.2 rounded text-[8px] font-black uppercase border ${getSignalBadgeColor(sig.signal_strength)}`}>
                                  {sig.signal_strength}
                                </span>
                              </div>
                              <span className="text-[10px] text-slate-400 block">{sig.rationale}</span>
                            </div>
                            <div className="text-right">
                              <span className="text-base font-black text-emerald-400 block">{Math.round(sig.probability * 100)}%</span>
                              <span className="text-[9px] text-slate-500 font-bold block">Conf {sig.confidence_score}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-2 text-center">No secondary markets meet live confidence thresholds.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 2: LIVE GOALS */}
              {activeTab === 'goals' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* Remaining xG Banner */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Expected Goals (Remaining vs Pre-Match)</span>
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Home Remaining</span>
                        <span className="text-lg font-black text-white">{goals.remaining_xg?.home?.toFixed(2) ?? '0.00'}</span>
                        <span className="text-[9px] text-slate-500 block">Pre: {goals.pre_match_xg?.home?.toFixed(2) ?? '0.00'}</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-rose-950/30 border border-rose-500/30">
                        <span className="text-[10px] text-rose-400 block font-bold">Total Remaining</span>
                        <span className="text-xl font-black text-rose-400">{goals.remaining_xg?.total?.toFixed(2) ?? '0.00'}</span>
                        <span className="text-[9px] text-rose-500/80 block">Pre: {goals.pre_match_xg?.total?.toFixed(2) ?? '0.00'}</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Away Remaining</span>
                        <span className="text-lg font-black text-white">{goals.remaining_xg?.away?.toFixed(2) ?? '0.00'}</span>
                        <span className="text-[9px] text-slate-500 block">Pre: {goals.pre_match_xg?.away?.toFixed(2) ?? '0.00'}</span>
                      </div>
                    </div>
                  </div>

                  {/* Dynamic Goal Lines (with Resolved Indicators) */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Match Goal Lines (Live Status)</span>
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

                  {/* Next Goal Probabilities */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2.5">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Next Goal Distribution</span>
                    <div className="grid grid-cols-3 gap-2 text-center text-xs">
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Home Next</span>
                        <span className="text-base font-black text-emerald-400">{Math.round((goals.next_goal?.home || 0) * 100)}%</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">No More Goals</span>
                        <span className="text-base font-black text-amber-400">{Math.round((goals.next_goal?.no_more_goals || 0) * 100)}%</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Away Next</span>
                        <span className="text-base font-black text-cyan-400">{Math.round((goals.next_goal?.away || 0) * 100)}%</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 3: LIVE CORNERS */}
              {activeTab === 'corners' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* Current & Remaining Corners */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <div className="flex items-center gap-2">
                        <Flag className="w-4 h-4 text-emerald-400" />
                        <span className="text-xs font-black uppercase tracking-wider text-white">Live Corners State</span>
                      </div>
                      <span className="text-xs font-black text-emerald-400">
                        Current Total: {corners.current_corners?.total ?? 0}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-center text-xs">
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Current Corners (H/A)</span>
                        <span className="text-base font-black text-white">{corners.current_corners?.home ?? 0} — {corners.current_corners?.away ?? 0}</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-emerald-950/30 border border-emerald-500/30">
                        <span className="text-[10px] text-emerald-400 block font-bold">Expected Remaining</span>
                        <span className="text-base font-black text-emerald-400">+{corners.remaining_expected_corners?.total?.toFixed(1) ?? '0.0'}</span>
                      </div>
                    </div>
                  </div>

                  {/* Corner Lines */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Full Match Corner Lines</span>
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
                  {/* Current Disciplinary State */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <div className="flex items-center gap-2">
                        <Square className="w-4 h-4 text-amber-400 fill-amber-400/20" />
                        <span className="text-xs font-black uppercase tracking-wider text-white">Live Cards State</span>
                      </div>
                      <span className="text-xs font-black text-amber-400">
                        Current: {cards.current_cards?.total_cards ?? 0} Cards ({cards.current_cards?.home_red + cards.current_cards?.away_red > 0 ? `${cards.current_cards?.home_red + cards.current_cards?.away_red} Red` : '0 Red'})
                      </span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-center text-xs">
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                        <span className="text-[10px] text-slate-400 block font-bold">Yellows (H/A)</span>
                        <span className="text-base font-black text-white">{cards.current_cards?.home_yellow ?? 0} — {cards.current_cards?.away_yellow ?? 0}</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-amber-950/30 border border-amber-500/30">
                        <span className="text-[10px] text-amber-400 block font-bold">Exp Remaining</span>
                        <span className="text-base font-black text-amber-400">+{cards.remaining_expected_cards?.total?.toFixed(1) ?? '0.0'}</span>
                      </div>
                    </div>
                  </div>

                  {/* Card Markets */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Remaining Card Probability</span>
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

              {/* TAB 5: MODEL DIAGNOSTICS */}
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
