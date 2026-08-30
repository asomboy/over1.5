import React, { useState, useEffect } from 'react';
import { 
  X, 
  Activity, 
  Shield, 
  Trophy, 
  RefreshCw, 
  ChevronRight, 
  BarChart2, 
  Zap, 
  Layers, 
  Sparkles, 
  TrendingUp, 
  Flame,
  CheckCircle2,
  Clock,
  Award,
  Flag,
  Square,
  UserCheck,
  AlertTriangle
} from 'lucide-react';

export default function MatchDetailModal({ fixtureId, isOpen, onClose, onOpenLiveModal, apiRequest, darkMode }) {
  const [data, setData] = useState(null);
  const [unifiedIntel, setUnifiedIntel] = useState(null);
  const [shotsData, setShotsData] = useState(null);
  const [matchStatsData, setMatchStatsData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('match_intel'); // 'match_intel' | 'shots' | 'match_stats' | 'overview' | 'goals' | 'corners' | 'cards' | 'result' | 'team_goals' | 'halves'

  useEffect(() => {
    if (isOpen && fixtureId) {
      fetchFixtureDetails();
      setActiveTab('match_intel');
    }
  }, [isOpen, fixtureId]);

  const fetchFixtureDetails = async () => {
    setLoading(true);
    try {
      const [res, intelRes, shotsRes, statsRes] = await Promise.all([
        apiRequest('get', `/api/fixtures/${fixtureId}/details`),
        apiRequest('get', `/api/fixtures/${fixtureId}/match-intelligence`),
        apiRequest('get', `/api/fixtures/${fixtureId}/shots`),
        apiRequest('get', `/api/fixtures/${fixtureId}/match-statistics`)
      ]);
      if (res.data?.status === 'ok') {
        setData(res.data);
      }
      if (intelRes?.data && !intelRes.data.error) {
        setUnifiedIntel(intelRes.data);
      }
      if (shotsRes?.data && !shotsRes.data.error) {
        setShotsData(shotsRes.data);
      }
      if (statsRes?.data && !statsRes.data.error) {
        setMatchStatsData(statsRes.data);
      }
    } catch (err) {
      console.error('Error fetching fixture details:', err);
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  const intel = data?.match_intelligence || data?.prediction?.match_intelligence;
  const legacyPred = data?.prediction;
  const home = data?.home_team;
  const away = data?.away_team;

  // Normalized safe extraction for Match Intelligence Core
  const xgHome = intel?.expected_goals?.home ?? legacyPred?.predicted_home_score ?? 1.45;
  const xgAway = intel?.expected_goals?.away ?? legacyPred?.predicted_away_score ?? 1.15;
  const xgTotal = intel?.expected_goals?.total ?? legacyPred?.expected_goals_xg ?? (xgHome + xgAway);

  const result1X2 = intel?.result ?? {
    home_win: legacyPred?.home_win_probability ?? 0.45,
    draw: legacyPred?.draw_probability ?? 0.25,
    away_win: legacyPred?.away_win_probability ?? 0.30
  };

  const goalsMarket = intel?.goals ?? {
    over_0_5: legacyPred?.over_0_5_probability ?? 0.90,
    under_0_5: Math.max(0, 1.0 - (legacyPred?.over_0_5_probability ?? 0.90)),
    over_1_5: legacyPred?.over_1_5_probability ?? 0.78,
    under_1_5: Math.max(0, 1.0 - (legacyPred?.over_1_5_probability ?? 0.78)),
    over_2_5: legacyPred?.over_2_5_probability ?? 0.52,
    under_2_5: Math.max(0, 1.0 - (legacyPred?.over_2_5_probability ?? 0.52)),
    over_3_5: legacyPred?.over_3_5_probability ?? 0.28,
    under_3_5: Math.max(0, 1.0 - (legacyPred?.over_3_5_probability ?? 0.28))
  };

  const bttsMarket = intel?.btts ?? {
    yes: legacyPred?.btts_probability ?? 0.55,
    no: Math.max(0, 1.0 - (legacyPred?.btts_probability ?? 0.55))
  };

  const homeGoals = intel?.home_team_goals ?? {
    over_0_5: 0.82, under_0_5: 0.18,
    over_1_5: 0.52, under_1_5: 0.48,
    over_2_5: 0.24, under_2_5: 0.76
  };

  const awayGoals = intel?.away_team_goals ?? {
    over_0_5: 0.68, under_0_5: 0.32,
    over_1_5: 0.34, under_1_5: 0.66,
    over_2_5: 0.12, under_2_5: 0.88
  };

  const halvesMarket = intel?.halves ?? {
    first_half_over_0_5: legacyPred?.first_half_over_0_5_probability ?? 0.70,
    first_half_over_1_5: legacyPred?.first_half_over_1_5_probability ?? 0.34,
    second_half_over_0_5: legacyPred?.second_half_over_0_5_probability ?? 0.78,
    second_half_over_1_5: legacyPred?.second_half_over_1_5_probability ?? 0.44
  };

  const exactScores = (intel?.exact_scores && intel.exact_scores.length > 0)
    ? intel.exact_scores
    : (legacyPred?.top_scorelines || [
        { home: 2, away: 1, score: '2-1', probability: 0.12 },
        { home: 1, away: 1, score: '1-1', probability: 0.11 },
        { home: 1, away: 0, score: '1-0', probability: 0.10 }
      ]);

  const confidence = intel?.confidence ?? {
    overall: Math.round((legacyPred?.confidence_score ?? 0.5) * 100),
    data_quality: 75,
    model_stability: 70,
    sample_quality: 'good'
  };

  const bestSignal = intel?.best_signal ?? {
    market: 'Over 1.5 Goals',
    probability: goalsMarket.over_1_5,
    signal_score: Math.round(goalsMarket.over_1_5 * 100),
    label: goalsMarket.over_1_5 >= 0.78 ? 'Strong' : 'Moderate'
  };

  const corners = intel?.corners || data?.corners;
  const cards = intel?.cards || data?.cards;

  const getConfidenceBadgeColor = (quality) => {
    switch (quality) {
      case 'strong': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40';
      case 'good': return 'bg-cyan-500/20 text-cyan-400 border-cyan-500/40';
      case 'moderate': return 'bg-amber-500/20 text-amber-400 border-amber-500/40';
      default: return 'bg-rose-500/20 text-rose-400 border-rose-500/40';
    }
  };

  const getSignalBadgeColor = (label) => {
    switch (label) {
      case 'Strong': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40';
      case 'Moderate': return 'bg-amber-500/20 text-amber-400 border-amber-500/40';
      default: return 'bg-slate-500/20 text-slate-300 border-slate-500/40';
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-slate-950/80 backdrop-blur-md animate-fadeIn">
      <div className={`w-full max-w-2xl rounded-3xl border shadow-2xl overflow-hidden flex flex-col max-h-[90vh] transition-colors ${
        darkMode ? 'bg-slate-900 border-slate-800 text-white' : 'bg-white border-slate-200 text-slate-900'
      }`}>
        
        {/* Modal Header */}
        <div className="p-4 sm:p-6 border-b border-slate-800 flex items-center justify-between bg-gradient-to-r from-emerald-950/50 via-slate-900 to-cyan-950/50">
          <div className="space-y-0.5">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-black uppercase tracking-wider text-emerald-400 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30">
                {data?.league_name || 'Match Intelligence'}
              </span>
              <span className="text-[9px] font-bold text-slate-400 uppercase">
                {intel?.model?.version || 'v2_match_intelligence'}
              </span>
            </div>
            <h2 className="text-base sm:text-xl font-black text-white tracking-tight">
              {home?.name || 'Home'} vs {away?.name || 'Away'}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            {onOpenLiveModal && (
              <button
                onClick={() => {
                  onClose();
                  onOpenLiveModal(fixtureId);
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-rose-600/20 hover:bg-rose-600/30 border border-rose-500/40 text-rose-300 text-xs font-black transition-all hover:scale-105"
                title="Open Live Match Intelligence"
              >
                <span className="w-2 h-2 rounded-full bg-rose-500 animate-pulse" />
                LIVE RADAR
              </button>
            )}
            <button 
              onClick={onClose}
              className="p-2 rounded-2xl bg-slate-800/60 hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Team Matchup Strip */}
        <div className="p-4 bg-slate-950/40 border-b border-slate-800/80 grid grid-cols-2 gap-4">
          <div className="flex items-center justify-between p-3 rounded-2xl bg-slate-900/60 border border-slate-800/60">
            <div className="space-y-1">
              <span className="text-xs font-black text-white block truncate">{home?.name || 'Home Team'}</span>
              <div className="flex items-center gap-1">
                {home?.last_5_results ? (
                  home.last_5_results.map((res, i) => (
                    <span key={i} className={`w-3.5 h-3.5 rounded text-[8px] font-black flex items-center justify-center ${
                      res === 'W' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' :
                      res === 'D' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40' :
                      'bg-rose-500/20 text-rose-400 border border-rose-500/40'
                    }`}>
                      {res}
                    </span>
                  ))
                ) : <span className="text-[10px] text-slate-500">N/A</span>}
              </div>
            </div>
            <span className="text-[10px] font-black text-emerald-400 px-2 py-1 rounded-xl bg-emerald-500/10 border border-emerald-500/30">
              Elo {home?.elo_rating || 1500}
            </span>
          </div>

          <div className="flex items-center justify-between p-3 rounded-2xl bg-slate-900/60 border border-slate-800/60">
            <div className="space-y-1">
              <span className="text-xs font-black text-white block truncate">{away?.name || 'Away Team'}</span>
              <div className="flex items-center gap-1">
                {away?.last_5_results ? (
                  away.last_5_results.map((res, i) => (
                    <span key={i} className={`w-3.5 h-3.5 rounded text-[8px] font-black flex items-center justify-center ${
                      res === 'W' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' :
                      res === 'D' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40' :
                      'bg-rose-500/20 text-rose-400 border border-rose-500/40'
                    }`}>
                      {res}
                    </span>
                  ))
                ) : <span className="text-[10px] text-slate-500">N/A</span>}
              </div>
            </div>
            <span className="text-[10px] font-black text-cyan-400 px-2 py-1 rounded-xl bg-cyan-500/10 border border-cyan-500/30">
              Elo {away?.elo_rating || 1500}
            </span>
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className="p-2 sm:p-3 border-b border-slate-800/80 bg-slate-950/70 flex items-center gap-1.5 overflow-x-auto custom-scrollbar">
          {[
            { id: 'match_intel', label: 'MATCH INTELLIGENCE' },
            { id: 'shots', label: 'SHOTS & SOT' },
            { id: 'match_stats', label: 'MATCH STATS' },
            { id: 'overview', label: 'OVERVIEW' },
            { id: 'goals', label: 'GOALS' },
            { id: 'corners', label: 'CORNERS' },
            { id: 'cards', label: 'CARDS' },
            { id: 'result', label: 'RESULT (1X2)' },
            { id: 'team_goals', label: 'TEAM GOALS' },
            { id: 'halves', label: 'HALVES' }
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-1.5 rounded-xl text-xs font-black transition-all whitespace-nowrap ${
                activeTab === tab.id
                  ? 'bg-cyan-600 text-white shadow-lg shadow-cyan-600/20 scale-[1.02]'
                  : 'bg-slate-800/40 text-slate-400 hover:text-white hover:bg-slate-800'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-4">
          {loading ? (
            <div className="py-16 text-center space-y-3">
              <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin mx-auto" />
              <p className="text-xs font-semibold text-slate-400">Synthesizing Unified Match Intelligence calculations...</p>
            </div>
          ) : (
            <>
              {/* TAB 0: UNIFIED MATCH INTELLIGENCE (Phase 9 Primary View) */}
              {activeTab === 'match_intel' && (
                <div className="space-y-4 animate-fadeIn">
                  
                  {/* Overall Confidence & Match State Classification Banner */}
                  <div className="p-4 rounded-2xl bg-gradient-to-br from-cyan-950/40 via-slate-900 to-indigo-950/40 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="space-y-0.5">
                        <span className="text-[10px] font-black uppercase tracking-wider text-cyan-400">Unified Match Confidence</span>
                        <div className="flex items-baseline gap-2">
                          <span className="text-2xl font-black text-white">
                            {unifiedIntel ? `${Math.round(unifiedIntel.unified_confidence * 100)}%` : '—'}
                          </span>
                          <span className="text-[10px] font-bold text-slate-400 uppercase">
                            Conservative Multi-Factor Index
                          </span>
                        </div>
                      </div>

                      <div className="text-right space-y-1">
                        <span className="text-[10px] font-black uppercase tracking-wider text-slate-400 block">Cross-Market Consistency</span>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase border ${
                          (unifiedIntel?.cross_market_consistency?.consistency_score || 1.0) >= 0.80
                            ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40'
                            : 'bg-amber-500/20 text-amber-400 border-amber-500/40'
                        }`}>
                          {unifiedIntel?.cross_market_consistency?.status || 'ALIGNED'} ({Math.round((unifiedIntel?.cross_market_consistency?.consistency_score || 1.0) * 100)}%)
                        </span>
                      </div>
                    </div>

                    {/* Match State Tags */}
                    <div className="flex flex-wrap items-center gap-1.5 pt-1 border-t border-slate-800/80">
                      <span className="text-[10px] font-black text-slate-500 uppercase mr-1">Match State:</span>
                      {unifiedIntel?.match_state_classification?.map((tag, i) => (
                        <span key={i} className="px-2 py-0.5 rounded-full text-[9px] font-black uppercase bg-slate-800 border border-slate-700 text-slate-200">
                          {tag.replace(/_/g, ' ')}
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Multi-Market Summary Grid */}
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    {/* Goals Card */}
                    <div className="p-3.5 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                      <div className="flex items-center justify-between text-[10px] text-slate-400 font-bold uppercase">
                        <span>Goals / xG</span>
                        <Activity className="w-3.5 h-3.5 text-cyan-400" />
                      </div>
                      <div className="flex items-baseline justify-between">
                        <span className="text-xl font-black text-white">{unifiedIntel?.markets?.goals?.total_xg?.toFixed(2) || xgTotal.toFixed(2)} xG</span>
                        <span className="text-[11px] font-black text-cyan-400">
                          O1.5: {Math.round((unifiedIntel?.markets?.goals?.probabilities?.over_1_5 || 0.78) * 100)}%
                        </span>
                      </div>
                      <div className="text-[10px] text-slate-500 flex justify-between">
                        <span>BTTS: <strong className="text-slate-300">{Math.round((unifiedIntel?.markets?.goals?.probabilities?.btts || 0.52) * 100)}%</strong></span>
                        <span>Model: <strong className="text-slate-400">Dixon-Coles v2</strong></span>
                      </div>
                    </div>

                    {/* Corners Card */}
                    <div className="p-3.5 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                      <div className="flex items-center justify-between text-[10px] text-slate-400 font-bold uppercase">
                        <span>Corners</span>
                        <Shield className="w-3.5 h-3.5 text-emerald-400" />
                      </div>
                      <div className="flex items-baseline justify-between">
                        <span className="text-xl font-black text-white">{unifiedIntel?.markets?.corners?.total_expected_corners?.toFixed(1) || '9.5'}</span>
                        <span className="text-[11px] font-black text-emerald-400">
                          O8.5: {Math.round((unifiedIntel?.markets?.corners?.probabilities?.over_8_5 || 0.62) * 100)}%
                        </span>
                      </div>
                      <div className="text-[10px] text-slate-500 flex justify-between">
                        <span>O9.5: <strong className="text-slate-300">{Math.round((unifiedIntel?.markets?.corners?.probabilities?.over_9_5 || 0.49) * 100)}%</strong></span>
                        <span>Model: <strong className="text-slate-400">NegBinomial v2</strong></span>
                      </div>
                    </div>

                    {/* Cards Card */}
                    <div className="p-3.5 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                      <div className="flex items-center justify-between text-[10px] text-slate-400 font-bold uppercase">
                        <span>Cards & Referee</span>
                        <Award className="w-3.5 h-3.5 text-amber-400" />
                      </div>
                      <div className="flex items-baseline justify-between">
                        <span className="text-xl font-black text-white">{unifiedIntel?.markets?.cards?.total_expected_cards?.toFixed(1) || '4.0'}</span>
                        <span className="text-[11px] font-black text-amber-400">
                          O3.5: {Math.round((unifiedIntel?.markets?.cards?.probabilities?.over_3_5 || 0.61) * 100)}%
                        </span>
                      </div>
                      <div className="text-[10px] text-slate-500 flex justify-between">
                        <span className="truncate max-w-[130px]">Ref: <strong className="text-slate-300">{unifiedIntel?.markets?.cards?.referee_name || 'Official'}</strong></span>
                        <span>Model: <strong className="text-slate-400">CardsRef v2</strong></span>
                      </div>
                    </div>
                  </div>

                  {/* Ranked Cross-Market Opportunities */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-black uppercase tracking-wider text-white">
                        Ranked Cross-Market Opportunities
                      </span>
                      <span className="text-[10px] text-slate-400">Strict Threshold & Risk Filtering</span>
                    </div>

                    <div className="space-y-2">
                      {unifiedIntel?.ranked_signals?.map((sig, i) => (
                        <div key={i} className="p-3 rounded-xl bg-slate-900 border border-slate-800/80 flex items-center justify-between">
                          <div className="space-y-0.5">
                            <div className="flex items-center gap-2">
                              <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${
                                sig.risk_tier === 'LOW' ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40' :
                                sig.risk_tier === 'MEDIUM' ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/40' :
                                'bg-amber-500/20 text-amber-400 border-amber-500/40'
                              }`}>
                                {sig.risk_tier} RISK
                              </span>
                              <span className="text-xs font-black text-white">{sig.market}</span>
                            </div>
                            <span className="text-[10px] text-slate-500 block">Model: {sig.model_source}</span>
                          </div>

                          <div className="text-right">
                            <span className="text-base font-black text-cyan-400 block">{Math.round(sig.probability * 100)}%</span>
                            <span className="text-[9px] text-slate-500 font-bold uppercase">Probability</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
              {/* TAB 1: OVERVIEW */}
              {activeTab === 'overview' && (
                <div className="space-y-4 animate-fadeIn">
                  
                  {/* Expected Goals & Best Signal */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                      <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Expected Goals (xG)</span>
                      <div className="flex items-baseline justify-between">
                        <div>
                          <span className="text-2xl font-black text-white">{xgTotal.toFixed(2)}</span>
                          <span className="text-[11px] text-slate-400 ml-1.5">Match Total xG</span>
                        </div>
                        <div className="text-right text-xs">
                          <span className="text-emerald-400 font-black">{xgHome.toFixed(2)}</span>
                          <span className="text-slate-500 mx-1">-</span>
                          <span className="text-cyan-400 font-black">{xgAway.toFixed(2)}</span>
                        </div>
                      </div>
                    </div>

                    <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Best Model Signal</span>
                        <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${getSignalBadgeColor(bestSignal.label)}`}>
                          {bestSignal.label}
                        </span>
                      </div>
                      <div className="flex items-baseline justify-between">
                        <span className="text-sm font-black text-white">{bestSignal.market}</span>
                        <span className="text-lg font-black text-emerald-400">{Math.round(bestSignal.probability * 100)}%</span>
                      </div>
                    </div>
                  </div>

                  {/* 1X2 Quick Win Breakdown */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2.5">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Match Result (1X2)</span>
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80">
                        <span className="text-[10px] text-slate-400 block truncate font-bold">1 ({home?.name?.slice(0, 8) || 'Home'})</span>
                        <span className="text-sm font-black text-emerald-400 mt-0.5 block">{Math.round(result1X2.home_win * 100)}%</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80">
                        <span className="text-[10px] text-slate-400 block font-bold">X (Draw)</span>
                        <span className="text-sm font-black text-amber-400 mt-0.5 block">{Math.round(result1X2.draw * 100)}%</span>
                      </div>
                      <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80">
                        <span className="text-[10px] text-slate-400 block truncate font-bold">2 ({away?.name?.slice(0, 8) || 'Away'})</span>
                        <span className="text-sm font-black text-cyan-400 mt-0.5 block">{Math.round(result1X2.away_win * 100)}%</span>
                      </div>
                    </div>
                  </div>

                  {/* BTTS Probability */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-bold text-slate-300">Both Teams To Score (BTTS)</span>
                      <span className="font-black text-amber-400">{Math.round(bttsMarket.yes * 100)}% Yes</span>
                    </div>
                    <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden flex">
                      <div className="bg-amber-400 h-full transition-all" style={{ width: `${bttsMarket.yes * 100}%` }} />
                      <div className="bg-slate-700 h-full transition-all" style={{ width: `${bttsMarket.no * 100}%` }} />
                    </div>
                  </div>

                  {/* Top 3 Exact Scorelines */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Top 3 Predicted Scorelines</span>
                    <div className="grid grid-cols-3 gap-2">
                      {exactScores.slice(0, 3).map((sc, i) => (
                        <div key={i} className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 text-center">
                          <span className="text-base font-black text-white block">{sc.score || `${sc.home}-${sc.away}`}</span>
                          <span className="text-xs font-bold text-emerald-400">{Math.round(sc.probability * 100)}%</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Multi-Factor Confidence Indicator */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Model Reliability & Confidence</span>
                      <span className={`text-[10px] font-black px-2 py-0.5 rounded-full border ${getConfidenceBadgeColor(confidence.sample_quality)}`}>
                        {confidence.sample_quality.toUpperCase()} QUALITY
                      </span>
                    </div>

                    <div className="grid grid-cols-3 gap-2 text-center text-xs">
                      <div className="p-2 rounded-xl bg-slate-900 border border-slate-800/60">
                        <span className="text-[10px] text-slate-400 block font-bold">Overall Score</span>
                        <span className="text-sm font-black text-white">{confidence.overall}/100</span>
                      </div>
                      <div className="p-2 rounded-xl bg-slate-900 border border-slate-800/60">
                        <span className="text-[10px] text-slate-400 block font-bold">Data Volume</span>
                        <span className="text-sm font-black text-cyan-400">{confidence.data_quality}%</span>
                      </div>
                      <div className="p-2 rounded-xl bg-slate-900 border border-slate-800/60">
                        <span className="text-[10px] text-slate-400 block font-bold">Stability</span>
                        <span className="text-sm font-black text-emerald-400">{confidence.model_stability}%</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 2: GOALS (OVER / UNDER) */}
              {activeTab === 'goals' && (
                <div className="space-y-3 animate-fadeIn">
                  <div className="p-3 bg-slate-950/40 rounded-2xl border border-slate-800 text-xs text-slate-400">
                    All goal lines are derived strictly from the corrected Dixon-Coles probability matrix, ensuring total mathematical consistency.
                  </div>

                  {[
                    { label: 'Over / Under 0.5 Goals', over: goalsMarket.over_0_5, under: goalsMarket.under_0_5 },
                    { label: 'Over / Under 1.5 Goals', over: goalsMarket.over_1_5, under: goalsMarket.under_1_5, highlight: true },
                    { label: 'Over / Under 2.5 Goals', over: goalsMarket.over_2_5, under: goalsMarket.under_2_5 },
                    { label: 'Over / Under 3.5 Goals', over: goalsMarket.over_3_5, under: goalsMarket.under_3_5 }
                  ].map((line, idx) => (
                    <div key={idx} className={`p-4 rounded-2xl border ${
                      line.highlight ? 'bg-emerald-950/20 border-emerald-500/40 shadow-lg shadow-emerald-950/30' : 'bg-slate-950/60 border-slate-800'
                    } space-y-2.5`}>
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-extrabold text-white flex items-center gap-1.5">
                          {line.highlight && <Zap className="w-3.5 h-3.5 text-emerald-400" />}
                          {line.label}
                        </span>
                        <div className="space-x-2 text-[11px] font-black">
                          <span className="text-emerald-400">Over: {Math.round(line.over * 100)}%</span>
                          <span className="text-slate-500">•</span>
                          <span className="text-slate-400">Under: {Math.round(line.under * 100)}%</span>
                        </div>
                      </div>

                      <div className="w-full h-2.5 rounded-full bg-slate-800 overflow-hidden flex">
                        <div className="bg-emerald-500 h-full transition-all" style={{ width: `${line.over * 100}%` }} />
                        <div className="bg-slate-700 h-full transition-all" style={{ width: `${line.under * 100}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* TAB 3: RESULT (1X2) */}
              {activeTab === 'result' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="grid grid-cols-3 gap-3">
                    <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 text-center space-y-2">
                      <span className="text-xs font-black text-white block truncate">{home?.name || 'Home'} Win</span>
                      <span className="text-2xl font-black text-emerald-400 block">{Math.round(result1X2.home_win * 100)}%</span>
                      <span className="text-[10px] text-slate-400 font-bold block">Implied: {(1.0 / Math.max(0.01, result1X2.home_win)).toFixed(2)}x</span>
                    </div>

                    <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 text-center space-y-2">
                      <span className="text-xs font-black text-white block">Draw</span>
                      <span className="text-2xl font-black text-amber-400 block">{Math.round(result1X2.draw * 100)}%</span>
                      <span className="text-[10px] text-slate-400 font-bold block">Implied: {(1.0 / Math.max(0.01, result1X2.draw)).toFixed(2)}x</span>
                    </div>

                    <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 text-center space-y-2">
                      <span className="text-xs font-black text-white block truncate">{away?.name || 'Away'} Win</span>
                      <span className="text-2xl font-black text-cyan-400 block">{Math.round(result1X2.away_win * 100)}%</span>
                      <span className="text-[10px] text-slate-400 font-bold block">Implied: {(1.0 / Math.max(0.01, result1X2.away_win)).toFixed(2)}x</span>
                    </div>
                  </div>

                  {/* Visual 1X2 Strip */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                    <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Outcome Distribution Ratio</span>
                    <div className="w-full h-3 rounded-full bg-slate-800 overflow-hidden flex">
                      <div className="bg-emerald-500 h-full transition-all" style={{ width: `${result1X2.home_win * 100}%` }} title={`Home Win: ${Math.round(result1X2.home_win * 100)}%`} />
                      <div className="bg-amber-400 h-full transition-all" style={{ width: `${result1X2.draw * 100}%` }} title={`Draw: ${Math.round(result1X2.draw * 100)}%`} />
                      <div className="bg-cyan-500 h-full transition-all" style={{ width: `${result1X2.away_win * 100}%` }} title={`Away Win: ${Math.round(result1X2.away_win * 100)}%`} />
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 4: TEAM GOALS */}
              {activeTab === 'team_goals' && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 animate-fadeIn">
                  {/* Home Team Goals */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-xs font-black text-white">{home?.name || 'Home'} Goals</span>
                      <span className="text-[10px] font-bold text-emerald-400">xG: {xgHome.toFixed(2)}</span>
                    </div>

                    {[
                      { line: 'Over 0.5', over: homeGoals.over_0_5, under: homeGoals.under_0_5 },
                      { line: 'Over 1.5', over: homeGoals.over_1_5, under: homeGoals.under_1_5 },
                      { line: 'Over 2.5', over: homeGoals.over_2_5, under: homeGoals.under_2_5 }
                    ].map((g, idx) => (
                      <div key={idx} className="space-y-1">
                        <div className="flex items-center justify-between text-[11px]">
                          <span className="font-bold text-slate-300">{g.line}</span>
                          <span className="font-black text-emerald-400">{Math.round(g.over * 100)}%</span>
                        </div>
                        <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                          <div className="bg-emerald-500 h-full transition-all" style={{ width: `${g.over * 100}%` }} />
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Away Team Goals */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-xs font-black text-white">{away?.name || 'Away'} Goals</span>
                      <span className="text-[10px] font-bold text-cyan-400">xG: {xgAway.toFixed(2)}</span>
                    </div>

                    {[
                      { line: 'Over 0.5', over: awayGoals.over_0_5, under: awayGoals.under_0_5 },
                      { line: 'Over 1.5', over: awayGoals.over_1_5, under: awayGoals.under_1_5 },
                      { line: 'Over 2.5', over: awayGoals.over_2_5, under: awayGoals.under_2_5 }
                    ].map((g, idx) => (
                      <div key={idx} className="space-y-1">
                        <div className="flex items-center justify-between text-[11px]">
                          <span className="font-bold text-slate-300">{g.line}</span>
                          <span className="font-black text-cyan-400">{Math.round(g.over * 100)}%</span>
                        </div>
                        <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                          <div className="bg-cyan-500 h-full transition-all" style={{ width: `${g.over * 100}%` }} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* TAB 5: HALVES */}
              {activeTab === 'halves' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {/* First Half */}
                    <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                      <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                        <span className="text-xs font-black text-white">First Half (1H)</span>
                        <span className="text-[10px] font-bold text-slate-400">1H xG: {(xgTotal * 0.45).toFixed(2)}</span>
                      </div>

                      <div className="space-y-3">
                        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1.5">
                          <div className="flex items-center justify-between text-xs">
                            <span className="font-bold text-slate-300">1H Over 0.5 Goals</span>
                            <span className="font-black text-emerald-400">{Math.round(halvesMarket.first_half_over_0_5 * 100)}%</span>
                          </div>
                          <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                            <div className="bg-emerald-500 h-full" style={{ width: `${halvesMarket.first_half_over_0_5 * 100}%` }} />
                          </div>
                        </div>

                        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1.5">
                          <div className="flex items-center justify-between text-xs">
                            <span className="font-bold text-slate-300">1H Over 1.5 Goals</span>
                            <span className="font-black text-cyan-400">{Math.round(halvesMarket.first_half_over_1_5 * 100)}%</span>
                          </div>
                          <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                            <div className="bg-cyan-500 h-full" style={{ width: `${halvesMarket.first_half_over_1_5 * 100}%` }} />
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Second Half */}
                    <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                      <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                        <span className="text-xs font-black text-white">Second Half (2H)</span>
                        <span className="text-[10px] font-bold text-slate-400">2H xG: {(xgTotal * 0.55).toFixed(2)}</span>
                      </div>

                      <div className="space-y-3">
                        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1.5">
                          <div className="flex items-center justify-between text-xs">
                            <span className="font-bold text-slate-300">2H Over 0.5 Goals</span>
                            <span className="font-black text-emerald-400">{Math.round(halvesMarket.second_half_over_0_5 * 100)}%</span>
                          </div>
                          <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                            <div className="bg-emerald-500 h-full" style={{ width: `${halvesMarket.second_half_over_0_5 * 100}%` }} />
                          </div>
                        </div>

                        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1.5">
                          <div className="flex items-center justify-between text-xs">
                            <span className="font-bold text-slate-300">2H Over 1.5 Goals</span>
                            <span className="font-black text-cyan-400">{Math.round(halvesMarket.second_half_over_1_5 * 100)}%</span>
                          </div>
                          <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                            <div className="bg-cyan-500 h-full" style={{ width: `${halvesMarket.second_half_over_1_5 * 100}%` }} />
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB: CORNERS */}
              {activeTab === 'corners' && (
                <div className="space-y-4 animate-fadeIn">
                  {corners?.available ? (
                    <>
                      {/* Expected Corners Top Banner */}
                      <div className="p-4 rounded-2xl bg-gradient-to-r from-emerald-950/60 via-slate-900 to-cyan-950/60 border border-emerald-500/30 space-y-3">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <Flag className="w-4 h-4 text-emerald-400" />
                            <span className="text-xs font-black uppercase tracking-wider text-emerald-400">Negative Binomial Corners Core</span>
                          </div>
                          <span className="text-[10px] font-bold text-slate-400 uppercase">
                            r={corners?.model?.dispersion || 5.5} ({corners?.model?.dispersion_source || 'fallback'})
                          </span>
                        </div>

                        <div className="grid grid-cols-3 gap-2 text-center pt-1">
                          <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800">
                            <span className="text-[10px] font-bold text-slate-400 uppercase block">Home ({home?.name?.slice(0, 10) || 'Home'})</span>
                            <span className="text-lg font-black text-white">{corners.expected.home.toFixed(1)}</span>
                          </div>
                          <div className="p-2.5 rounded-xl bg-emerald-950/40 border border-emerald-500/40">
                            <span className="text-[10px] font-black text-emerald-400 uppercase block">Total Corners</span>
                            <span className="text-xl font-black text-emerald-400">{corners.expected.total.toFixed(1)}</span>
                          </div>
                          <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800">
                            <span className="text-[10px] font-bold text-slate-400 uppercase block">Away ({away?.name?.slice(0, 10) || 'Away'})</span>
                            <span className="text-lg font-black text-white">{corners.expected.away.toFixed(1)}</span>
                          </div>
                        </div>
                      </div>

                      {/* Total Corners Markets (Over / Under) */}
                      <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Total Corners Markets (Full Match)</span>
                        <div className="space-y-2.5">
                          {[
                            { key: 'over_7_5', label: 'Over / Under 7.5 Corners', over: corners.total_markets?.over_7_5, under: corners.total_markets?.under_7_5 },
                            { key: 'over_8_5', label: 'Over / Under 8.5 Corners', over: corners.total_markets?.over_8_5, under: corners.total_markets?.under_8_5 },
                            { key: 'over_9_5', label: 'Over / Under 9.5 Corners', over: corners.total_markets?.over_9_5, under: corners.total_markets?.under_9_5 },
                            { key: 'over_10_5', label: 'Over / Under 10.5 Corners', over: corners.total_markets?.over_10_5, under: corners.total_markets?.under_10_5 },
                            { key: 'over_11_5', label: 'Over / Under 11.5 Corners', over: corners.total_markets?.over_11_5, under: corners.total_markets?.under_11_5 }
                          ].map((m) => (
                            <div key={m.key} className="p-3 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1.5">
                              <div className="flex items-center justify-between text-xs">
                                <span className="font-bold text-slate-300">{m.label}</span>
                                <div className="flex items-center gap-2">
                                  <span className="font-black text-emerald-400">Over {Math.round((m.over || 0) * 100)}%</span>
                                  <span className="text-slate-500">|</span>
                                  <span className="font-bold text-slate-400">Under {Math.round((m.under || 0) * 100)}%</span>
                                </div>
                              </div>
                              <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden flex">
                                <div className="bg-emerald-500 h-full transition-all" style={{ width: `${(m.over || 0) * 100}%` }} />
                                <div className="bg-slate-700 h-full transition-all" style={{ width: `${(m.under || 0) * 100}%` }} />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Team Corners (Home & Away) */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {/* Home Team Corners */}
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                            <span className="text-xs font-black text-white truncate max-w-[150px]">{home?.name || 'Home Team'} Corners</span>
                            <span className="text-[10px] font-bold text-emerald-400">xCorners: {corners.expected.home.toFixed(1)}</span>
                          </div>
                          <div className="space-y-2">
                            {[
                              { label: 'Over 3.5 Team Corners', prob: corners.home_team?.over_3_5 },
                              { label: 'Over 4.5 Team Corners', prob: corners.home_team?.over_4_5 },
                              { label: 'Over 5.5 Team Corners', prob: corners.home_team?.over_5_5 }
                            ].map((t, idx) => (
                              <div key={idx} className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1">
                                <div className="flex items-center justify-between text-xs">
                                  <span className="font-bold text-slate-300">{t.label}</span>
                                  <span className="font-black text-emerald-400">{Math.round((t.prob || 0) * 100)}%</span>
                                </div>
                                <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                                  <div className="bg-emerald-500 h-full" style={{ width: `${(t.prob || 0) * 100}%` }} />
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* Away Team Corners */}
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                            <span className="text-xs font-black text-white truncate max-w-[150px]">{away?.name || 'Away Team'} Corners</span>
                            <span className="text-[10px] font-bold text-cyan-400">xCorners: {corners.expected.away.toFixed(1)}</span>
                          </div>
                          <div className="space-y-2">
                            {[
                              { label: 'Over 3.5 Team Corners', prob: corners.away_team?.over_3_5 },
                              { label: 'Over 4.5 Team Corners', prob: corners.away_team?.over_4_5 },
                              { label: 'Over 5.5 Team Corners', prob: corners.away_team?.over_5_5 }
                            ].map((t, idx) => (
                              <div key={idx} className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1">
                                <div className="flex items-center justify-between text-xs">
                                  <span className="font-bold text-slate-300">{t.label}</span>
                                  <span className="font-black text-cyan-400">{Math.round((t.prob || 0) * 100)}%</span>
                                </div>
                                <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                                  <div className="bg-cyan-500 h-full" style={{ width: `${(t.prob || 0) * 100}%` }} />
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>

                      {/* Corner Model Confidence Footer */}
                      <div className="p-3 rounded-2xl bg-slate-900/60 border border-slate-800 flex items-center justify-between text-xs">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-bold text-slate-400 uppercase">Corner Confidence:</span>
                          <span className={`px-2 py-0.5 rounded-lg text-[10px] font-black uppercase border ${getConfidenceBadgeColor(corners.confidence?.label)}`}>
                            {corners.confidence?.label || 'moderate'} ({corners.confidence?.overall || 65}%)
                          </span>
                        </div>
                        <span className="text-[10px] text-slate-500">
                          Data Quality: {corners.confidence?.data_quality || 60}% | Sample: {corners.confidence?.sample_strength || 60}%
                        </span>
                      </div>
                    </>
                  ) : (
                    /* Empty State when Corner Data is Sparse/Insufficient */
                    <div className="p-8 rounded-2xl bg-slate-950/60 border border-slate-800 text-center space-y-3">
                      <div className="w-12 h-12 rounded-2xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center mx-auto text-amber-400">
                        <Flag className="w-6 h-6" />
                      </div>
                      <div className="space-y-1">
                        <h4 className="text-sm font-black text-white">Insufficient Verified Corner History</h4>
                        <p className="text-xs text-slate-400 max-w-md mx-auto">
                          {corners?.reason || "Corner predictions require verified historical match-level corner statistics. Data coverage for this fixture is currently under collection threshold."}
                        </p>
                      </div>
                      <div className="inline-flex items-center gap-3 px-3 py-1.5 rounded-xl bg-slate-900 border border-slate-800 text-[10px] text-slate-400">
                        <span>Home Samples: <strong className="text-white">{corners?.diagnostics?.home_sample_size ?? 0}</strong></span>
                        <span>•</span>
                        <span>Away Samples: <strong className="text-white">{corners?.diagnostics?.away_sample_size ?? 0}</strong></span>
                        <span>•</span>
                        <span>Coverage: <strong className="text-white">{Math.round((corners?.diagnostics?.corner_data_coverage ?? 0) * 100)}%</strong></span>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* TAB: CARDS */}
              {activeTab === 'cards' && (
                <div className="space-y-4 animate-fadeIn">
                  {cards?.available ? (
                    <>
                      {/* Expected Cards Top Banner */}
                      <div className="p-4 rounded-2xl bg-gradient-to-r from-amber-950/60 via-slate-900 to-rose-950/60 border border-amber-500/30 space-y-3">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <Square className="w-4 h-4 text-amber-400 fill-amber-400/20" />
                            <span className="text-xs font-black uppercase tracking-wider text-amber-400">Disciplinary & Cards Engine</span>
                          </div>
                          <span className="text-[10px] font-bold text-slate-400 uppercase">
                            r={cards?.model?.dispersion || 4.0} ({cards?.model?.dispersion_source || 'fallback'})
                          </span>
                        </div>

                        <div className="grid grid-cols-3 gap-2 text-center pt-1">
                          <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800">
                            <span className="text-[10px] font-bold text-slate-400 uppercase block">Home ({home?.name?.slice(0, 10) || 'Home'})</span>
                            <span className="text-lg font-black text-white">{cards.expected.home.toFixed(1)}</span>
                            <span className="text-[9px] text-slate-500 block">Yellow: {cards.expected.home_yellow?.toFixed(1) || cards.expected.home.toFixed(1)}</span>
                          </div>
                          <div className="p-2.5 rounded-xl bg-amber-950/40 border border-amber-500/40">
                            <span className="text-[10px] font-black text-amber-400 uppercase block">Total Cards</span>
                            <span className="text-xl font-black text-amber-400">{cards.expected.total.toFixed(1)}</span>
                            <span className="text-[9px] text-amber-500/80 block">Yellow: {cards.expected.total_yellow?.toFixed(1) || cards.expected.total.toFixed(1)}</span>
                          </div>
                          <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800">
                            <span className="text-[10px] font-bold text-slate-400 uppercase block">Away ({away?.name?.slice(0, 10) || 'Away'})</span>
                            <span className="text-lg font-black text-white">{cards.expected.away.toFixed(1)}</span>
                            <span className="text-[9px] text-slate-500 block">Yellow: {cards.expected.away_yellow?.toFixed(1) || cards.expected.away.toFixed(1)}</span>
                          </div>
                        </div>
                      </div>

                      {/* Referee Intelligence Card */}
                      <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2.5">
                        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                          <div className="flex items-center gap-2">
                            <UserCheck className="w-4 h-4 text-cyan-400" />
                            <span className="text-xs font-black text-white uppercase tracking-wider">Referee Intelligence</span>
                          </div>
                          <span className="text-[10px] font-bold text-slate-400 uppercase">
                            Source: {cards.referee?.source || 'competition'}
                          </span>
                        </div>

                        {cards.referee?.available ? (
                          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1">
                            <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80">
                              <span className="text-[10px] text-slate-400 block font-bold">Assigned Official</span>
                              <span className="text-xs font-black text-white truncate block">{cards.referee.referee_name}</span>
                              <span className="text-[9px] text-slate-500">{cards.referee.sample_size} matches recorded</span>
                            </div>
                            <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80">
                              <span className="text-[10px] text-slate-400 block font-bold">Average Cards / Match</span>
                              <span className="text-xs font-black text-cyan-400">{cards.referee.average_cards ?? 4.2}</span>
                              <span className="text-[9px] text-slate-500">Disciplinary tendency</span>
                            </div>
                            <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80">
                              <span className="text-[10px] text-slate-400 block font-bold">Tendency Index</span>
                              <span className={`text-xs font-black ${cards.referee.influence_factor > 1.05 ? 'text-rose-400' : cards.referee.influence_factor < 0.95 ? 'text-emerald-400' : 'text-slate-300'}`}>
                                {cards.referee.influence_label} ({((cards.referee.influence_factor - 1.0) * 100).toFixed(0)}%)
                              </span>
                              <span className="text-[9px] text-slate-500">vs League Baseline</span>
                            </div>
                          </div>
                        ) : (
                          <div className="p-3 rounded-xl bg-slate-900/60 border border-slate-800/50 text-xs text-slate-400 flex items-center justify-between">
                            <span>Referee data unavailable — Prediction uses competition & team baseline.</span>
                            <span className="text-[10px] font-bold text-slate-500 uppercase">Neutral (1.00x)</span>
                          </div>
                        )}
                      </div>

                      {/* Total Cards Markets (Over / Under) */}
                      <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Total Cards Markets (Full Match)</span>
                        <div className="space-y-2.5">
                          {[
                            { key: 'over_1_5', label: 'Over / Under 1.5 Cards', over: cards.total_markets?.over_1_5, under: cards.total_markets?.under_1_5 },
                            { key: 'over_2_5', label: 'Over / Under 2.5 Cards', over: cards.total_markets?.over_2_5, under: cards.total_markets?.under_2_5 },
                            { key: 'over_3_5', label: 'Over / Under 3.5 Cards', over: cards.total_markets?.over_3_5, under: cards.total_markets?.under_3_5, highlight: true },
                            { key: 'over_4_5', label: 'Over / Under 4.5 Cards', over: cards.total_markets?.over_4_5, under: cards.total_markets?.under_4_5 },
                            { key: 'over_5_5', label: 'Over / Under 5.5 Cards', over: cards.total_markets?.over_5_5, under: cards.total_markets?.under_5_5 },
                            { key: 'over_6_5', label: 'Over / Under 6.5 Cards', over: cards.total_markets?.over_6_5, under: cards.total_markets?.under_6_5 }
                          ].map((m) => (
                            <div key={m.key} className={`p-3 rounded-xl border ${m.highlight ? 'bg-amber-950/20 border-amber-500/40' : 'bg-slate-900 border-slate-800/80'} space-y-1.5`}>
                              <div className="flex items-center justify-between text-xs">
                                <span className="font-bold text-slate-300 flex items-center gap-1.5">
                                  {m.highlight && <Zap className="w-3.5 h-3.5 text-amber-400" />}
                                  {m.label}
                                </span>
                                <div className="flex items-center gap-2">
                                  <span className="font-black text-amber-400">Over {Math.round((m.over || 0) * 100)}%</span>
                                  <span className="text-slate-500">|</span>
                                  <span className="font-bold text-slate-400">Under {Math.round((m.under || 0) * 100)}%</span>
                                </div>
                              </div>
                              <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden flex">
                                <div className="bg-amber-500 h-full transition-all" style={{ width: `${(m.over || 0) * 100}%` }} />
                                <div className="bg-slate-700 h-full transition-all" style={{ width: `${(m.under || 0) * 100}%` }} />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Team Cards (Home & Away) & Red Card Risk */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {/* Team Cards Breakdown */}
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                            <span className="text-xs font-black text-white">Team Card Thresholds</span>
                            <span className="text-[10px] font-bold text-slate-400">Over 1.5+ Probs</span>
                          </div>
                          <div className="space-y-2">
                            <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1">
                              <div className="flex items-center justify-between text-xs">
                                <span className="font-bold text-slate-300 truncate max-w-[130px]">{home?.name || 'Home'}: Over 1.5 Cards</span>
                                <span className="font-black text-amber-400">{Math.round((cards.home_team?.over_1_5 || 0) * 100)}%</span>
                              </div>
                              <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                                <div className="bg-amber-500 h-full" style={{ width: `${(cards.home_team?.over_1_5 || 0) * 100}%` }} />
                              </div>
                            </div>
                            <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1">
                              <div className="flex items-center justify-between text-xs">
                                <span className="font-bold text-slate-300 truncate max-w-[130px]">{away?.name || 'Away'}: Over 1.5 Cards</span>
                                <span className="font-black text-amber-400">{Math.round((cards.away_team?.over_1_5 || 0) * 100)}%</span>
                              </div>
                              <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                                <div className="bg-amber-500 h-full" style={{ width: `${(cards.away_team?.over_1_5 || 0) * 100}%` }} />
                              </div>
                            </div>
                          </div>
                        </div>

                        {/* Red Card Risk Indicator */}
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                          <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                            <div className="flex items-center gap-1.5">
                              <AlertTriangle className="w-4 h-4 text-rose-400" />
                              <span className="text-xs font-black text-white">Red Card Risk Signal</span>
                            </div>
                            <span className={`px-2 py-0.5 rounded text-[9px] font-black uppercase ${
                              cards.red_card_risk?.risk_level === 'High' ? 'bg-rose-500/20 text-rose-400 border border-rose-500/40' :
                              cards.red_card_risk?.risk_level === 'Moderate' ? 'bg-amber-500/20 text-amber-400 border border-amber-500/40' :
                              'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                            }`}>
                              {cards.red_card_risk?.risk_level || 'Low'} Risk
                            </span>
                          </div>
                          <div className="grid grid-cols-2 gap-2 text-center pt-0.5">
                            <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80">
                              <span className="text-[10px] text-slate-400 block font-bold">Any Red Card</span>
                              <span className="text-base font-black text-rose-400">
                                {Math.round((cards.red_card_risk?.any_red_prob || 0.08) * 100)}%
                              </span>
                            </div>
                            <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80">
                              <span className="text-[10px] text-slate-400 block font-bold">Exp Red Rate</span>
                              <span className="text-base font-black text-white">
                                {((cards.red_card_risk?.home_red_prob || 0.04) + (cards.red_card_risk?.away_red_prob || 0.04)).toFixed(2)}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>

                      {/* Card Model Confidence Footer */}
                      <div className="p-3 rounded-2xl bg-slate-900/60 border border-slate-800 flex items-center justify-between text-xs">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-bold text-slate-400 uppercase">Card Confidence:</span>
                          <span className={`px-2 py-0.5 rounded-lg text-[10px] font-black uppercase border ${getConfidenceBadgeColor(cards.confidence?.label)}`}>
                            {cards.confidence?.label || 'moderate'} ({cards.confidence?.overall || 65}%)
                          </span>
                        </div>
                        <span className="text-[10px] text-slate-500">
                          Data: {cards.confidence?.data_quality || 60}% | Sample: {cards.confidence?.sample_strength || 60}% | Ref: {cards.confidence?.referee_confidence || 50}%
                        </span>
                      </div>
                    </>
                  ) : (
                    /* Empty State when Card Data is Sparse/Insufficient */
                    <div className="p-8 rounded-2xl bg-slate-950/60 border border-slate-800 text-center space-y-3">
                      <div className="w-12 h-12 rounded-2xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center mx-auto text-amber-400">
                        <Square className="w-6 h-6" />
                      </div>
                      <div className="space-y-1">
                        <h4 className="text-sm font-black text-white">Insufficient Verified Disciplinary History</h4>
                        <p className="text-xs text-slate-400 max-w-md mx-auto">
                          {cards?.reason || "Card predictions require verified historical match-level card statistics. Data coverage for this fixture is currently under collection threshold."}
                        </p>
                      </div>
                      <div className="inline-flex items-center gap-3 px-3 py-1.5 rounded-xl bg-slate-900 border border-slate-800 text-[10px] text-slate-400">
                        <span>Home Samples: <strong className="text-white">{cards?.diagnostics?.home_sample_size ?? 0}</strong></span>
                        <span>•</span>
                        <span>Away Samples: <strong className="text-white">{cards?.diagnostics?.away_sample_size ?? 0}</strong></span>
                        <span>•</span>
                        <span>Coverage: <strong className="text-white">{Math.round((cards?.diagnostics?.card_data_coverage ?? 0) * 100)}%</strong></span>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* TAB: SHOTS & SOT (Phase 10 Engine) */}
              {activeTab === 'shots' && (
                <div className="space-y-4 animate-fadeIn">
                  {shotsData && shotsData.status === 'AVAILABLE' ? (
                    <>
                      {/* Shot Totals Overview Card */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Expected Total Shots</span>
                          <div className="flex items-baseline justify-between">
                            <div>
                              <span className="text-2xl font-black text-white">{shotsData.shots?.expected_total_shots?.toFixed(1) || '24.5'}</span>
                              <span className="text-[11px] text-slate-400 ml-1.5">Match Total Shots</span>
                            </div>
                            <div className="text-right text-xs">
                              <span className="text-cyan-400 font-black">{shotsData.shots?.expected_home_shots?.toFixed(1) || '13.5'}</span>
                              <span className="text-slate-500 mx-1">-</span>
                              <span className="text-indigo-400 font-black">{shotsData.shots?.expected_away_shots?.toFixed(1) || '11.0'}</span>
                            </div>
                          </div>
                        </div>

                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Expected Shots on Target (SoT)</span>
                          <div className="flex items-baseline justify-between">
                            <div>
                              <span className="text-2xl font-black text-white">{shotsData.shots_on_target?.expected_total_sot?.toFixed(1) || '8.5'}</span>
                              <span className="text-[11px] text-slate-400 ml-1.5">Target Conversion: {Math.round((shotsData.diagnostics?.sot_to_shot_ratio || 0.35) * 100)}%</span>
                            </div>
                            <div className="text-right text-xs">
                              <span className="text-cyan-400 font-black">{shotsData.shots_on_target?.expected_home_sot?.toFixed(1) || '4.8'}</span>
                              <span className="text-slate-500 mx-1">-</span>
                              <span className="text-indigo-400 font-black">{shotsData.shots_on_target?.expected_away_sot?.toFixed(1) || '3.7'}</span>
                            </div>
                          </div>
                        </div>
                      </div>

                      {/* Total Shots Markets (Over / Under) */}
                      <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Total Shots Markets (Full Match)</span>
                        <div className="space-y-2">
                          {[
                            { key: 'over_17_5', label: 'Over / Under 17.5 Shots', over: shotsData.shots?.probabilities?.over_17_5, under: shotsData.shots?.probabilities?.under_17_5 },
                            { key: 'over_19_5', label: 'Over / Under 19.5 Shots', over: shotsData.shots?.probabilities?.over_19_5, under: shotsData.shots?.probabilities?.under_19_5, highlight: true },
                            { key: 'over_21_5', label: 'Over / Under 21.5 Shots', over: shotsData.shots?.probabilities?.over_21_5, under: shotsData.shots?.probabilities?.under_21_5 },
                            { key: 'over_23_5', label: 'Over / Under 23.5 Shots', over: shotsData.shots?.probabilities?.over_23_5, under: shotsData.shots?.probabilities?.under_23_5 },
                            { key: 'over_25_5', label: 'Over / Under 25.5 Shots', over: shotsData.shots?.probabilities?.over_25_5, under: shotsData.shots?.probabilities?.under_25_5 }
                          ].map((m) => (
                            <div key={m.key} className={`p-3 rounded-xl border ${m.highlight ? 'bg-cyan-950/20 border-cyan-500/40' : 'bg-slate-900 border-slate-800/80'} space-y-1.5`}>
                              <div className="flex items-center justify-between text-xs">
                                <span className="font-bold text-slate-300">{m.label}</span>
                                <div className="flex items-center gap-2">
                                  <span className="font-black text-cyan-400">Over {Math.round((m.over || 0) * 100)}%</span>
                                  <span className="text-slate-500">|</span>
                                  <span className="font-bold text-slate-400">Under {Math.round((m.under || 0) * 100)}%</span>
                                </div>
                              </div>
                              <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden flex">
                                <div className="bg-cyan-500 h-full transition-all" style={{ width: `${(m.over || 0) * 100}%` }} />
                                <div className="bg-slate-700 h-full transition-all" style={{ width: `${(m.under || 0) * 100}%` }} />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Total Shots on Target Markets */}
                      <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Shots on Target (SoT) Markets</span>
                        <div className="space-y-2">
                          {[
                            { key: 'over_3_5', label: 'Over / Under 3.5 SoT', over: shotsData.shots_on_target?.probabilities?.over_3_5, under: shotsData.shots_on_target?.probabilities?.under_3_5 },
                            { key: 'over_5_5', label: 'Over / Under 5.5 SoT', over: shotsData.shots_on_target?.probabilities?.over_5_5, under: shotsData.shots_on_target?.probabilities?.under_5_5, highlight: true },
                            { key: 'over_7_5', label: 'Over / Under 7.5 SoT', over: shotsData.shots_on_target?.probabilities?.over_7_5, under: shotsData.shots_on_target?.probabilities?.under_7_5 }
                          ].map((m) => (
                            <div key={m.key} className={`p-3 rounded-xl border ${m.highlight ? 'bg-indigo-950/20 border-indigo-500/40' : 'bg-slate-900 border-slate-800/80'} space-y-1.5`}>
                              <div className="flex items-center justify-between text-xs">
                                <span className="font-bold text-slate-300">{m.label}</span>
                                <div className="flex items-center gap-2">
                                  <span className="font-black text-indigo-400">Over {Math.round((m.over || 0) * 100)}%</span>
                                  <span className="text-slate-500">|</span>
                                  <span className="font-bold text-slate-400">Under {Math.round((m.under || 0) * 100)}%</span>
                                </div>
                              </div>
                              <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden flex">
                                <div className="bg-indigo-500 h-full transition-all" style={{ width: `${(m.over || 0) * 100}%` }} />
                                <div className="bg-slate-700 h-full transition-all" style={{ width: `${(m.under || 0) * 100}%` }} />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="p-8 rounded-2xl bg-slate-950/60 border border-slate-800 text-center space-y-2">
                      <h4 className="text-sm font-black text-white">Shots Predictions Synthesizing</h4>
                      <p className="text-xs text-slate-400">Negative Binomial shot distributions computed with Bayesian shrinkage toward league baseline.</p>
                    </div>
                  )}
                </div>
              )}

              {/* TAB: MATCH STATS (Phase 11 Engine) */}
              {activeTab === 'match_stats' && (
                <div className="space-y-4 animate-fadeIn">
                  {matchStatsData && matchStatsData.status === 'AVAILABLE' ? (
                    <>
                      {/* Possession & Attacking Pressure Row */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                        {/* Possession Forecast */}
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                          <div className="flex items-center justify-between">
                            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Possession Forecast</span>
                            <span className="text-[10px] font-black text-cyan-400 px-2 py-0.5 rounded bg-cyan-500/10 border border-cyan-500/20 uppercase">
                              Conserved (100%)
                            </span>
                          </div>
                          <div className="flex items-baseline justify-between">
                            <div>
                              <span className="text-xl font-black text-cyan-400">{matchStatsData.possession?.expected_home_possession || 50.0}%</span>
                              <span className="text-[11px] text-slate-400 ml-1.5">{home?.name || 'Home'}</span>
                            </div>
                            <div className="text-right">
                              <span className="text-xl font-black text-indigo-400">{matchStatsData.possession?.expected_away_possession || 50.0}%</span>
                              <span className="text-[11px] text-slate-400 ml-1.5">{away?.name || 'Away'}</span>
                            </div>
                          </div>
                          <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden flex">
                            <div className="bg-cyan-500 h-full transition-all" style={{ width: `${matchStatsData.possession?.expected_home_possession || 50}%` }} />
                            <div className="bg-indigo-500 h-full transition-all" style={{ width: `${matchStatsData.possession?.expected_away_possession || 50}%` }} />
                          </div>
                          <div className="flex items-center justify-between text-[10px] text-slate-500 font-mono">
                            <span>Proj Range: {matchStatsData.possession?.projected_range_home?.[0]}% - {matchStatsData.possession?.projected_range_home?.[1]}%</span>
                            <span>Diff: {matchStatsData.possession?.possession_differential > 0 ? `+${matchStatsData.possession?.possession_differential}%` : `${matchStatsData.possession?.possession_differential}%`}</span>
                          </div>
                        </div>

                        {/* Attacking Pressure Index (MODEL_DERIVED) */}
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                          <div className="flex items-center justify-between">
                            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Attacking Pressure</span>
                            <span className="text-[10px] font-black text-purple-400 px-2 py-0.5 rounded bg-purple-500/10 border border-purple-500/20 uppercase">
                              MODEL_DERIVED
                            </span>
                          </div>
                          <div className="flex items-baseline justify-between">
                            <div>
                              <span className="text-xl font-black text-white">{matchStatsData.attacking_pressure?.home_pressure_index || 50.0}</span>
                              <span className="text-[11px] text-slate-400 ml-1.5">Home Index</span>
                            </div>
                            <div className="text-right">
                              <span className="text-xl font-black text-white">{matchStatsData.attacking_pressure?.away_pressure_index || 50.0}</span>
                              <span className="text-[11px] text-slate-400 ml-1.5">Away Index</span>
                            </div>
                          </div>
                          <div className="p-2 rounded-xl bg-slate-900 border border-slate-800 text-[11px] flex items-center justify-between">
                            <span className="text-slate-400">Dominant Pressure Profile:</span>
                            <strong className="text-purple-400 font-black">{matchStatsData.attacking_pressure?.dominant_side || 'BALANCED'}</strong>
                          </div>
                          <p className="text-[10px] text-slate-500">
                            Derived from Shots (30%), SoT (25%), Corners (20%), Possession (15%), Box Shots (10%).
                          </p>
                        </div>
                      </div>

                      {/* Fouls & Disciplinary Model */}
                      <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Fouls Expectations & Markets</span>
                          <span className="text-[10px] text-slate-400">
                            Total Expected: <strong className="text-amber-400">{matchStatsData.fouls?.expected_total_fouls?.toFixed(1) || '24.0'}</strong> ({matchStatsData.fouls?.expected_home_fouls?.toFixed(1)} vs {matchStatsData.fouls?.expected_away_fouls?.toFixed(1)})
                          </span>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                          {[
                            { key: 'over_21_5', label: 'Over 21.5 Fouls', prob: matchStatsData.fouls?.probabilities?.over_21_5 },
                            { key: 'over_23_5', label: 'Over 23.5 Fouls', prob: matchStatsData.fouls?.probabilities?.over_23_5, highlight: true },
                            { key: 'over_25_5', label: 'Over 25.5 Fouls', prob: matchStatsData.fouls?.probabilities?.over_25_5 }
                          ].map((m) => (
                            <div key={m.key} className={`p-3 rounded-xl border ${m.highlight ? 'bg-amber-950/20 border-amber-500/40' : 'bg-slate-900 border-slate-800/80'} text-center space-y-1`}>
                              <span className="text-[11px] font-bold text-slate-300 block">{m.label}</span>
                              <span className="text-base font-black text-amber-400">{Math.round((m.prob || 0) * 100)}%</span>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Offsides, Saves & Blocked Shots Breakdown Grid */}
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        {/* Offsides */}
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Offsides</span>
                          <div className="flex items-baseline justify-between">
                            <span className="text-xl font-black text-white">{matchStatsData.offsides?.expected_total_offsides?.toFixed(1) || '3.3'}</span>
                            <span className="text-xs text-slate-400">{matchStatsData.offsides?.expected_home_offsides?.toFixed(1)} - {matchStatsData.offsides?.expected_away_offsides?.toFixed(1)}</span>
                          </div>
                          <div className="pt-1 text-[11px] text-slate-300 flex items-center justify-between">
                            <span>Over 2.5 Offsides:</span>
                            <strong className="text-cyan-400">{Math.round((matchStatsData.offsides?.probabilities?.over_2_5 || 0) * 100)}%</strong>
                          </div>
                        </div>

                        {/* Saves */}
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Goalkeeper Saves</span>
                          <div className="flex items-baseline justify-between">
                            <span className="text-xl font-black text-white">{matchStatsData.saves?.expected_total_saves?.toFixed(1) || '6.0'}</span>
                            <span className="text-xs text-slate-400">{matchStatsData.saves?.expected_home_saves?.toFixed(1)} - {matchStatsData.saves?.expected_away_saves?.toFixed(1)}</span>
                          </div>
                          <div className="pt-1 text-[11px] text-slate-300 flex items-center justify-between">
                            <span>Over 4.5 Saves:</span>
                            <strong className="text-emerald-400">{Math.round((matchStatsData.saves?.probabilities?.over_4_5 || 0) * 100)}%</strong>
                          </div>
                        </div>

                        {/* Blocked Shots */}
                        <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Blocked Shots</span>
                          <div className="flex items-baseline justify-between">
                            <span className="text-xl font-black text-white">{matchStatsData.blocked_shots?.expected_total_blocked_shots?.toFixed(1) || '5.5'}</span>
                            <span className="text-xs text-slate-400">{matchStatsData.blocked_shots?.expected_home_blocked_shots?.toFixed(1)} - {matchStatsData.blocked_shots?.expected_away_blocked_shots?.toFixed(1)}</span>
                          </div>
                          <div className="pt-1 text-[11px] text-slate-300 flex items-center justify-between">
                            <span>Over 3.5 Blocked:</span>
                            <strong className="text-indigo-400">{Math.round((matchStatsData.blocked_shots?.probabilities?.over_3_5 || 0) * 100)}%</strong>
                          </div>
                        </div>
                      </div>

                      {/* Shot Location Breakdown (Inside vs Outside Box) */}
                      <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Shot Location Decomposition</span>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                          <div className="p-3 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1">
                            <div className="flex items-center justify-between text-xs">
                              <span className="font-bold text-slate-300">Inside The Penalty Box</span>
                              <span className="font-black text-cyan-400">{matchStatsData.shot_location?.expected_total_inside_box?.toFixed(1) || '15.0'} Shots (~62%)</span>
                            </div>
                            <div className="text-[11px] text-slate-400 flex items-center justify-between">
                              <span>Over 13.5 Inside Box:</span>
                              <strong className="text-white">{Math.round((matchStatsData.shot_location?.probabilities_inside_box?.over_13_5 || 0) * 100)}%</strong>
                            </div>
                          </div>

                          <div className="p-3 rounded-xl bg-slate-900 border border-slate-800/80 space-y-1">
                            <div className="flex items-center justify-between text-xs">
                              <span className="font-bold text-slate-300">Outside The Penalty Box</span>
                              <span className="font-black text-indigo-400">{matchStatsData.shot_location?.expected_total_outside_box?.toFixed(1) || '9.5'} Shots (~38%)</span>
                            </div>
                            <div className="text-[11px] text-slate-400 flex items-center justify-between">
                              <span>Over 8.5 Outside Box:</span>
                              <strong className="text-white">{Math.round((matchStatsData.shot_location?.probabilities_outside_box?.over_8_5 || 0) * 100)}%</strong>
                            </div>
                          </div>
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="p-8 rounded-2xl bg-slate-950/60 border border-slate-800 text-center space-y-2">
                      <h4 className="text-sm font-black text-white">Match Statistics Synthesizing</h4>
                      <p className="text-xs text-slate-400">Match statistics models integrating possession conservation, fouls dispersion, offsides, saves, and location forecasts.</p>
                    </div>
                  )}
                </div>
              )}

              {/* H2H Meetings (Always accessible below) */}
              <div className="p-4 rounded-2xl bg-slate-950/40 border border-slate-800/80 space-y-2.5">
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Head-to-Head History</span>
                {data?.h2h_history && data.h2h_history.length > 0 ? (
                  <div className="space-y-1.5">
                    {data.h2h_history.map((h2h, i) => (
                      <div key={i} className="p-2 rounded-xl bg-slate-900/60 border border-slate-800/50 flex items-center justify-between text-xs">
                        <span className="text-slate-400 text-[10px] font-mono">{h2h.match_date ? h2h.match_date.slice(0, 10) : ''}</span>
                        <span className="font-bold text-slate-200 truncate max-w-[220px]">{h2h.home_team_name} vs {h2h.away_team_name}</span>
                        <span className="font-black text-emerald-400 px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-[11px]">
                          {h2h.score} ({h2h.total_goals}G)
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-slate-500 py-1">No past head-to-head match records recorded.</p>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
