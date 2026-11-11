import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Calculator,
  CheckCircle2,
  Coins,
  Receipt,
  RefreshCw,
  Trash2,
  TrendingUp,
  X,
} from 'lucide-react';

const STATUS_BADGE = {
  WON: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
  LOST: 'bg-rose-500/20 text-rose-300 border-rose-500/40',
  VOID: 'bg-slate-700/60 text-slate-300 border-slate-600',
  PENDING: 'bg-amber-500/15 text-amber-300 border-amber-500/35',
};

const QUICK_MARKETS = [
  'Over 1.5 Goals',
  'Over 2.5 Goals',
  'BTTS Yes',
  'Home Win',
  'Draw',
  'Away Win',
  '1H Over 0.5 Goals',
  'Over 8.5 Corners',
  'Over 3.5 Cards',
  'At Least 1 More Goal',
];

export default function BetSlipTrackerModal({
  isOpen,
  onClose,
  draftLegs = [],
  onUpdateDraftLeg,
  onRemoveDraftLeg,
  onClearDraftLegs,
  apiRequest,
  darkMode = true,
  onNotifyBanner,
}) {
  const [tab, setTab] = useState(draftLegs.length > 0 ? 'builder' : 'ledger');
  const [title, setTitle] = useState('');
  const [stake, setStake] = useState('10');
  const [submitting, setSubmitting] = useState(false);
  const [loadingLedger, setLoadingLedger] = useState(false);
  const [settling, setSettling] = useState(false);
  const [slips, setSlips] = useState([]);
  const [summary, setSummary] = useState(null);

  useEffect(() => {
    if (isOpen && draftLegs.length > 0) {
      setTab('builder');
    }
  }, [isOpen, draftLegs.length]);

  const fetchLedger = useCallback(async () => {
    setLoadingLedger(true);
    try {
      const res = await apiRequest('get', '/api/betslips');
      if (res.data?.status === 'ok') {
        setSlips(Array.isArray(res.data.slips) ? res.data.slips : []);
        setSummary(res.data.summary || null);
      }
    } catch {
      // Handled silently; empty state remains visible
    } finally {
      setLoadingLedger(false);
    }
  }, [apiRequest]);

  useEffect(() => {
    if (isOpen) {
      fetchLedger();
    }
  }, [isOpen, fetchLedger]);

  const slipMetrics = useMemo(() => {
    let combinedOdds = 1.0;
    let jointProb = 1.0;
    const enrichedLegs = draftLegs.map((leg) => {
      const p = Math.max(0.01, Math.min(0.99, Number(leg.model_prob || 0.7)));
      const fairOdds = Number((1 / p).toFixed(2));
      const userOddsNum = Number(leg.bookmaker_odds);
      const effOdds = userOddsNum > 1.0 ? userOddsNum : fairOdds;
      const evPct = userOddsNum > 1.0 ? Number(((p * userOddsNum - 1) * 100).toFixed(2)) : 0.0;
      const kellyRaw = userOddsNum > 1.0 ? (p * userOddsNum - 1) / Math.max(0.01, userOddsNum - 1) : 0;
      const kellyQuarter = Math.min(0.1, Math.max(0, kellyRaw * 0.25));
      combinedOdds *= effOdds;
      jointProb *= p;
      return {
        ...leg,
        fairOdds,
        effOdds,
        evPct,
        kellyQuarterPct: Number((kellyQuarter * 100).toFixed(1)),
      };
    });
    const effCombined = Number(combinedOdds.toFixed(2));
    const effEv = Number(((jointProb * effCombined - 1) * 100).toFixed(2));
    const stakeNum = Math.max(1, Number(stake) || 10);
    return {
      enrichedLegs,
      combinedOdds: effCombined,
      jointProbPct: Number((jointProb * 100).toFixed(1)),
      combinedEvPct: effEv,
      potentialReturn: Number((stakeNum * effCombined).toFixed(2)),
    };
  }, [draftLegs, stake]);

  if (!isOpen) return null;

  const surface = darkMode
    ? 'bg-slate-900 border-slate-800 text-white'
    : 'bg-white border-slate-200 text-slate-900';
  const cardCls = darkMode ? 'bg-slate-950/70 border-slate-800' : 'bg-slate-50 border-slate-200';

  const handleTrackSlip = async () => {
    if (draftLegs.length === 0) return;
    setSubmitting(true);
    try {
      const payload = {
        title:
          title.trim() ||
          (draftLegs.length === 1
            ? `${draftLegs[0].match_label} (${draftLegs[0].market})`
            : `${draftLegs.length}-Leg Multi Slip`),
        source: draftLegs.some((l) => l.baseline_goals !== null && l.baseline_goals !== undefined)
          ? 'live_radar'
          : 'manual',
        stake: Math.max(1, Number(stake) || 10),
        legs: draftLegs.map((l) => ({
          fixture_id: Number(l.fixture_id),
          match_label: l.match_label,
          league_name: l.league_name || '',
          match_date: l.match_date || null,
          market: l.market,
          model_prob: Math.max(0.05, Math.min(0.95, Number(l.model_prob || 0.7))),
          bookmaker_odds:
            l.bookmaker_odds && Number(l.bookmaker_odds) > 1.0 ? Number(l.bookmaker_odds) : null,
          baseline_goals:
            l.baseline_goals !== null && l.baseline_goals !== undefined
              ? Number(l.baseline_goals)
              : null,
        })),
      };
      const res = await apiRequest('post', '/api/betslips', payload);
      if (res.data?.status === 'ok') {
        onClearDraftLegs();
        setTitle('');
        await fetchLedger();
        setTab('ledger');
        if (onNotifyBanner) {
          onNotifyBanner({ type: 'success', message: 'Paper slip saved & tracked cleanly!' });
        }
      }
    } catch {
      if (onNotifyBanner) {
        onNotifyBanner({ type: 'error', message: 'Unable to save paper bet slip.' });
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleSettle = async () => {
    setSettling(true);
    try {
      const res = await apiRequest('post', '/api/betslips/settle');
      if (res.data?.status === 'ok') {
        setSlips(Array.isArray(res.data.slips) ? res.data.slips : []);
        setSummary(res.data.summary || null);
      }
    } catch {
      // Ignore quietly
    } finally {
      setSettling(false);
    }
  };

  const handleDelete = async (slipId) => {
    try {
      await apiRequest('delete', `/api/betslips/${slipId}`);
      await fetchLedger();
    } catch {
      // Ignore quietly
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-slate-950/80 backdrop-blur-md">
      <div className={`w-full max-w-4xl rounded-3xl border shadow-2xl overflow-hidden flex flex-col max-h-[90vh] ${surface}`}>
        {/* Header */}
        <div className="p-4 sm:p-6 border-b border-slate-800 flex items-center justify-between bg-gradient-to-r from-amber-950/30 via-slate-900 to-emerald-950/30">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-2xl bg-amber-500/20 text-amber-400 border border-amber-500/40">
              <Calculator className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg sm:text-xl font-black tracking-tight">+EV Edge Calculator &amp; Paper Bet Ledger</h2>
              <p className="text-xs text-slate-400">
                Compare model fair odds against your bookmaker odds, size with Quarter-Kelly, and track auto-settled ROI
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Mode Tabs */}
        <div className="px-4 py-3 border-b border-slate-800 flex items-center gap-2 bg-slate-950/40">
          <button
            type="button"
            onClick={() => setTab('builder')}
            className={`px-3.5 py-2 rounded-xl text-xs font-extrabold flex items-center gap-1.5 ${
              tab === 'builder'
                ? 'bg-amber-600 text-white shadow'
                : 'bg-slate-800/70 text-slate-400 hover:text-white'
            }`}
          >
            <Receipt className="w-3.5 h-3.5" />
            Slip Builder ({draftLegs.length})
          </button>
          <button
            type="button"
            onClick={() => setTab('ledger')}
            className={`px-3.5 py-2 rounded-xl text-xs font-extrabold flex items-center gap-1.5 ${
              tab === 'ledger'
                ? 'bg-emerald-600 text-white shadow'
                : 'bg-slate-800/70 text-slate-400 hover:text-white'
            }`}
          >
            <TrendingUp className="w-3.5 h-3.5" />
            Paper Ledger &amp; ROI ({slips.length})
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-4">
          {tab === 'builder' ? (
            draftLegs.length === 0 ? (
              <div className="py-12 text-center space-y-2">
                <Coins className="w-8 h-8 mx-auto text-amber-400/70" />
                <p className="text-sm font-bold">Your active bet slip builder is empty</p>
                <p className="text-xs text-slate-400 max-w-md mx-auto">
                  Click <strong>+ Slip</strong> on any match row, Live Goal Radar card, or Smart Accumulator to evaluate +EV edge and track its settled outcome.
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="text-[11px] font-bold text-slate-400 block mb-1">Slip Label</label>
                    <input
                      type="text"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder={`${draftLegs.length}-Leg Edge Ticket`}
                      className={`w-full px-3 py-2 rounded-xl border text-xs font-semibold ${
                        darkMode ? 'bg-slate-950 border-slate-800' : 'bg-white border-slate-300'
                      }`}
                    />
                  </div>
                  <div>
                    <label className="text-[11px] font-bold text-slate-400 block mb-1">Simulated Paper Stake ($)</label>
                    <input
                      type="number"
                      min={1}
                      max={100000}
                      value={stake}
                      onChange={(e) => setStake(e.target.value)}
                      className={`w-full px-3 py-2 rounded-xl border text-xs font-bold ${
                        darkMode ? 'bg-slate-950 border-slate-800' : 'bg-white border-slate-300'
                      }`}
                    />
                  </div>
                </div>

                {/* Draft Legs */}
                <div className="space-y-2.5">
                  {slipMetrics.enrichedLegs.map((leg, idx) => (
                    <div key={`${leg.fixture_id}-${idx}`} className={`p-3.5 rounded-2xl border space-y-2.5 ${cardCls}`}>
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="text-xs font-extrabold">{leg.match_label}</div>
                          <div className="text-[11px] text-slate-400">{leg.league_name}</div>
                        </div>
                        <button
                          type="button"
                          onClick={() => onRemoveDraftLeg(idx)}
                          className="p-1 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-rose-500/10"
                          title="Remove leg"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>

                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 items-end text-xs">
                        <div>
                          <label className="text-[10px] font-bold text-slate-400 block mb-1">Market</label>
                          <select
                            value={leg.market}
                            onChange={(e) => onUpdateDraftLeg(idx, { market: e.target.value })}
                            className={`w-full px-2 py-1.5 rounded-lg border text-xs font-bold ${
                              darkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-300'
                            }`}
                          >
                            {!QUICK_MARKETS.includes(leg.market) && (
                              <option value={leg.market}>{leg.market}</option>
                            )}
                            {QUICK_MARKETS.map((m) => (
                              <option key={m} value={m}>
                                {m}
                              </option>
                            ))}
                          </select>
                        </div>

                        <div>
                          <div className="text-[10px] font-bold text-slate-400 mb-1">Model Fair Odds</div>
                          <div className="px-2.5 py-1.5 rounded-lg bg-slate-900/60 border border-slate-800 font-mono font-bold text-cyan-300">
                            {leg.fairOdds}x ({Math.round(leg.model_prob * 100)}%)
                          </div>
                        </div>

                        <div>
                          <label className="text-[10px] font-bold text-slate-400 block mb-1">Your Book Odds</label>
                          <input
                            type="number"
                            step="0.01"
                            min="1.01"
                            placeholder={`Fair: ${leg.fairOdds}`}
                            value={leg.bookmaker_odds ?? ''}
                            onChange={(e) => onUpdateDraftLeg(idx, { bookmaker_odds: e.target.value })}
                            className={`w-full px-2.5 py-1.5 rounded-lg border font-mono font-bold text-xs ${
                              darkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-300'
                            }`}
                          />
                        </div>

                        <div className="px-2.5 py-1.5 rounded-lg bg-slate-900/60 border border-slate-800 text-right">
                          <div className="text-[10px] font-bold text-slate-400">+EV Edge / ¼ Kelly</div>
                          <div
                            className={`font-mono font-black ${
                              leg.evPct > 0
                                ? 'text-emerald-400'
                                : leg.evPct < 0
                                ? 'text-rose-400'
                                : 'text-slate-300'
                            }`}
                          >
                            {leg.evPct > 0 ? `+${leg.evPct}%` : `${leg.evPct}%`} · {leg.kellyQuarterPct}%
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Combined Totals */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4 rounded-2xl bg-slate-950 border border-slate-800">
                  <div>
                    <div className="text-[10px] font-bold text-slate-400 uppercase">Combined Odds</div>
                    <div className="text-lg font-black text-cyan-400">{slipMetrics.combinedOdds}x</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-bold text-slate-400 uppercase">Model Win Prob</div>
                    <div className="text-lg font-black text-white">{slipMetrics.jointProbPct}%</div>
                  </div>
                  <div>
                    <div className="text-[10px] font-bold text-slate-400 uppercase">Expected Value</div>
                    <div
                      className={`text-lg font-black ${
                        slipMetrics.combinedEvPct > 0
                          ? 'text-emerald-400'
                          : slipMetrics.combinedEvPct < 0
                          ? 'text-rose-400'
                          : 'text-slate-300'
                      }`}
                    >
                      {slipMetrics.combinedEvPct > 0
                        ? `+${slipMetrics.combinedEvPct}%`
                        : `${slipMetrics.combinedEvPct}%`}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] font-bold text-slate-400 uppercase">Potential Return</div>
                    <div className="text-lg font-black text-amber-400">${slipMetrics.potentialReturn}</div>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-3">
                  <button
                    type="button"
                    onClick={onClearDraftLegs}
                    className="px-3.5 py-2 rounded-xl text-xs font-bold text-rose-400 border border-rose-500/30 hover:bg-rose-500/10"
                  >
                    Clear Slip
                  </button>
                  <button
                    type="button"
                    onClick={handleTrackSlip}
                    disabled={submitting}
                    className="px-5 py-2.5 rounded-xl text-xs font-extrabold bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg disabled:opacity-50"
                  >
                    {submitting ? 'Saving Slip...' : 'Track Paper Bet Slip'}
                  </button>
                </div>
              </div>
            )
          ) : (
            <div className="space-y-4">
              {/* Summary KPIs */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-xs text-slate-400 font-semibold">
                  Slips auto-grade to WON / LOST / VOID when matches finish or are postponed.
                </div>
                <button
                  type="button"
                  onClick={handleSettle}
                  disabled={settling || loadingLedger}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold bg-slate-800 hover:bg-slate-700 text-emerald-300 border border-slate-700"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${settling || loadingLedger ? 'animate-spin' : ''}`} />
                  Auto-Settle Results
                </button>
              </div>

              {summary && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div className={`p-3 rounded-xl border ${cardCls}`}>
                    <div className="text-[10px] font-bold uppercase text-slate-400">Win Rate</div>
                    <div className="text-xl font-black text-emerald-400">{summary.win_rate_pct ?? 0}%</div>
                    <div className="text-[10px] text-slate-500">
                      {summary.won_slips ?? 0}W / {summary.lost_slips ?? 0}L / {summary.pending_slips ?? 0}P
                    </div>
                  </div>
                  <div className={`p-3 rounded-xl border ${cardCls}`}>
                    <div className="text-[10px] font-bold uppercase text-slate-400">Net Profit/Loss</div>
                    <div
                      className={`text-xl font-black ${
                        summary.net_profit >= 0 ? 'text-emerald-400' : 'text-rose-400'
                      }`}
                    >
                      {summary.net_profit >= 0 ? `+$${summary.net_profit}` : `-$${Math.abs(summary.net_profit)}`}
                    </div>
                    <div className="text-[10px] text-slate-500">Staked: ${summary.total_staked ?? 0}</div>
                  </div>
                  <div className={`p-3 rounded-xl border ${cardCls}`}>
                    <div className="text-[10px] font-bold uppercase text-slate-400">ROI</div>
                    <div
                      className={`text-xl font-black ${
                        summary.roi_pct >= 0 ? 'text-cyan-400' : 'text-rose-400'
                      }`}
                    >
                      {summary.roi_pct >= 0 ? `+${summary.roi_pct}%` : `${summary.roi_pct}%`}
                    </div>
                    <div className="text-[10px] text-slate-500">Settled Slips: {summary.settled_slips ?? 0}</div>
                  </div>
                  <div className={`p-3 rounded-xl border ${cardCls}`}>
                    <div className="text-[10px] font-bold uppercase text-slate-400">Max Drawdown</div>
                    <div className="text-xl font-black text-amber-400">-${summary.max_drawdown}</div>
                    <div className="text-[10px] text-slate-500">Total Slips: {summary.total_slips}</div>
                  </div>
                </div>
              )}

              {/* Slips List */}
              {slips.length === 0 ? (
                <div className="py-10 text-center text-xs text-slate-400">
                  No tracked paper slips recorded yet.
                </div>
              ) : (
                <div className="space-y-3">
                  {slips.map((s) => (
                    <div key={s.id} className={`p-3.5 rounded-2xl border space-y-2.5 ${cardCls}`}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div>
                          <div className="flex items-center gap-2">
                            <span
                              className={`px-2 py-0.5 rounded-full text-[10px] font-black border ${
                                STATUS_BADGE[s.status] || STATUS_BADGE.PENDING
                              }`}
                            >
                              {s.status}
                            </span>
                            <span className="text-xs font-extrabold">{s.title}</span>
                          </div>
                          <div className="text-[11px] text-slate-400 mt-0.5">
                            Stake: ${s.stake} · Odds: {s.combined_odds}x · Expected EV:{' '}
                            {s.expected_value_pct >= 0 ? `+${s.expected_value_pct}%` : `${s.expected_value_pct}%`}
                          </div>
                        </div>

                        <div className="flex items-center gap-3">
                          <div className="text-right">
                            <div
                              className={`text-sm font-black ${
                                s.status === 'WON'
                                  ? 'text-emerald-400'
                                  : s.status === 'LOST'
                                  ? 'text-rose-400'
                                  : 'text-slate-300'
                              }`}
                            >
                              {s.status === 'WON'
                                ? `+$${s.profit_loss}`
                                : s.status === 'LOST'
                                ? `-$${Math.abs(s.profit_loss)}`
                                : 'Pending'}
                            </div>
                            <div className="text-[10px] text-slate-500">Payout: ${s.payout}</div>
                          </div>
                          <button
                            type="button"
                            onClick={() => handleDelete(s.id)}
                            className="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-500/10"
                            title="Delete slip"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 pt-2 border-t border-slate-800/70">
                        {(s.legs || []).map((lg, idx) => (
                          <div
                            key={`${s.id}-lg-${idx}`}
                            className="flex items-center justify-between text-[11px] px-2.5 py-1.5 rounded-lg bg-slate-900/60 border border-slate-800/80"
                          >
                            <span className="truncate pr-2">
                              {lg.match_label} · <strong className="text-cyan-300">{lg.market}</strong>
                            </span>
                            <span className="inline-flex items-center gap-1 font-mono font-bold shrink-0">
                              {lg.final_score ? `(${lg.final_score})` : ''}
                              <span
                                className={
                                  lg.result === 'WON'
                                    ? 'text-emerald-400'
                                    : lg.result === 'LOST'
                                    ? 'text-rose-400'
                                    : 'text-amber-300'
                                }
                              >
                                {lg.result === 'WON' && <CheckCircle2 className="w-3 h-3 inline mr-0.5" />}
                                {lg.result || 'PENDING'}
                              </span>
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
