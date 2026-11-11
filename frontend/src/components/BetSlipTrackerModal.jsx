import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Calculator,
  Check,
  CheckCircle2,
  Coins,
  Copy,
  ExternalLink,
  Receipt,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Tag,
  Ticket,
  Trash2,
  TrendingUp,
  X,
} from 'lucide-react';
import {
  BOOKMAKER_META,
  formatBookmakerSlipText,
  getBookmakerSearchUrl,
  translateMarketForBookmaker,
} from '../utils/bookmakerMarkets';

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
  'Double Chance 1X',
  'Double Chance X2',
  'Double Chance 12',
  'Home Win',
  'Draw',
  'Away Win',
  '1H Over 0.5 Goals',
  'Over 8.5 Corners',
  'Over 3.5 Cards',
  'At Least 1 More Goal',
];

const PRESET_TABS = [
  { id: 'safe_2_odds', label: '2-Odds Safe', badge: '~2.0x' },
  { id: 'balanced_3_odds', label: '3-Odds Multi', badge: '~3.0x' },
  { id: 'goals_corners_5_odds', label: '5-Odds Combo', badge: '~5.0x' },
  { id: 'high_yield_10_odds', label: '10-Odds Yield', badge: '~10.0x' },
];

export default function BetSlipTrackerModal({
  isOpen,
  onClose,
  draftLegs = [],
  draftBookmaker = 'general',
  onChangeDraftBookmaker,
  onLoadBookmakerLegs,
  onUpdateDraftLeg,
  onRemoveDraftLeg,
  onClearDraftLegs,
  selectedPickDay = 'ALL_DAYS',
  apiRequest,
  darkMode = true,
  onNotifyBanner,
}) {
  const [tab, setTab] = useState(draftLegs.length > 0 ? 'builder' : 'bookmakers');
  const [activeBookmaker, setActiveBookmaker] = useState('sportybet');
  const [activePreset, setActivePreset] = useState('safe_2_odds');
  const [dayScope, setDayScope] = useState('selected'); // 'selected' | 'all'
  const [bookmakerData, setBookmakerData] = useState(null);
  const [loadingBookmakerSlips, setLoadingBookmakerSlips] = useState(false);

  const [title, setTitle] = useState('');
  const [stake, setStake] = useState('10');
  const [bookingCode, setBookingCode] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const [loadingLedger, setLoadingLedger] = useState(false);
  const [settling, setSettling] = useState(false);
  const [slips, setSlips] = useState([]);
  const [summary, setSummary] = useState(null);

  const [editingSlipId, setEditingSlipId] = useState(null);
  const [editingBookmaker, setEditingBookmaker] = useState('sportybet');
  const [editingBookingCode, setEditingBookingCode] = useState('');
  const [savingCode, setSavingCode] = useState(false);

  const [copiedKey, setCopiedKey] = useState(null);
  const wasOpenRef = useRef(false);
  const bookmakerAbortRef = useRef(null);

  useEffect(() => {
    if (isOpen && !wasOpenRef.current) {
      setTab(draftLegs.length > 0 ? 'builder' : 'bookmakers');
    }
    wasOpenRef.current = isOpen;
  }, [isOpen, draftLegs.length]);

  const copyTextWithFeedback = useCallback(async (text, key) => {
    if (!text) return;
    try {
      await navigator.clipboard?.writeText(text);
      setCopiedKey(key);
      setTimeout(() => {
        setCopiedKey((prev) => (prev === key ? null : prev));
      }, 2200);
    } catch {
      if (onNotifyBanner) {
        onNotifyBanner({ type: 'error', message: 'Clipboard access blocked by browser.' });
      }
    }
  }, [onNotifyBanner]);

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

  const fetchBookmakerSlips = useCallback(async () => {
    if (bookmakerAbortRef.current) {
      bookmakerAbortRef.current.abort();
    }
    const controller = new AbortController();
    bookmakerAbortRef.current = controller;
    setLoadingBookmakerSlips(true);
    try {
      const effectiveDay =
        dayScope === 'selected' && selectedPickDay && selectedPickDay !== 'ALL_DAYS'
          ? selectedPickDay
          : 'ALL_DAYS';
      const res = await apiRequest(
        'get',
        `/api/bookmakers/slips?bookmaker=${encodeURIComponent(activeBookmaker)}&day=${encodeURIComponent(effectiveDay)}`,
        null,
        { signal: controller.signal }
      );
      if (controller.signal.aborted) return;
      if (res.data?.status === 'ok') {
        setBookmakerData(res.data);
      }
    } catch (err) {
      if (err?.name === 'CanceledError' || err?.name === 'AbortError') return;
    } finally {
      if (!controller.signal.aborted) {
        setLoadingBookmakerSlips(false);
      }
    }
  }, [apiRequest, activeBookmaker, dayScope, selectedPickDay]);

  useEffect(() => {
    if (isOpen) {
      fetchLedger();
      fetchBookmakerSlips();
    } else if (bookmakerAbortRef.current) {
      bookmakerAbortRef.current.abort();
    }
    return () => {
      if (bookmakerAbortRef.current) {
        bookmakerAbortRef.current.abort();
      }
    };
  }, [isOpen, fetchLedger, fetchBookmakerSlips]);

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
        translation:
          draftBookmaker === 'sportybet' || draftBookmaker === 'bet9ja'
            ? translateMarketForBookmaker(leg.market, draftBookmaker)
            : null,
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
  }, [draftLegs, stake, draftBookmaker]);

  if (!isOpen) return null;

  const surface = darkMode
    ? 'bg-slate-900 border-slate-800 text-white'
    : 'bg-white border-slate-200 text-slate-900';
  const cardCls = darkMode ? 'bg-slate-950/70 border-slate-800' : 'bg-slate-50 border-slate-200';
  const inputCls = darkMode ? 'bg-slate-950 border-slate-800 text-white' : 'bg-white border-slate-300 text-slate-900';

  const currentPresetSlip = bookmakerData?.slips?.[activePreset] || null;
  const activeBookmakerMeta = BOOKMAKER_META[activeBookmaker] || BOOKMAKER_META.sportybet;

  const handleLoadPresetToBuilder = () => {
    if (!currentPresetSlip?.legs?.length) return;
    const mappedLegs = currentPresetSlip.legs.map((lg) => ({
      fixture_id: Number(lg.fixture_id),
      match_label: lg.match || `${lg.home_team || 'Home'} vs ${lg.away_team || 'Away'}`,
      league_name: lg.league || '',
      match_date: lg.match_date || null,
      market: lg.market || 'Over 1.5 Goals',
      model_prob: Math.max(0.05, Math.min(0.95, Number(lg.model_prob || 0.7))),
      bookmaker_odds: '',
      baseline_goals: null,
    }));
    if (onLoadBookmakerLegs) {
      onLoadBookmakerLegs(mappedLegs, activeBookmaker);
    }
    setTitle(`${activeBookmakerMeta.shortName} ${currentPresetSlip.label}`);
    setTab('builder');
  };

  const handleTrackSlip = async () => {
    if (draftLegs.length === 0) return;
    setSubmitting(true);
    try {
      const cleanedCode = bookingCode.trim().toUpperCase().slice(0, 32);
      const bmName = BOOKMAKER_META[draftBookmaker]?.shortName || 'Multi';
      const payload = {
        title:
          title.trim() ||
          (draftLegs.length === 1
            ? `${draftLegs[0].match_label} (${draftLegs[0].market})`
            : `${draftBookmaker !== 'general' ? `${bmName} ` : ''}${draftLegs.length}-Leg Slip`),
        source: draftLegs.some((l) => l.baseline_goals !== null && l.baseline_goals !== undefined)
          ? 'live_radar'
          : draftBookmaker !== 'general'
          ? `bookmaker_${draftBookmaker}`
          : 'manual',
        bookmaker: draftBookmaker || 'general',
        booking_code: cleanedCode || null,
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
        setBookingCode('');
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

  const handleSaveSlipBookingCode = async (slipId) => {
    setSavingCode(true);
    try {
      const res = await apiRequest('patch', `/api/betslips/${slipId}/booking-code`, {
        bookmaker: editingBookmaker,
        booking_code: editingBookingCode.trim().toUpperCase(),
      });
      if (res.data?.status === 'ok') {
        setEditingSlipId(null);
        await fetchLedger();
        if (onNotifyBanner) {
          onNotifyBanner({ type: 'success', message: 'Booking code attached to ledger slip!' });
        }
      }
    } catch {
      if (onNotifyBanner) {
        onNotifyBanner({ type: 'error', message: 'Failed to update booking code.' });
      }
    } finally {
      setSavingCode(false);
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
        <div className="p-4 sm:p-6 border-b border-slate-800 flex items-center justify-between bg-gradient-to-r from-rose-950/30 via-slate-900 to-emerald-950/30">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-2xl bg-amber-500/20 text-amber-400 border border-amber-500/40">
              <Calculator className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg sm:text-xl font-black tracking-tight">
                SportyBet &amp; Bet9ja Slip Builder · +EV Ledger
              </h2>
              <p className="text-xs text-slate-400">
                Build model-backed SportyBet/Bet9ja tickets, price +EV vs book odds, attach booking codes &amp; track ROI
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
        <div className="px-4 py-3 border-b border-slate-800 flex flex-wrap items-center gap-2 bg-slate-950/40">
          <button
            type="button"
            onClick={() => setTab('bookmakers')}
            className={`px-3.5 py-2 rounded-xl text-xs font-extrabold flex items-center gap-1.5 transition-all ${
              tab === 'bookmakers'
                ? 'bg-rose-600 text-white shadow'
                : 'bg-slate-800/70 text-slate-400 hover:text-white'
            }`}
          >
            <Ticket className="w-3.5 h-3.5" />
            SportyBet &amp; Bet9ja
          </button>
          <button
            type="button"
            onClick={() => setTab('builder')}
            className={`px-3.5 py-2 rounded-xl text-xs font-extrabold flex items-center gap-1.5 transition-all ${
              tab === 'builder'
                ? 'bg-amber-600 text-white shadow'
                : 'bg-slate-800/70 text-slate-400 hover:text-white'
            }`}
          >
            <Receipt className="w-3.5 h-3.5" />
            +EV Slip Builder ({draftLegs.length})
          </button>
          <button
            type="button"
            onClick={() => setTab('ledger')}
            className={`px-3.5 py-2 rounded-xl text-xs font-extrabold flex items-center gap-1.5 transition-all ${
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
          {tab === 'bookmakers' ? (
            <div className="space-y-4">
              {/* Bookmaker Switcher & Scope Controls */}
              <div className="flex flex-wrap items-center justify-between gap-2.5">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setActiveBookmaker('sportybet')}
                    className={`px-3.5 py-2 rounded-xl text-xs font-black border transition-all ${
                      activeBookmaker === 'sportybet'
                        ? 'bg-rose-600 text-white border-rose-400 shadow-lg shadow-rose-600/20'
                        : 'bg-slate-800/70 text-slate-300 border-slate-700 hover:text-white'
                    }`}
                  >
                    SportyBet (NG)
                  </button>
                  <button
                    type="button"
                    onClick={() => setActiveBookmaker('bet9ja')}
                    className={`px-3.5 py-2 rounded-xl text-xs font-black border transition-all ${
                      activeBookmaker === 'bet9ja'
                        ? 'bg-emerald-600 text-white border-emerald-400 shadow-lg shadow-emerald-600/20'
                        : 'bg-slate-800/70 text-slate-300 border-slate-700 hover:text-white'
                    }`}
                  >
                    Bet9ja
                  </button>
                  {activeBookmakerMeta.sportsbookUrl && (
                    <a
                      href={activeBookmakerMeta.sportsbookUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 px-3 py-2 rounded-xl text-xs font-bold bg-slate-800/80 hover:bg-slate-700 text-cyan-300 border border-slate-700"
                    >
                      <span>Open {activeBookmakerMeta.shortName}</span>
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  <div className="flex items-center rounded-xl border border-slate-800 bg-slate-950 p-0.5 text-[11px] font-bold">
                    <button
                      type="button"
                      onClick={() => setDayScope('selected')}
                      disabled={!selectedPickDay || selectedPickDay === 'ALL_DAYS'}
                      className={`px-2.5 py-1 rounded-lg transition-all disabled:opacity-40 ${
                        dayScope === 'selected' && selectedPickDay !== 'ALL_DAYS'
                          ? 'bg-cyan-600 text-white'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      {selectedPickDay && selectedPickDay !== 'ALL_DAYS' ? selectedPickDay : 'Day'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setDayScope('all')}
                      className={`px-2.5 py-1 rounded-lg transition-all ${
                        dayScope === 'all' || selectedPickDay === 'ALL_DAYS'
                          ? 'bg-cyan-600 text-white'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      All Upcoming
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={fetchBookmakerSlips}
                    disabled={loadingBookmakerSlips}
                    className="p-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700"
                    title="Regenerate bookmaker slips"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${loadingBookmakerSlips ? 'animate-spin' : ''}`} />
                  </button>
                </div>
              </div>

              {/* Honest Model-Odds Notice */}
              <div className="p-3 rounded-2xl border border-cyan-500/25 bg-cyan-950/20 text-[11px] text-cyan-200/90 flex items-start gap-2.5">
                <ShieldCheck className="w-4 h-4 text-cyan-400 shrink-0 mt-0.5" />
                <div>
                  <span className="font-bold text-cyan-300">How this works: </span>
                  {activeBookmakerMeta.bookingNote} Odds displayed below are calibrated{' '}
                  <strong>Est. Model Fair Odds</strong> (not scraped sportsbook odds) — load into the{' '}
                  <strong>+EV Slip Builder</strong> to compare live odds and save your generated code.
                </div>
              </div>

              {/* Preset Strategy Pills */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {PRESET_TABS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => setActivePreset(preset.id)}
                    className={`p-2.5 rounded-2xl border text-left transition-all ${
                      activePreset === preset.id
                        ? 'bg-slate-800 border-amber-500/60 text-white shadow'
                        : 'bg-slate-950/60 border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-1">
                      <span className="text-xs font-extrabold">{preset.label}</span>
                      <span className="text-[10px] font-mono font-bold text-amber-400">{preset.badge}</span>
                    </div>
                  </button>
                ))}
              </div>

              {/* Active Preset Body */}
              {loadingBookmakerSlips ? (
                <div className="py-12 text-center space-y-2.5">
                  <RefreshCw className="w-7 h-7 mx-auto text-rose-400 animate-spin" />
                  <p className="text-xs font-semibold text-slate-400">
                    Building optimized {activeBookmakerMeta.name} slip candidates...
                  </p>
                </div>
              ) : currentPresetSlip && currentPresetSlip.legs?.length > 0 ? (
                <div className="space-y-3.5">
                  {/* KPI Bar */}
                  <div className="p-4 rounded-2xl bg-slate-950 border border-slate-800 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-wrap items-center gap-5">
                      <div>
                        <div className="text-[10px] font-bold uppercase text-slate-400">Selections</div>
                        <div className="text-base font-black text-white">{currentPresetSlip.legs_count} Matches</div>
                      </div>
                      <div>
                        <div className="text-[10px] font-bold uppercase text-slate-400">Est. Model Fair Odds</div>
                        <div className="text-base font-black text-amber-400">{currentPresetSlip.total_fair_odds}x</div>
                      </div>
                      <div>
                        <div className="text-[10px] font-bold uppercase text-slate-400">Joint Hit Prob</div>
                        <div className="text-base font-black text-cyan-400">{currentPresetSlip.combined_probability}%</div>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => copyTextWithFeedback(currentPresetSlip.share_text, `preset-${activePreset}`)}
                        className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-bold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
                      >
                        {copiedKey === `preset-${activePreset}` ? (
                          <Check className="w-3.5 h-3.5 text-emerald-400" />
                        ) : (
                          <Copy className="w-3.5 h-3.5" />
                        )}
                        <span>{copiedKey === `preset-${activePreset}` ? 'Copied!' : 'Copy Ticket'}</span>
                      </button>
                      <button
                        type="button"
                        onClick={handleLoadPresetToBuilder}
                        className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-extrabold bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg"
                      >
                        <Sparkles className="w-3.5 h-3.5" />
                        <span>Load into +EV Builder</span>
                      </button>
                    </div>
                  </div>

                  <p className="text-[11px] text-slate-400 px-1">{currentPresetSlip.description}</p>

                  {/* Preset Legs */}
                  <div className="space-y-2.5">
                    {currentPresetSlip.legs.map((leg, i) => (
                      <div
                        key={`${leg.fixture_id}-${leg.canonical_market}-${i}`}
                        className={`p-3.5 rounded-2xl border space-y-2 ${cardCls}`}
                      >
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="w-5 h-5 rounded-full bg-rose-500/20 text-rose-300 text-[10px] font-black flex items-center justify-center border border-rose-500/40">
                                {i + 1}
                              </span>
                              <span className="text-xs font-extrabold">{leg.match}</span>
                            </div>
                            <div className="text-[11px] text-slate-400 pl-7 mt-0.5">
                              {leg.league}
                              {leg.country ? ` (${leg.country})` : ''} · {leg.kickoff_gmt1} · xG {leg.expected_goals} · Scoreline {leg.most_likely_score}
                            </div>
                          </div>

                          <div className="text-right">
                            <div className="text-xs font-black text-emerald-400">{leg.market}</div>
                            <div className="text-[10px] font-mono text-slate-400">
                              {leg.probability}% Prob · Fair ~{leg.estimated_fair_odds}x
                            </div>
                          </div>
                        </div>

                        {/* Bookmaker UI Placement Guide */}
                        <div className="pl-0 sm:pl-7 flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-800/70">
                          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                            <span className="px-2 py-0.5 rounded-md bg-slate-800 text-slate-300 font-bold">
                              {leg.bookmaker_section}
                            </span>
                            <span className="text-slate-500">&rarr;</span>
                            <span className="px-2 py-0.5 rounded-md bg-slate-800 text-cyan-300 font-bold">
                              {leg.bookmaker_market}
                            </span>
                            <span className="text-slate-500">&rarr;</span>
                            <span className="px-2 py-0.5 rounded-md bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-extrabold">
                              {leg.bookmaker_pick}
                            </span>
                          </div>

                          {leg.sportsbook_search_url && (
                            <a
                              href={leg.sportsbook_search_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 text-[11px] font-bold text-amber-300 hover:text-amber-200"
                            >
                              <span>Find on {activeBookmakerMeta.shortName}</span>
                              <ExternalLink className="w-3 h-3" />
                            </a>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="py-10 text-center space-y-2 rounded-2xl border border-slate-800 bg-slate-950/50">
                  <Ticket className="w-8 h-8 mx-auto text-slate-500" />
                  <p className="text-sm font-bold text-slate-300">
                    Not enough qualifying picks for this preset right now
                  </p>
                  <p className="text-xs text-slate-500 max-w-md mx-auto">
                    Try switching to <strong>All Upcoming</strong> or selecting <strong>2-Odds Safe</strong> /{' '}
                    <strong>3-Odds Multi</strong>.
                  </p>
                </div>
              )}
            </div>
          ) : tab === 'builder' ? (
            draftLegs.length === 0 ? (
              <div className="py-12 text-center space-y-2.5">
                <Coins className="w-8 h-8 mx-auto text-amber-400/70" />
                <p className="text-sm font-bold">Your active +EV slip builder is empty</p>
                <p className="text-xs text-slate-400 max-w-md mx-auto">
                  Load a curated ticket from the <strong>SportyBet &amp; Bet9ja</strong> tab, or click{' '}
                  <strong>+ Slip</strong> on any match row, Live Goal Radar card, or Smart Accumulator.
                </p>
                <button
                  type="button"
                  onClick={() => setTab('bookmakers')}
                  className="mt-2 inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-extrabold bg-rose-600 hover:bg-rose-500 text-white"
                >
                  <Ticket className="w-3.5 h-3.5" />
                  Browse SportyBet &amp; Bet9ja Slips
                </button>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                  <div className="sm:col-span-2 lg:col-span-1">
                    <label className="text-[11px] font-bold text-slate-400 block mb-1">Slip Label</label>
                    <input
                      type="text"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder={`${draftLegs.length}-Leg Edge Ticket`}
                      className={`w-full px-3 py-2 rounded-xl border text-xs font-semibold ${inputCls}`}
                    />
                  </div>
                  <div>
                    <label className="text-[11px] font-bold text-slate-400 block mb-1">Sportsbook Profile</label>
                    <select
                      value={draftBookmaker}
                      onChange={(e) => onChangeDraftBookmaker && onChangeDraftBookmaker(e.target.value)}
                      className={`w-full px-3 py-2 rounded-xl border text-xs font-bold ${inputCls}`}
                    >
                      <option value="general">General / Multi-Book</option>
                      <option value="sportybet">SportyBet (NG)</option>
                      <option value="bet9ja">Bet9ja</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-[11px] font-bold text-slate-400 block mb-1">
                      Booking Code (Optional)
                    </label>
                    <input
                      type="text"
                      maxLength={32}
                      value={bookingCode}
                      onChange={(e) => setBookingCode(e.target.value.toUpperCase())}
                      placeholder="e.g. BC7A9X"
                      className={`w-full px-3 py-2 rounded-xl border text-xs font-mono font-bold uppercase ${inputCls}`}
                    />
                  </div>
                  <div>
                    <label className="text-[11px] font-bold text-slate-400 block mb-1">Simulated Stake ($)</label>
                    <input
                      type="number"
                      min={1}
                      max={100000}
                      value={stake}
                      onChange={(e) => setStake(e.target.value)}
                      className={`w-full px-3 py-2 rounded-xl border text-xs font-bold ${inputCls}`}
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

                      <div className="grid grid-cols-1 sm:grid-cols-4 gap-2 items-center text-xs">
                        <div>
                          <label className="text-[10px] font-bold text-slate-400 block mb-1">Market</label>
                          <select
                            value={leg.market}
                            onChange={(e) => onUpdateDraftLeg(idx, { market: e.target.value })}
                            className={`w-full px-2.5 py-1.5 rounded-lg border font-bold text-xs ${
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

                      {leg.translation && (
                        <div className="flex flex-wrap items-center justify-between gap-2 pt-1.5 border-t border-slate-800/60 text-[11px]">
                          <span className="text-slate-400">
                            <strong className="text-slate-200">{BOOKMAKER_META[draftBookmaker]?.shortName} Path:</strong>{' '}
                            <span className="text-emerald-300 font-semibold">{leg.translation.hint}</span>
                          </span>
                          <a
                            href={getBookmakerSearchUrl(draftBookmaker, '', '', leg.match_label)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-amber-300 hover:text-amber-200 font-bold"
                          >
                            <span>Search Match</span>
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        </div>
                      )}
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

                <div className="flex flex-wrap items-center justify-between gap-3">
                  <button
                    type="button"
                    onClick={onClearDraftLegs}
                    className="px-3.5 py-2 rounded-xl text-xs font-bold text-rose-400 border border-rose-500/30 hover:bg-rose-500/10"
                  >
                    Clear Slip
                  </button>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        copyTextWithFeedback(
                          formatBookmakerSlipText({
                            bookmaker: draftBookmaker === 'bet9ja' ? 'bet9ja' : 'sportybet',
                            title: title || `${draftLegs.length}-Leg Slip`,
                            legs: slipMetrics.enrichedLegs,
                            combinedOdds: slipMetrics.combinedOdds,
                            bookingCode,
                          }),
                          'builder-copy'
                        )
                      }
                      className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
                    >
                      {copiedKey === 'builder-copy' ? (
                        <Check className="w-3.5 h-3.5 text-emerald-400" />
                      ) : (
                        <Copy className="w-3.5 h-3.5" />
                      )}
                      <span>{copiedKey === 'builder-copy' ? 'Copied!' : 'Copy Formatted'}</span>
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
                  {slips.map((s) => {
                    const bmMeta = BOOKMAKER_META[s.bookmaker] || BOOKMAKER_META.general;
                    const isEditingCode = editingSlipId === s.id;
                    return (
                      <div key={s.id} className={`p-3.5 rounded-2xl border space-y-2.5 ${cardCls}`}>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <span
                                className={`px-2 py-0.5 rounded-full text-[10px] font-black border ${
                                  STATUS_BADGE[s.status] || STATUS_BADGE.PENDING
                                }`}
                              >
                                {s.status}
                              </span>
                              {s.bookmaker && s.bookmaker !== 'general' && (
                                <span
                                  className={`px-2 py-0.5 rounded-full text-[10px] font-black border ${bmMeta.accentClass}`}
                                >
                                  {bmMeta.shortName}
                                </span>
                              )}
                              {s.booking_code && (
                                <button
                                  type="button"
                                  onClick={() => copyTextWithFeedback(s.booking_code, `code-${s.id}`)}
                                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-black bg-cyan-500/15 text-cyan-300 border border-cyan-500/40 hover:bg-cyan-500/25"
                                  title="Copy booking code"
                                >
                                  <span>{copiedKey === `code-${s.id}` ? 'Copied Code!' : `Code: ${s.booking_code}`}</span>
                                  <Copy className="w-2.5 h-2.5" />
                                </button>
                              )}
                              <span className="text-xs font-extrabold">{s.title}</span>
                            </div>
                            <div className="text-[11px] text-slate-400 mt-0.5">
                              Stake: ${s.stake} · Odds: {s.combined_odds}x · Expected EV:{' '}
                              {s.expected_value_pct >= 0 ? `+${s.expected_value_pct}%` : `${s.expected_value_pct}%`}
                            </div>
                          </div>

                          <div className="flex items-center gap-2">
                            <div className="text-right mr-1">
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
                              onClick={() =>
                                copyTextWithFeedback(
                                  formatBookmakerSlipText({
                                    bookmaker: s.bookmaker === 'bet9ja' ? 'bet9ja' : 'sportybet',
                                    title: s.title,
                                    legs: s.legs,
                                    combinedOdds: s.combined_odds,
                                    bookingCode: s.booking_code || '',
                                  }),
                                  `slip-copy-${s.id}`
                                )
                              }
                              className="p-1.5 rounded-lg text-slate-400 hover:text-cyan-300 hover:bg-slate-800"
                              title="Copy slip share text"
                            >
                              {copiedKey === `slip-copy-${s.id}` ? (
                                <Check className="w-3.5 h-3.5 text-emerald-400" />
                              ) : (
                                <Copy className="w-3.5 h-3.5" />
                              )}
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                if (isEditingCode) {
                                  setEditingSlipId(null);
                                } else {
                                  setEditingSlipId(s.id);
                                  setEditingBookmaker(
                                    s.bookmaker && s.bookmaker !== 'general' ? s.bookmaker : 'sportybet'
                                  );
                                  setEditingBookingCode(s.booking_code || '');
                                }
                              }}
                              className="p-1.5 rounded-lg text-slate-400 hover:text-amber-300 hover:bg-slate-800"
                              title="Attach or edit SportyBet / Bet9ja Booking Code"
                            >
                              <Tag className="w-3.5 h-3.5" />
                            </button>
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

                        {isEditingCode && (
                          <div className="flex flex-wrap items-center gap-2 p-2.5 rounded-xl bg-slate-900/90 border border-slate-800">
                            <select
                              value={editingBookmaker}
                              onChange={(e) => setEditingBookmaker(e.target.value)}
                              className="px-2.5 py-1.5 rounded-lg bg-slate-950 border border-slate-700 text-xs font-bold text-white"
                            >
                              <option value="sportybet">SportyBet (NG)</option>
                              <option value="bet9ja">Bet9ja</option>
                              <option value="general">General</option>
                            </select>
                            <input
                              type="text"
                              maxLength={32}
                              value={editingBookingCode}
                              onChange={(e) => setEditingBookingCode(e.target.value.toUpperCase())}
                              placeholder="Enter SportyBet / Bet9ja Booking Code"
                              className="flex-1 min-w-[160px] px-2.5 py-1.5 rounded-lg bg-slate-950 border border-slate-700 text-xs font-mono font-bold uppercase text-white"
                            />
                            <button
                              type="button"
                              onClick={() => handleSaveSlipBookingCode(s.id)}
                              disabled={savingCode}
                              className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-extrabold disabled:opacity-50"
                            >
                              {savingCode ? 'Saving...' : 'Save Code'}
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditingSlipId(null)}
                              className="px-2.5 py-1.5 rounded-lg bg-slate-800 text-slate-300 text-xs font-bold"
                            >
                              Cancel
                            </button>
                          </div>
                        )}

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
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
