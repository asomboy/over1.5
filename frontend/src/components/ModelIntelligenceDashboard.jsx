import React, { useState, useEffect } from 'react';
import {
  X,
  Activity,
  BarChart2,
  TrendingUp,
  Shield,
  Award,
  Clock,
  Layers,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  Percent,
  Sliders,
  ChevronRight,
  Filter
} from 'lucide-react';

export default function ModelIntelligenceDashboard({ isOpen, onClose, apiRequest, darkMode }) {
  const [activeTab, setActiveTab] = useState('overview'); // 'overview' | 'markets' | 'calibration' | 'leagues' | 'live' | 'drift'
  const [loading, setLoading] = useState(false);
  const [statusData, setStatusData] = useState(null);
  const [leaderboard, setLeaderboard] = useState([]);
  const [calibrationData, setCalibrationData] = useState(null);
  const [leaguesData, setLeaguesData] = useState([]);
  const [liveData, setLiveData] = useState(null);
  const [driftData, setDriftData] = useState(null);
  const [selectedMarket, setSelectedMarket] = useState('');

  useEffect(() => {
    if (isOpen) {
      fetchAllData();
    }
  }, [isOpen]);

  const fetchAllData = async () => {
    setLoading(true);
    try {
      const [stRes, lbRes, calRes, lgRes, lvRes, drRes] = await Promise.all([
        apiRequest('get', '/api/models/status'),
        apiRequest('get', '/api/models/leaderboard'),
        apiRequest('get', '/api/models/calibration'),
        apiRequest('get', '/api/models/league-performance'),
        apiRequest('get', '/api/live/performance'),
        apiRequest('get', '/api/models/drift')
      ]);

      if (stRes?.data) setStatusData(stRes.data);
      if (lbRes?.data?.leaderboard) setLeaderboard(lbRes.data.leaderboard);
      if (calRes?.data) setCalibrationData(calRes.data);
      if (lgRes?.data?.leagues) setLeaguesData(lgRes.data.leagues);
      if (lvRes?.data) setLiveData(lvRes.data);
      if (drRes?.data) setDriftData(drRes.data);
    } catch (err) {
      console.error('Error fetching model evaluation data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleMarketFilterChange = async (mkt) => {
    setSelectedMarket(mkt);
    try {
      const path = mkt ? `/api/models/calibration?market=${mkt}` : '/api/models/calibration';
      const res = await apiRequest('get', path);
      if (res?.data) setCalibrationData(res.data);
    } catch (err) {
      console.error('Error filtering calibration market:', err);
    }
  };

  if (!isOpen) return null;

  const getStatusBadge = (status) => {
    switch (status) {
      case 'VALIDATED':
      case 'VALIDATED (Well Calibrated)':
      case 'GOOD':
      case 'STABLE':
        return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40';
      case 'VALIDATING':
      case 'ACCEPTABLE':
      case 'ACCEPTABLE (Moderate Calibration)':
      case 'WARNING':
        return 'bg-amber-500/20 text-amber-400 border-amber-500/40';
      case 'CALIBRATION_WARNING':
      case 'MODEL_DRIFT_WARNING':
      case 'DEGRADED':
      case 'POOR':
        return 'bg-rose-500/20 text-rose-400 border-rose-500/40';
      default:
        return 'bg-slate-500/20 text-slate-400 border-slate-500/40';
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-slate-950/85 backdrop-blur-md animate-fadeIn">
      <div className={`w-full max-w-4xl rounded-3xl border shadow-2xl overflow-hidden flex flex-col max-h-[92vh] transition-colors ${
        darkMode ? 'bg-slate-900 border-slate-800 text-white' : 'bg-white border-slate-200 text-slate-900'
      }`}>
        
        {/* DASHBOARD HEADER */}
        <div className="p-4 sm:p-6 border-b border-slate-800 bg-gradient-to-r from-cyan-950/60 via-slate-900 to-indigo-950/60 flex items-center justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-wider text-cyan-400 px-2.5 py-0.5 rounded-full bg-cyan-500/10 border border-cyan-500/30">
                <BarChart2 className="w-3.5 h-3.5" />
                Continuous Model Evaluation
              </span>
              <span className="text-[9px] font-bold text-slate-400 uppercase">
                Phase 5 Central Intelligence
              </span>
            </div>
            <h2 className="text-base sm:text-xl font-black text-white tracking-tight">
              Model Calibration & Performance Leaderboard
            </h2>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={fetchAllData}
              className="p-2 rounded-2xl bg-slate-800/60 hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
              title="Refresh evaluations"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-cyan-400' : ''}`} />
            </button>
            <button
              onClick={onClose}
              className="p-2 rounded-2xl bg-slate-800/60 hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* NAVIGATION TABS */}
        <div className="p-2 border-b border-slate-800 bg-slate-950/70 flex items-center gap-1.5 overflow-x-auto custom-scrollbar">
          {[
            { id: 'overview', label: 'OVERVIEW' },
            { id: 'markets', label: 'MARKET LEADERBOARD' },
            { id: 'calibration', label: 'CALIBRATION' },
            { id: 'leagues', label: 'LEAGUES' },
            { id: 'live', label: 'LIVE PERFORMANCE' },
            { id: 'drift', label: 'MODEL DRIFT' }
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

        {/* MODAL BODY */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-4">
          {loading && !statusData ? (
            <div className="py-16 text-center space-y-3">
              <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin mx-auto" />
              <p className="text-xs font-semibold text-slate-400">Loading empirical evaluation metrics...</p>
            </div>
          ) : (
            <>
              {/* TAB 1: OVERVIEW */}
              {activeTab === 'overview' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* System Readiness Grid */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    {[
                      { title: 'Goals Model', key: 'goals_model', ver: 'Dixon-Coles v2' },
                      { title: 'Corners Model', key: 'corners_model', ver: 'NegBinomial v1' },
                      { title: 'Cards Model', key: 'cards_model', ver: 'Cards NegBin v1' },
                      { title: 'Live In-Play', key: 'live_model', ver: 'Dynamic Fusion v1' }
                    ].map((m, idx) => {
                      const stObj = statusData?.[m.key] || { status: 'INSUFFICIENT_DATA', sample_size: 0, required_sample: 100 };
                      return (
                        <div key={idx} className="p-3.5 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                          <span className="text-[10px] text-slate-400 font-bold block">{m.title}</span>
                          <div className="flex items-center justify-between">
                            <span className={`px-2 py-0.5 rounded text-[9px] font-black uppercase border ${getStatusBadge(stObj.status)}`}>
                              {stObj.status}
                            </span>
                          </div>
                          <div className="text-xs text-slate-400 pt-1 flex justify-between">
                            <span>Sample:</span>
                            <strong className="text-white">{stObj.sample_size}/{stObj.required_sample}</strong>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* Summary Metric Strip */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-slate-300 block">
                      Evaluation Core Principles
                    </span>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                      <div className="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                        <span className="text-[10px] font-bold text-cyan-400 block">Zero Fabrication</span>
                        <p className="text-[11px] text-slate-400">
                          Evaluations only link to verified historical matches with observed boxscores.
                        </p>
                      </div>
                      <div className="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                        <span className="text-[10px] font-bold text-emerald-400 block">Probabilistic Scoring</span>
                        <p className="text-[11px] text-slate-400">
                          Models are evaluated via Brier scores and Log Loss rather than simplistic binary accuracy.
                        </p>
                      </div>
                      <div className="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                        <span className="text-[10px] font-bold text-amber-400 block">Immutable Snapshots</span>
                        <p className="text-[11px] text-slate-400">
                          Pre-match predictions are fixed at kickoff and can never be mutated by post-match data.
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 2: MARKET LEADERBOARD */}
              {activeTab === 'markets' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-xs font-black uppercase tracking-wider text-white">
                        Ranked Markets Leaderboard ({leaderboard.length})
                      </span>
                      <span className="text-[10px] text-slate-400">Sorted by Composite Score</span>
                    </div>

                    {leaderboard.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                              <th className="py-2 px-2">Market</th>
                              <th className="py-2 px-2">Model</th>
                              <th className="py-2 px-2 text-center">Sample</th>
                              <th className="py-2 px-2 text-center">Brier</th>
                              <th className="py-2 px-2 text-center">ECE</th>
                              <th className="py-2 px-2 text-center">Accuracy</th>
                              <th className="py-2 px-2 text-center">Status</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {leaderboard.map((item, idx) => (
                              <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                                <td className="py-2.5 px-2 font-bold text-slate-200 capitalize">
                                  {item.market.replace(/_/g, ' ')}
                                </td>
                                <td className="py-2.5 px-2 text-slate-400 text-[11px]">{item.model_version}</td>
                                <td className="py-2.5 px-2 text-center font-semibold text-white">{item.sample_size}</td>
                                <td className="py-2.5 px-2 text-center font-black text-cyan-400">
                                  {item.brier_score !== null ? item.brier_score.toFixed(3) : '—'}
                                </td>
                                <td className="py-2.5 px-2 text-center text-slate-300">
                                  {item.ece !== null ? `${(item.ece * 100).toFixed(1)}%` : '—'}
                                </td>
                                <td className="py-2.5 px-2 text-center text-slate-300">
                                  {item.accuracy !== null ? `${(item.accuracy * 100).toFixed(0)}%` : '—'}
                                </td>
                                <td className="py-2.5 px-2 text-center">
                                  <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(item.validation_status)}`}>
                                    {item.validation_status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-6 text-center">No verified prediction market evaluations recorded yet.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 3: CALIBRATION */}
              {activeTab === 'calibration' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* Calibration Summary */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 flex items-center justify-between">
                    <div>
                      <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Expected Calibration Error (ECE)</span>
                      <span className="text-2xl font-black text-white">
                        {calibrationData?.ece !== null ? `${(calibrationData?.ece * 100).toFixed(1)}%` : 'N/A'}
                      </span>
                    </div>
                    <div className="text-right">
                      <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Calibration Status</span>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase border ${getStatusBadge(calibrationData?.status)}`}>
                        {calibrationData?.status || 'INSUFFICIENT_DATA'}
                      </span>
                    </div>
                  </div>

                  {/* 10-Decile Table */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      10-Decile Probability Reliability Table
                    </span>

                    {calibrationData?.buckets?.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                              <th className="py-2 px-2">Probability Range</th>
                              <th className="py-2 px-2 text-center">Sample</th>
                              <th className="py-2 px-2 text-center">Avg Predicted</th>
                              <th className="py-2 px-2 text-center">Actual Frequency</th>
                              <th className="py-2 px-2 text-center">Error</th>
                              <th className="py-2 px-2 text-center">Status</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {calibrationData.buckets.map((b, idx) => (
                              <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                                <td className="py-2 px-2 font-bold text-slate-200">{b.bucket_range}</td>
                                <td className="py-2 px-2 text-center text-slate-300 font-semibold">{b.predictions_count}</td>
                                <td className="py-2 px-2 text-center text-cyan-400 font-bold">{(b.avg_predicted_prob * 100).toFixed(1)}%</td>
                                <td className="py-2 px-2 text-center text-emerald-400 font-bold">{(b.actual_event_rate * 100).toFixed(1)}%</td>
                                <td className="py-2 px-2 text-center text-slate-400">{(b.calibration_error * 100).toFixed(1)}%</td>
                                <td className="py-2 px-2 text-center">
                                  <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(b.status)}`}>
                                    {b.status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-6 text-center">No calibration bucket records available.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 4: LEAGUES */}
              {activeTab === 'leagues' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      League-Specific Performance
                    </span>

                    {leaguesData.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                              <th className="py-2 px-2">Competition</th>
                              <th className="py-2 px-2 text-center">Evaluated Matches</th>
                              <th className="py-2 px-2 text-center">Brier</th>
                              <th className="py-2 px-2 text-center">ECE</th>
                              <th className="py-2 px-2 text-center">Status</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {leaguesData.map((lg, idx) => (
                              <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                                <td className="py-2.5 px-2 font-bold text-slate-200">{lg.competition}</td>
                                <td className="py-2.5 px-2 text-center text-white font-semibold">{lg.sample_size}</td>
                                <td className="py-2.5 px-2 text-center text-cyan-400 font-black">
                                  {lg.brier_score !== null ? lg.brier_score.toFixed(3) : '—'}
                                </td>
                                <td className="py-2.5 px-2 text-center text-slate-300">
                                  {lg.ece !== null ? `${(lg.ece * 100).toFixed(1)}%` : '—'}
                                </td>
                                <td className="py-2.5 px-2 text-center">
                                  <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(lg.status)}`}>
                                    {lg.status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-6 text-center">No league performance records available.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 5: LIVE */}
              {activeTab === 'live' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      Live Minute-Bucket In-Play Performance
                    </span>

                    {liveData?.minute_buckets?.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                              <th className="py-2 px-2">Minute Interval</th>
                              <th className="py-2 px-2 text-center">Snapshots</th>
                              <th className="py-2 px-2 text-center">Brier</th>
                              <th className="py-2 px-2 text-center">Log Loss</th>
                              <th className="py-2 px-2 text-center">Status</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {liveData.minute_buckets.map((b, idx) => (
                              <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                                <td className="py-2.5 px-2 font-bold text-slate-200">{b.minute_bucket}</td>
                                <td className="py-2.5 px-2 text-center text-white font-semibold">{b.sample_size}</td>
                                <td className="py-2.5 px-2 text-center text-cyan-400 font-black">
                                  {b.brier_score !== null ? b.brier_score.toFixed(3) : '—'}
                                </td>
                                <td className="py-2.5 px-2 text-center text-slate-300">
                                  {b.log_loss !== null ? b.log_loss.toFixed(3) : '—'}
                                </td>
                                <td className="py-2.5 px-2 text-center">
                                  <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(b.status)}`}>
                                    {b.status}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-6 text-center">No live in-play prediction evaluation records recorded yet.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 6: MODEL DRIFT */}
              {activeTab === 'drift' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <div>
                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">Drift Monitor Status</span>
                        <span className="text-lg font-black text-white">{driftData?.label || 'Monitoring Inactive'}</span>
                      </div>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-black uppercase border ${getStatusBadge(driftData?.status)}`}>
                        {driftData?.status || 'INSUFFICIENT_DATA'}
                      </span>
                    </div>

                    {driftData?.status !== 'INSUFFICIENT_DATA' ? (
                      <div className="grid grid-cols-2 gap-3 text-xs pt-1">
                        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                          <span className="text-[10px] text-slate-400 block font-bold">Recent Window (Brier)</span>
                          <span className="text-lg font-black text-cyan-400">{driftData?.recent_brier?.toFixed(3)}</span>
                          <span className="text-[9px] text-slate-500 block">Baseline: {driftData?.historical_brier?.toFixed(3)}</span>
                        </div>
                        <div className="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
                          <span className="text-[10px] text-slate-400 block font-bold">Brier Score Shift</span>
                          <span className={`text-lg font-black ${driftData?.brier_change_pct > 15 ? 'text-rose-400' : 'text-emerald-400'}`}>
                            {driftData?.brier_change_pct > 0 ? `+${driftData?.brier_change_pct}%` : `${driftData?.brier_change_pct}%`}
                          </span>
                          <span className="text-[9px] text-slate-500 block">Tolerance: &lt; 25%</span>
                        </div>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-4 text-center">
                        {driftData?.message || "Insufficient verified prediction records to establish a baseline."}
                      </p>
                    )}
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
