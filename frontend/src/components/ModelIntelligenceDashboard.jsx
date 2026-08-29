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
  Filter,
  Database,
  Server,
  Play,
  Pause,
  RotateCcw,
  Bell,
  HardDrive,
  Cpu,
  FileCheck,
  GitPullRequest
} from 'lucide-react';

export default function ModelIntelligenceDashboard({ isOpen, onClose, apiRequest, darkMode }) {
  const [activeTab, setActiveTab] = useState('real_validation'); // 'real_validation' | 'provenance' | 'overview' | 'jobs' | 'alerts' | 'coverage' | 'readiness' | 'markets' | 'calibration' | 'backfill' | 'live'
  const [loading, setLoading] = useState(false);
  const [runningJob, setRunningJob] = useState('');
  const [backingUp, setBackingUp] = useState(false);
  const [backfillAction, setBackfillAction] = useState('');

  const [systemStatus, setSystemStatus] = useState(null);
  const [coverageData, setCoverageData] = useState(null);
  const [competitionsCoverage, setCompetitionsCoverage] = useState([]);
  const [readinessData, setReadinessData] = useState(null);
  const [leaderboard, setLeaderboard] = useState([]);
  const [calibrationData, setCalibrationData] = useState(null);
  const [liveData, setLiveData] = useState(null);
  const [backfillStatus, setBackfillStatus] = useState(null);
  const [providersData, setProvidersData] = useState([]);
  const [jobsData, setJobsData] = useState(null);
  const [alertsData, setAlertsData] = useState([]);
  const [backupsData, setBackupsData] = useState([]);
  
  // Phase 8 states
  const [provenanceData, setProvenanceData] = useState([]);
  const [conflictsData, setConflictsData] = useState([]);
  const [marketReadiness, setMarketReadiness] = useState([]);
  const [realLeaderboard, setRealLeaderboard] = useState([]);
  const [realValidation, setRealValidation] = useState(null);

  useEffect(() => {
    if (isOpen) {
      fetchAllData();
    }
  }, [isOpen]);

  const fetchAllData = async () => {
    setLoading(true);
    try {
      const [
        stRes, covRes, compRes, readRes, lbRes, calRes, lvRes, bfRes, provRes, jbRes, altRes, bkRes,
        provAudRes, confRes, mktReadRes, realLbRes, realValRes
      ] = await Promise.all([
        apiRequest('get', '/api/system/status'),
        apiRequest('get', '/api/data-quality/overview'),
        apiRequest('get', '/api/data-quality/competitions'),
        apiRequest('get', '/api/models/readiness'),
        apiRequest('get', '/api/models/leaderboard'),
        apiRequest('get', '/api/models/calibration'),
        apiRequest('get', '/api/live/performance'),
        apiRequest('get', '/api/data-quality/backfill-status'),
        apiRequest('get', '/api/system/providers'),
        apiRequest('get', '/api/system/jobs'),
        apiRequest('get', '/api/system/alerts'),
        apiRequest('get', '/api/system/backups'),
        apiRequest('get', '/api/data-quality/provenance'),
        apiRequest('get', '/api/data-quality/conflicts'),
        apiRequest('get', '/api/models/market-readiness'),
        apiRequest('get', '/api/models/real-leaderboard'),
        apiRequest('get', '/api/models/real-validation')
      ]);

      if (stRes?.data) setSystemStatus(stRes.data);
      if (covRes?.data) setCoverageData(covRes.data);
      if (compRes?.data?.competitions) setCompetitionsCoverage(compRes.data.competitions);
      if (readRes?.data) setReadinessData(readRes.data);
      if (lbRes?.data?.leaderboard) setLeaderboard(lbRes.data.leaderboard);
      if (calRes?.data) setCalibrationData(calRes.data);
      if (lvRes?.data) setLiveData(lvRes.data);
      if (bfRes?.data) setBackfillStatus(bfRes.data);
      if (provRes?.data?.providers) setProvidersData(provRes.data.providers);
      if (jbRes?.data) setJobsData(jbRes.data);
      if (altRes?.data?.alerts) setAlertsData(altRes.data.alerts);
      if (bkRes?.data?.backups) setBackupsData(bkRes.data.backups);
      if (provAudRes?.data?.provenance) setProvenanceData(provAudRes.data.provenance);
      if (confRes?.data?.conflicts) setConflictsData(confRes.data.conflicts);
      if (mktReadRes?.data?.markets) setMarketReadiness(mktReadRes.data.markets);
      if (realLbRes?.data?.leaderboard) setRealLeaderboard(realLbRes.data.leaderboard);
      if (realValRes?.data) setRealValidation(realValRes.data);
    } catch (err) {
      console.error('Error fetching production operations intelligence:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRunJob = async (jobName) => {
    setRunningJob(jobName);
    try {
      await apiRequest('post', `/api/system/jobs/${jobName}/run`);
      await fetchAllData();
    } catch (err) {
      console.error(`Error running job ${jobName}:`, err);
    } finally {
      setRunningJob('');
    }
  };

  const handleBackfillControl = async (action) => {
    setBackfillAction(action);
    try {
      if (action === 'start') await apiRequest('post', '/api/data-quality/backfill/start');
      else if (action === 'pause') await apiRequest('post', '/api/data-quality/backfill/pause');
      else if (action === 'resume') await apiRequest('post', '/api/data-quality/backfill/resume');
      else if (action === 'retry') await apiRequest('post', '/api/data-quality/backfill/retry');
      await fetchAllData();
    } catch (err) {
      console.error(`Error in backfill ${action}:`, err);
    } finally {
      setBackfillAction('');
    }
  };

  const handleRunBackup = async () => {
    setBackingUp(true);
    try {
      await apiRequest('post', '/api/system/backups/run');
      await fetchAllData();
    } catch (err) {
      console.error('Error running SQLite backup:', err);
    } finally {
      setBackingUp(false);
    }
  };

  const handleResolveAlert = async (alertId) => {
    try {
      await apiRequest('post', `/api/system/alerts/${alertId}/resolve`);
      await fetchAllData();
    } catch (err) {
      console.error(`Error resolving alert ${alertId}:`, err);
    }
  };

  const handleResolveConflict = async (conflictId, resolvedVal) => {
    try {
      await apiRequest('post', `/api/data-quality/conflicts/${conflictId}/resolve`, {
        resolved_value: resolvedVal,
        notes: 'Operator manual resolution'
      });
      await fetchAllData();
    } catch (err) {
      console.error(`Error resolving conflict ${conflictId}:`, err);
    }
  };

  if (!isOpen) return null;

  const getStatusBadge = (status) => {
    switch (status) {
      case 'VALIDATED':
      case 'VALIDATED (Well Calibrated)':
      case 'HEALTHY':
      case 'STABLE':
      case 'SUCCESS':
      case 'OPERATIONAL':
      case 'GOOD':
      case 'REAL_DATA':
        return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40';
      case 'VALIDATING':
      case 'ACCEPTABLE':
      case 'ACCEPTABLE (Moderate Calibration)':
      case 'WARNING':
      case 'PARTIAL':
      case 'DELAYED':
        return 'bg-amber-500/20 text-amber-400 border-amber-500/40';
      case 'CALIBRATION_WARNING':
      case 'MODEL_DRIFT_WARNING':
      case 'DEGRADED':
      case 'FAILED':
      case 'UNAVAILABLE':
      case 'CRITICAL':
      case 'POOR':
      case 'CONFLICT':
        return 'bg-rose-500/20 text-rose-400 border-rose-500/40';
      default:
        return 'bg-slate-500/20 text-slate-400 border-slate-500/40';
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-slate-950/85 backdrop-blur-md animate-fadeIn">
      <div className={`w-full max-w-5xl rounded-3xl border shadow-2xl overflow-hidden flex flex-col max-h-[92vh] transition-colors ${
        darkMode ? 'bg-slate-900 border-slate-800 text-white' : 'bg-white border-slate-200 text-slate-900'
      }`}>
        
        {/* DASHBOARD HEADER */}
        <div className="p-4 sm:p-6 border-b border-slate-800 bg-gradient-to-r from-cyan-950/70 via-slate-900 to-indigo-950/70 flex items-center justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-wider text-cyan-400 px-2.5 py-0.5 rounded-full bg-cyan-500/10 border border-cyan-500/30">
                <FileCheck className="w-3.5 h-3.5" />
                Real Data Model Validation
              </span>
              <span className="text-[9px] font-bold text-slate-400 uppercase">
                Phase 8 Production Intelligence
              </span>
            </div>
            <h2 className="text-base sm:text-xl font-black text-white tracking-tight flex items-center gap-2">
              <span>Production Data Acquisition & Real-World Validation</span>
              {systemStatus && (
                <span className={`px-2 py-0.5 rounded-full text-[9px] font-extrabold uppercase border ${getStatusBadge(systemStatus.overall_status)}`}>
                  {systemStatus.overall_status}
                </span>
              )}
            </h2>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={fetchAllData}
              className="p-2 rounded-2xl bg-slate-800/60 hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
              title="Refresh intelligence metrics"
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
            { id: 'real_validation', label: 'REAL DATA VALIDATION' },
            { id: 'provenance', label: `PROVENANCE & CONFLICTS (${conflictsData.length})` },
            { id: 'overview', label: 'OVERVIEW' },
            { id: 'jobs', label: 'AUTOMATION JOBS' },
            { id: 'alerts', label: `ALERTS (${alertsData.length})` },
            { id: 'coverage', label: 'DATA COVERAGE' },
            { id: 'readiness', label: 'MODEL READINESS' },
            { id: 'markets', label: 'MARKET LEADERBOARD' },
            { id: 'calibration', label: 'CALIBRATION' },
            { id: 'backfill', label: 'BACKFILL & PROVIDERS' },
            { id: 'live', label: 'LIVE OPERATIONS' }
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
          {loading && !coverageData ? (
            <div className="py-16 text-center space-y-3">
              <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin mx-auto" />
              <p className="text-xs font-semibold text-slate-400">Loading production operations intelligence...</p>
            </div>
          ) : (
            <>
              {/* TAB 1: REAL DATA VALIDATION (Phase 8 Main View) */}
              {activeTab === 'real_validation' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* Verified Outcomes Summary */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    {[
                      { title: 'Completed Matches', val: coverageData?.eligible_completed_matches || 0, icon: Activity, color: 'text-white' },
                      { title: 'Verified Goals', val: coverageData?.goals_coverage?.observed || 0, icon: BarChart2, color: 'text-cyan-400' },
                      { title: 'Verified Corners', val: coverageData?.corners_coverage?.observed || 0, icon: Shield, color: 'text-emerald-400' },
                      { title: 'Verified Cards', val: coverageData?.cards_coverage?.observed || 0, icon: Award, color: 'text-amber-400' }
                    ].map((kpi, idx) => (
                      <div key={idx} className="p-3.5 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-1.5">
                        <div className="flex items-center justify-between text-[10px] text-slate-400 font-bold">
                          <span>{kpi.title}</span>
                          <kpi.icon className="w-3.5 h-3.5 text-cyan-400" />
                        </div>
                        <div className={`text-2xl font-black ${kpi.color} tracking-tight`}>
                          {kpi.val}
                        </div>
                        <div className="text-[10px] text-slate-500">
                          Source: <strong className="text-slate-400">100% Real Observed Data</strong>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Real Multi-Model Comparison Leaderboard */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-black uppercase tracking-wider text-white">
                        Real-World Model Comparison Leaderboard
                      </span>
                      <span className="text-[10px] text-slate-400">Zero Synthetic Benchmarking</span>
                    </div>

                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                            <th className="py-2 px-2">Model Architecture</th>
                            <th className="py-2 px-2 text-center">Data Source</th>
                            <th className="py-2 px-2 text-center">Sample (N)</th>
                            <th className="py-2 px-2 text-center">Brier</th>
                            <th className="py-2 px-2 text-center">Log Loss</th>
                            <th className="py-2 px-2 text-center">Activation Gate</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60">
                          {realLeaderboard.map((m, idx) => (
                            <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                              <td className="py-2.5 px-2 font-bold text-slate-200">{m.model_name}</td>
                              <td className="py-2.5 px-2 text-center">
                                <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(m.data_source)}`}>
                                  {m.data_source}
                                </span>
                              </td>
                              <td className="py-2.5 px-2 text-center text-white font-semibold">{m.sample_size}</td>
                              <td className="py-2.5 px-2 text-center font-black text-cyan-400">
                                {m.brier_score !== null ? m.brier_score.toFixed(3) : '—'}
                              </td>
                              <td className="py-2.5 px-2 text-center text-slate-300">
                                {m.log_loss !== null ? m.log_loss.toFixed(3) : '—'}
                              </td>
                              <td className="py-2.5 px-2 text-center">
                                <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(m.readiness_state)}`}>
                                  {m.readiness_state}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* Market-Level Granular Sample Gates */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-black uppercase tracking-wider text-white">
                        Market-Level Activation Gates ({marketReadiness.length} Markets)
                      </span>
                      <span className="text-[10px] text-slate-400">Independent Sample Accounting</span>
                    </div>

                    <div className="overflow-x-auto max-h-60 custom-scrollbar">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase sticky top-0 bg-slate-950">
                            <th className="py-2 px-2">Betting Market</th>
                            <th className="py-2 px-2 text-center">Sample</th>
                            <th className="py-2 px-2 text-center">Brier</th>
                            <th className="py-2 px-2 text-center">Log Loss</th>
                            <th className="py-2 px-2 text-center">ECE</th>
                            <th className="py-2 px-2 text-center">Readiness Gate</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/60">
                          {marketReadiness.map((m, idx) => (
                            <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                              <td className="py-2 px-2 font-bold text-slate-200 capitalize">{m.market.replace(/_/g, ' ')}</td>
                              <td className="py-2 px-2 text-center text-white font-semibold">{m.sample_size}</td>
                              <td className="py-2 px-2 text-center font-bold text-cyan-400">
                                {m.brier_score !== null ? m.brier_score.toFixed(3) : '—'}
                              </td>
                              <td className="py-2 px-2 text-center text-slate-300">
                                {m.log_loss !== null ? m.log_loss.toFixed(3) : '—'}
                              </td>
                              <td className="py-2 px-2 text-center text-emerald-400">
                                {m.ece !== null ? `${(m.ece * 100).toFixed(1)}%` : '—'}
                              </td>
                              <td className="py-2 px-2 text-center">
                                <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(m.readiness?.readiness_state)}`}>
                                  {m.readiness?.readiness_state}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 2: PROVENANCE & CONFLICTS (Phase 8 Audit View) */}
              {activeTab === 'provenance' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* Backfill Controls */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 flex items-center justify-between">
                    <div>
                      <span className="text-xs font-black uppercase tracking-wider text-white block">
                        Historical Data Backfill Orchestration
                      </span>
                      <span className="text-[11px] text-slate-400">
                        {backfillStatus?.fully_enriched_fixtures || 0} / {backfillStatus?.total_finished_fixtures || 0} fixtures enriched
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleBackfillControl('start')}
                        disabled={backfillAction !== ''}
                        className="px-3 py-1.5 rounded-xl text-xs font-bold bg-cyan-600 hover:bg-cyan-500 text-white flex items-center gap-1 shadow-sm active:scale-95 disabled:opacity-50"
                      >
                        <Play className="w-3.5 h-3.5" />
                        <span>Start Batch</span>
                      </button>
                      <button
                        onClick={() => handleBackfillControl('pause')}
                        disabled={backfillAction !== ''}
                        className="px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-800 hover:bg-slate-700 text-slate-300 flex items-center gap-1 active:scale-95 disabled:opacity-50"
                      >
                        <Pause className="w-3.5 h-3.5" />
                        <span>Pause</span>
                      </button>
                      <button
                        onClick={() => handleBackfillControl('retry')}
                        disabled={backfillAction !== ''}
                        className="px-3 py-1.5 rounded-xl text-xs font-bold bg-amber-600 hover:bg-amber-500 text-white flex items-center gap-1 active:scale-95 disabled:opacity-50"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                        <span>Retry Failed</span>
                      </button>
                    </div>
                  </div>

                  {/* Provider Conflicts */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      Active Provider Data Conflicts ({conflictsData.length})
                    </span>

                    {conflictsData.length > 0 ? (
                      <div className="space-y-2">
                        {conflictsData.map((c, idx) => (
                          <div key={idx} className="p-3 rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-between">
                            <div className="space-y-1">
                              <span className="text-xs font-bold text-slate-200">
                                Fixture #{c.fixture_id} — {c.field_name}
                              </span>
                              <div className="text-[11px] text-slate-400 flex items-center gap-3">
                                <span>{c.primary_provider}: <strong>{c.primary_value}</strong></span>
                                <span>vs</span>
                                <span>{c.conflicting_provider}: <strong>{c.conflicting_value}</strong></span>
                              </div>
                            </div>

                            <div className="flex items-center gap-2">
                              <button
                                onClick={() => handleResolveConflict(c.id, c.primary_value)}
                                className="px-2.5 py-1 rounded-xl text-[10px] font-bold bg-cyan-700 hover:bg-cyan-600 text-white"
                              >
                                Accept {c.primary_provider}
                              </button>
                              <button
                                onClick={() => handleResolveConflict(c.id, c.conflicting_value)}
                                className="px-2.5 py-1 rounded-xl text-[10px] font-bold bg-indigo-700 hover:bg-indigo-600 text-white"
                              >
                                Accept {c.conflicting_provider}
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="py-4 text-center text-slate-400 text-xs">
                        <CheckCircle2 className="w-5 h-5 text-emerald-400 mx-auto mb-1" />
                        Zero provider conflicts detected. All feeds consistent.
                      </div>
                    )}
                  </div>

                  {/* Provenance Audit Log */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      Recent Field-Level Provenance Audit Records ({provenanceData.length})
                    </span>

                    {provenanceData.length > 0 ? (
                      <div className="overflow-x-auto max-h-60 custom-scrollbar">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase sticky top-0 bg-slate-950">
                              <th className="py-2 px-2">Fixture ID</th>
                              <th className="py-2 px-2">Field</th>
                              <th className="py-2 px-2">Value</th>
                              <th className="py-2 px-2">Provider</th>
                              <th className="py-2 px-2">Source</th>
                              <th className="py-2 px-2 text-right">Retrieved At</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {provenanceData.map((p, idx) => (
                              <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                                <td className="py-2 px-2 text-slate-300 font-bold">#{p.fixture_id}</td>
                                <td className="py-2 px-2 text-cyan-400 font-semibold">{p.field_name}</td>
                                <td className="py-2 px-2 text-white font-black">{p.value}</td>
                                <td className="py-2 px-2 text-slate-400 uppercase text-[10px]">{p.provider}</td>
                                <td className="py-2 px-2">
                                  <span className="px-1.5 py-0.5 rounded text-[8px] font-black uppercase bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">
                                    {p.source_type}
                                  </span>
                                </td>
                                <td className="py-2 px-2 text-right text-[10px] text-slate-500">
                                  {p.retrieved_at ? new Date(p.retrieved_at).toLocaleTimeString() : '—'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-3 text-center">No provenance records available yet.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 3: OVERVIEW */}
              {activeTab === 'overview' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    {[
                      { title: 'Goals Coverage', data: coverageData?.goals_coverage, icon: Activity },
                      { title: 'Corners Coverage', data: coverageData?.corners_coverage, icon: BarChart2 },
                      { title: 'Cards Coverage', data: coverageData?.cards_coverage, icon: Shield },
                      { title: 'Referee Coverage', data: coverageData?.referee_coverage, icon: Award }
                    ].map((kpi, idx) => {
                      const covPct = kpi.data?.coverage_ratio ? (kpi.data.coverage_ratio * 100).toFixed(1) : '0.0';
                      return (
                        <div key={idx} className="p-3.5 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-2">
                          <div className="flex items-center justify-between text-[10px] text-slate-400 font-bold">
                            <span>{kpi.title}</span>
                            <kpi.icon className="w-3.5 h-3.5 text-cyan-400" />
                          </div>
                          <div className="text-xl font-black text-white tracking-tight">
                            {covPct}%
                          </div>
                          <div className="text-[10px] text-slate-500 flex justify-between">
                            <span>Observed:</span>
                            <strong className="text-slate-300">{kpi.data?.observed || 0} / {kpi.data?.eligible || 0}</strong>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* TAB 4: AUTOMATION JOBS */}
              {activeTab === 'jobs' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      Scheduled Production Jobs Runner
                    </span>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {jobsData?.available_jobs?.map((jobName, idx) => (
                        <div key={idx} className="p-3 rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-between">
                          <div className="space-y-0.5">
                            <span className="text-xs font-bold text-white uppercase">{jobName.replace(/_/g, ' ')}</span>
                            <span className="text-[10px] text-slate-500 block">Scheduled automated background task</span>
                          </div>
                          <button
                            onClick={() => handleRunJob(jobName)}
                            disabled={runningJob === jobName}
                            className="px-3 py-1 rounded-xl text-xs font-bold bg-cyan-600 hover:bg-cyan-500 text-white flex items-center gap-1 shadow-sm active:scale-95 disabled:opacity-50"
                          >
                            <Play className={`w-3 h-3 ${runningJob === jobName ? 'animate-spin' : ''}`} />
                            <span>{runningJob === jobName ? 'Running...' : 'Run'}</span>
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Recent Job Executions */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      Recent Execution History Logs
                    </span>

                    {jobsData?.recent_executions?.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                              <th className="py-2 px-2">Job Name</th>
                              <th className="py-2 px-2">Status</th>
                              <th className="py-2 px-2 text-center">Duration</th>
                              <th className="py-2 px-2 text-center">Processed</th>
                              <th className="py-2 px-2">Timestamp</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {jobsData.recent_executions.map((rec, idx) => (
                              <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                                <td className="py-2 px-2 font-bold text-slate-200 uppercase">{rec.job_name.replace(/_/g, ' ')}</td>
                                <td className="py-2 px-2">
                                  <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(rec.status)}`}>
                                    {rec.status}
                                  </span>
                                </td>
                                <td className="py-2 px-2 text-center text-cyan-400 font-bold">{rec.duration_ms} ms</td>
                                <td className="py-2 px-2 text-center text-white">{rec.records_processed}</td>
                                <td className="py-2 px-2 text-[10px] text-slate-500">
                                  {rec.started_at ? new Date(rec.started_at).toLocaleTimeString() : '—'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-4 text-center">No job execution history recorded yet.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 5: ALERTS & BACKUPS */}
              {activeTab === 'alerts' && (
                <div className="space-y-4 animate-fadeIn">
                  {/* Active Alerts */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      Active System Operational Alerts ({alertsData.length})
                    </span>

                    {alertsData.length > 0 ? (
                      <div className="space-y-2">
                        {alertsData.map((alt, idx) => (
                          <div key={idx} className="p-3 rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-between">
                            <div className="space-y-1">
                              <div className="flex items-center gap-2">
                                <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(alt.severity)}`}>
                                  {alt.severity}
                                </span>
                                <span className="text-[10px] font-black text-slate-300 uppercase">{alt.category}</span>
                              </div>
                              <p className="text-xs text-slate-300 font-semibold">{alt.message}</p>
                            </div>
                            <button
                              onClick={() => handleResolveAlert(alt.alert_id)}
                              className="px-2.5 py-1 rounded-xl text-[11px] font-bold bg-slate-800 hover:bg-slate-700 text-slate-300 active:scale-95"
                            >
                              Resolve
                            </button>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="py-6 text-center text-slate-400 space-y-1">
                        <CheckCircle2 className="w-6 h-6 text-emerald-400 mx-auto" />
                        <p className="text-xs font-bold text-slate-300">All systems operating normally</p>
                        <p className="text-[11px] text-slate-500">No active alerts or incident warnings.</p>
                      </div>
                    )}
                  </div>

                  {/* Backups Management */}
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-black uppercase tracking-wider text-white block">
                        SQLite Online Backups ({backupsData.length})
                      </span>
                      <button
                        onClick={handleRunBackup}
                        disabled={backingUp}
                        className="px-3 py-1.5 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white flex items-center gap-1.5 shadow-md active:scale-95 disabled:opacity-50"
                      >
                        <HardDrive className={`w-3.5 h-3.5 ${backingUp ? 'animate-spin' : ''}`} />
                        <span>{backingUp ? 'Backing Up...' : 'Create Live Backup'}</span>
                      </button>
                    </div>

                    {backupsData.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                              <th className="py-2 px-2">Archive File</th>
                              <th className="py-2 px-2 text-center">Size</th>
                              <th className="py-2 px-2 text-right">Created At</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {backupsData.map((bk, idx) => (
                              <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                                <td className="py-2 px-2 font-bold text-slate-200">{bk.filename}</td>
                                <td className="py-2 px-2 text-center text-cyan-400 font-bold">{(bk.size_bytes / 1024).toFixed(0)} KB</td>
                                <td className="py-2 px-2 text-right text-[10px] text-slate-500">
                                  {new Date(bk.created_at).toLocaleString()}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-3 text-center">No backup archives created yet.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 6: DATA COVERAGE */}
              {activeTab === 'coverage' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      Competition Data Quality & Coverage Registry ({competitionsCoverage.length})
                    </span>

                    {competitionsCoverage.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                              <th className="py-2 px-2">Competition</th>
                              <th className="py-2 px-2 text-center">Matches</th>
                              <th className="py-2 px-2 text-center">Goals</th>
                              <th className="py-2 px-2 text-center">Corners</th>
                              <th className="py-2 px-2 text-center">Cards</th>
                              <th className="py-2 px-2 text-center">Referees</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-800/60">
                            {competitionsCoverage.map((comp, idx) => (
                              <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                                <td className="py-2.5 px-2 font-bold text-slate-200">{comp.competition}</td>
                                <td className="py-2.5 px-2 text-center font-semibold text-white">{comp.eligible_matches}</td>
                                <td className="py-2.5 px-2 text-center text-cyan-400 font-bold">{(comp.goals_coverage * 100).toFixed(0)}%</td>
                                <td className="py-2.5 px-2 text-center text-emerald-400 font-bold">{(comp.corners_coverage * 100).toFixed(0)}%</td>
                                <td className="py-2.5 px-2 text-center text-amber-400 font-bold">{(comp.cards_coverage * 100).toFixed(0)}%</td>
                                <td className="py-2.5 px-2 text-center text-indigo-400 font-bold">{(comp.referee_coverage * 100).toFixed(0)}%</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 py-6 text-center">No competition coverage data recorded.</p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 7: MODEL READINESS */}
              {activeTab === 'readiness' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      Production Model Readiness & Activation Gates
                    </span>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {readinessData?.models?.map((m, idx) => (
                        <div key={idx} className="p-3.5 rounded-2xl bg-slate-900 border border-slate-800 space-y-2">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-black text-white">{m.model_name}</span>
                            <span className={`px-2 py-0.5 rounded text-[9px] font-black uppercase border ${getStatusBadge(m.readiness?.readiness_state)}`}>
                              {m.readiness?.readiness_state}
                            </span>
                          </div>

                          <div className="grid grid-cols-3 gap-2 text-[11px] pt-1">
                            <div>
                              <span className="text-slate-500 text-[10px] block">Sample</span>
                              <strong className="text-white">{m.sample_size}</strong>
                            </div>
                            <div>
                              <span className="text-slate-500 text-[10px] block">Brier</span>
                              <strong className="text-cyan-400">{m.brier_score !== null ? m.brier_score.toFixed(3) : '—'}</strong>
                            </div>
                            <div>
                              <span className="text-slate-500 text-[10px] block">ECE</span>
                              <strong className="text-emerald-400">{m.ece !== null ? `${(m.ece * 100).toFixed(1)}%` : '—'}</strong>
                            </div>
                          </div>

                          <div className="text-[10px] text-slate-400 pt-1 flex justify-between border-t border-slate-800/80">
                            <span>Ensemble Activation:</span>
                            <strong className={m.readiness?.can_activate_ensemble ? 'text-emerald-400' : 'text-slate-500'}>
                              {m.readiness?.can_activate_ensemble ? 'ELIGIBLE' : 'GATED (Sample < 100)'}
                            </strong>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 8: MARKET LEADERBOARD */}
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

              {/* TAB 9: CALIBRATION */}
              {activeTab === 'calibration' && (
                <div className="space-y-4 animate-fadeIn">
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

                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      10-Decile Probability Reliability Table
                    </span>

                    {calibrationData?.buckets?.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                          <thead>
                            <tr className="border-b border-slate-800 text-[10px] text-slate-400 font-bold uppercase">
                              <th className="py-2 px-2">Range</th>
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

              {/* TAB 10: BACKFILL & PROVIDERS */}
              {activeTab === 'backfill' && (
                <div className="space-y-4 animate-fadeIn">
                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      Historical Data Backfill & Enrichment
                    </span>
                    <span className="text-[11px] text-slate-400 block">
                      {backfillStatus?.fully_enriched_fixtures || 0} / {backfillStatus?.total_finished_fixtures || 0} fixtures fully enriched
                    </span>
                  </div>

                  <div className="p-4 rounded-2xl bg-slate-950/60 border border-slate-800 space-y-3">
                    <span className="text-xs font-black uppercase tracking-wider text-white block">
                      External Data Providers Health
                    </span>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      {providersData.map((pr, idx) => (
                        <div key={idx} className="p-3 rounded-xl bg-slate-900 border border-slate-800 space-y-1.5">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-black uppercase text-white">{pr.provider}</span>
                            <span className={`px-1.5 py-0.5 rounded text-[8px] font-black uppercase border ${getStatusBadge(pr.status)}`}>
                              {pr.status}
                            </span>
                          </div>
                          <div className="text-[10px] text-slate-400 flex justify-between">
                            <span>Success Rate:</span>
                            <strong className="text-emerald-400">
                              {pr.request_count > 0 ? `${((pr.success_count / pr.request_count) * 100).toFixed(0)}%` : '100%'}
                            </strong>
                          </div>
                          <div className="text-[10px] text-slate-400 flex justify-between">
                            <span>Latency:</span>
                            <strong className="text-slate-300">{pr.average_latency_ms} ms</strong>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* TAB 11: LIVE OPERATIONS */}
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
            </>
          )}
        </div>
      </div>
    </div>
  );
}
