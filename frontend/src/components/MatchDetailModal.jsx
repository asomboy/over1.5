import React, { useState, useEffect } from 'react';
import { X, Activity, Shield, Trophy, RefreshCw, ChevronRight, BarChart2 } from 'lucide-react';

export default function MatchDetailModal({ fixtureId, isOpen, onClose, apiRequest, darkMode }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isOpen && fixtureId) {
      fetchFixtureDetails();
    }
  }, [isOpen, fixtureId]);

  const fetchFixtureDetails = async () => {
    setLoading(true);
    try {
      const res = await apiRequest('get', `/api/fixtures/${fixtureId}/details`);
      if (res.data?.status === 'ok') {
        setData(res.data);
      }
    } catch (err) {
      console.error('Error fetching fixture details:', err);
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  const pred = data?.prediction;
  const home = data?.home_team;
  const away = data?.away_team;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-slate-950/80 backdrop-blur-md animate-fadeIn">
      <div className={`w-full max-w-2xl rounded-3xl border shadow-2xl overflow-hidden flex flex-col max-h-[90vh] transition-colors ${
        darkMode ? 'bg-slate-900 border-slate-800 text-white' : 'bg-white border-slate-200 text-slate-900'
      }`}>
        
        {/* Modal Header */}
        <div className="p-4 sm:p-6 border-b border-slate-800 flex items-center justify-between bg-gradient-to-r from-emerald-950/40 via-slate-900 to-indigo-950/40">
          <div className="space-y-0.5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-400">{data?.league_name || 'Match Analysis'}</span>
            <h2 className="text-base sm:text-xl font-black text-white">
              {home?.name || 'Home'} vs {away?.name || 'Away'}
            </h2>
          </div>
          <button 
            onClick={onClose}
            className="p-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 transition-all"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Content */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-6">
          {loading ? (
            <div className="py-12 text-center space-y-3">
              <RefreshCw className="w-8 h-8 text-emerald-400 animate-spin mx-auto" />
              <p className="text-xs font-semibold text-slate-400">Loading deep H2H & goal expectation analytics...</p>
            </div>
          ) : data ? (
            <>
              {/* Elo & Expectation Overview */}
              <div className="grid grid-cols-2 gap-3">
                <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-extrabold text-slate-300">{home.name}</span>
                    <span className="text-[10px] font-bold text-emerald-400 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30">
                      Elo {home.elo_rating}
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between text-xs text-slate-400">
                    <span>Expected Goals (xG)</span>
                    <span className="text-sm font-black text-white">{pred.predicted_home_score}</span>
                  </div>
                  {/* Recent Form Streak Badges */}
                  <div className="flex items-center gap-1 mt-2">
                    <span className="text-[10px] text-slate-400 font-bold mr-1">Form:</span>
                    {home.last_5_results && home.last_5_results.length > 0 ? (
                      home.last_5_results.map((res, i) => (
                        <span key={i} className={`w-4 h-4 rounded text-[9px] font-bold flex items-center justify-center ${
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

                <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-extrabold text-slate-300">{away.name}</span>
                    <span className="text-[10px] font-bold text-indigo-400 px-2 py-0.5 rounded-full bg-indigo-500/10 border border-indigo-500/30">
                      Elo {away.elo_rating}
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between text-xs text-slate-400">
                    <span>Expected Goals (xG)</span>
                    <span className="text-sm font-black text-white">{pred.predicted_away_score}</span>
                  </div>
                  {/* Recent Form Streak Badges */}
                  <div className="flex items-center gap-1 mt-2">
                    <span className="text-[10px] text-slate-400 font-bold mr-1">Form:</span>
                    {away.last_5_results && away.last_5_results.length > 0 ? (
                      away.last_5_results.map((res, i) => (
                        <span key={i} className={`w-4 h-4 rounded text-[9px] font-bold flex items-center justify-center ${
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
              </div>

              {/* Goal Probabilities Breakdown */}
              <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                <h3 className="text-xs font-extrabold uppercase text-slate-400 flex items-center gap-1.5">
                  <BarChart2 className="w-4 h-4 text-emerald-400" />
                  Model Market Probabilities
                </h3>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                    <span className="text-[10px] font-bold text-slate-400 uppercase block">Over 1.5 Goals</span>
                    <span className="text-base font-black text-emerald-400">{Math.round((pred.over_1_5_probability || 0) * 100)}%</span>
                  </div>
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                    <span className="text-[10px] font-bold text-slate-400 uppercase block">Over 2.5 Goals</span>
                    <span className="text-base font-black text-cyan-400">{Math.round((pred.over_2_5_probability || 0) * 100)}%</span>
                  </div>
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800">
                    <span className="text-[10px] font-bold text-slate-400 uppercase block">BTTS</span>
                    <span className="text-base font-black text-amber-400">{Math.round((pred.btts_probability || 0) * 100)}%</span>
                  </div>
                </div>
              </div>

              {/* Top Scorelines Probability */}
              {pred.top_scorelines && pred.top_scorelines.length > 0 && (
                <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                  <h3 className="text-xs font-extrabold uppercase text-slate-400">Top Predicted Scorelines</h3>
                  <div className="space-y-2">
                    {pred.top_scorelines.slice(0, 4).map((sc, i) => (
                      <div key={i} className="flex items-center justify-between text-xs">
                        <span className="font-bold text-white w-12">{sc.scoreline || sc.score}</span>
                        <div className="flex-1 mx-3 h-2 rounded-full bg-slate-800 overflow-hidden">
                          <div 
                            className="h-full bg-emerald-400 rounded-full transition-all" 
                            style={{ width: `${Math.min(100, (sc.probability || 0) * 500)}%` }} 
                          />
                        </div>
                        <span className="font-semibold text-slate-400 w-12 text-right">{Math.round((sc.probability || 0) * 100)}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Head-to-Head History */}
              <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                <h3 className="text-xs font-extrabold uppercase text-slate-400">Head-to-Head Recent Meetings</h3>
                {data.h2h_history && data.h2h_history.length > 0 ? (
                  <div className="space-y-2">
                    {data.h2h_history.map((h2h, i) => (
                      <div key={i} className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 flex items-center justify-between text-xs">
                        <span className="text-slate-400 text-[11px]">{h2h.match_date ? h2h.match_date.slice(0, 10) : ''}</span>
                        <span className="font-extrabold text-white">{h2h.home_team_name} vs {h2h.away_team_name}</span>
                        <span className="font-black text-emerald-400 px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30">
                          {h2h.score} ({h2h.total_goals} Goals)
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-slate-500 py-2">No recent head-to-head match records found in database.</p>
                )}
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
