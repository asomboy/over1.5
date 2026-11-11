import React, { useEffect, useState } from 'react';
import {
  Bell,
  BellRing,
  Check,
  Clock,
  Lock,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  Volume2,
  X,
} from 'lucide-react';
import {
  ALL_SIGNAL_MARKETS,
  dispatchLiveBrowserNotification,
  getBrowserNotificationPermission,
  playGoalAlertChime,
  requestBrowserNotificationPermission,
} from '../utils/alertUtils';

export default function AlertSettingsModal({
  isOpen,
  onClose,
  settings,
  onUpdateSettings,
  availableLeagues = [],
  watchlistCount = 0,
  apiRequest,
  darkMode = true,
  onNotifyBanner,
}) {
  const [permission, setPermission] = useState(() => getBrowserNotificationPermission());
  const [leagueQuery, setLeagueQuery] = useState('');
  const [serverPrefs, setServerPrefs] = useState(null);
  const [loadingServer, setLoadingServer] = useState(false);
  const [savingServer, setSavingServer] = useState(false);
  const [adminKeyInput, setAdminKeyInput] = useState(() =>
    typeof window !== 'undefined' ? window.localStorage?.getItem('sgp_admin_key') || '' : ''
  );
  const [serverMsg, setServerMsg] = useState(null);

  useEffect(() => {
    if (!isOpen) return;
    setPermission(getBrowserNotificationPermission());
    setServerMsg(null);
    let cancelled = false;
    const loadServerPrefs = async () => {
      setLoadingServer(true);
      try {
        const res = await apiRequest('get', '/api/alerts/preferences');
        if (!cancelled && res.data?.preferences) {
          setServerPrefs(res.data.preferences);
        }
      } catch {
        // Non-blocking: local browser alert rules remain fully functional
      } finally {
        if (!cancelled) setLoadingServer(false);
      }
    };
    loadServerPrefs();
    return () => {
      cancelled = true;
    };
  }, [isOpen, apiRequest]);

  if (!isOpen) return null;

  const surface = darkMode
    ? 'bg-slate-900 border-slate-800 text-white'
    : 'bg-white border-slate-200 text-slate-900';
  const cardCls = darkMode
    ? 'bg-slate-950/70 border-slate-800'
    : 'bg-slate-50 border-slate-200';

  const handleEnableBrowserPush = async () => {
    const res = await requestBrowserNotificationPermission();
    setPermission(res);
    if (res === 'granted') {
      onUpdateSettings({ ...settings, browserPushEnabled: true });
      if (onNotifyBanner) {
        onNotifyBanner({ type: 'success', message: 'Browser live goal notifications enabled!' });
      }
    } else if (res === 'denied' && onNotifyBanner) {
      onNotifyBanner({
        type: 'error',
        message: 'Browser notifications are blocked in browser site settings.',
      });
    }
  };

  const handleTestSignal = async () => {
    playGoalAlertChime();
    if (permission === 'default') {
      const res = await requestBrowserNotificationPermission();
      setPermission(res);
    }
    const sent = await dispatchLiveBrowserNotification({
      key: `test-${Date.now()}`,
      fixtureId: 0,
      strength: 'STRONG',
      market: 'At Least 1 More Goal',
      probPct: 82,
      matchLabel: 'Test Fixture FC vs Live Demo',
      scoreText: '1-1',
      minute: 67,
      league: 'Signal Test',
    });
    if (onNotifyBanner) {
      onNotifyBanner({
        type: 'success',
        message: sent
          ? 'Test chime + browser notification triggered!'
          : 'Audio chime played (grant Notification permission for desktop popups).',
      });
    }
  };

  const toggleMarket = (mkt) => {
    const curr = new Set(settings.allowedMarkets || []);
    if (curr.has(mkt)) curr.delete(mkt);
    else curr.add(mkt);
    onUpdateSettings({ ...settings, allowedMarkets: Array.from(curr) });
  };

  const toggleLeague = (lg) => {
    const curr = new Set(settings.allowedLeagues || []);
    if (curr.has(lg)) curr.delete(lg);
    else curr.add(lg);
    onUpdateSettings({ ...settings, allowedLeagues: Array.from(curr) });
  };

  const handleSaveServerRules = async () => {
    if (!serverPrefs) return;
    if (typeof window !== 'undefined' && adminKeyInput.trim()) {
      window.localStorage.setItem('sgp_admin_key', adminKeyInput.trim());
    }
    setSavingServer(true);
    setServerMsg(null);
    try {
      const payload = {
        telegram_live_enabled: Boolean(serverPrefs.telegram_live_enabled),
        telegram_digest_enabled: Boolean(serverPrefs.telegram_digest_enabled),
        min_live_probability: Number(settings.minProbabilityPct) / 100.0,
        min_live_confidence: Number(settings.minConfidence),
        min_time_remaining: Number(settings.minTimeRemaining),
        allowed_markets: settings.allowedMarkets || [],
        allowed_leagues: settings.allowedLeagues || [],
        quiet_hours_start:
          serverPrefs.quiet_hours_start === '' || serverPrefs.quiet_hours_start === null
            ? null
            : Number(serverPrefs.quiet_hours_start),
        quiet_hours_end:
          serverPrefs.quiet_hours_end === '' || serverPrefs.quiet_hours_end === null
            ? null
            : Number(serverPrefs.quiet_hours_end),
        browser_push_enabled: Boolean(settings.browserPushEnabled),
        audio_chime_enabled: Boolean(settings.audioChimeEnabled),
      };
      const res = await apiRequest('put', '/api/alerts/preferences', payload, {
        headers: adminKeyInput.trim() ? { 'X-Admin-Key': adminKeyInput.trim() } : {},
      });
      if (res.data?.preferences) {
        setServerPrefs(res.data.preferences);
      }
      setServerMsg({ type: 'ok', text: 'Saved Telegram & signal rules to server!' });
    } catch (err) {
      const detail =
        err?.response?.status === 401 || err?.response?.status === 403
          ? 'Admin Key required in production to save server Telegram rules.'
          : err?.response?.data?.detail || 'Could not update server rules.';
      setServerMsg({ type: 'err', text: detail });
    } finally {
      setSavingServer(false);
    }
  };

  const filteredLeagues = availableLeagues
    .filter((lg) => lg.toLowerCase().includes(leagueQuery.trim().toLowerCase()))
    .slice(0, 30);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-slate-950/80 backdrop-blur-md">
      <div className={`w-full max-w-3xl rounded-3xl border shadow-2xl overflow-hidden flex flex-col max-h-[90vh] ${surface}`}>
        {/* Header */}
        <div className="p-4 sm:p-6 border-b border-slate-800 flex items-center justify-between bg-gradient-to-r from-cyan-950/40 via-slate-900 to-rose-950/30">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-2xl bg-cyan-500/20 text-cyan-400 border border-cyan-500/40">
              <BellRing className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg sm:text-xl font-black tracking-tight">Live Signal &amp; Alert Preferences</h2>
              <p className="text-xs text-slate-400">
                Customize browser push alerts, audio chime triggers, custom probability gates, and Telegram rules
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

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-5">
          {/* 1. In-Browser Push & Audio Alerts */}
          <div className={`p-4 rounded-2xl border space-y-3 ${cardCls}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Bell className="w-4 h-4 text-cyan-400" />
                <h3 className="text-sm font-extrabold">In-Browser Push &amp; Audio Chime Alerts</h3>
              </div>
              <span
                className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${
                  permission === 'granted'
                    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                    : permission === 'denied'
                    ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                    : 'bg-amber-500/15 text-amber-300 border border-amber-500/35'
                }`}
              >
                Browser Permission: {permission}
              </span>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <label className="inline-flex items-center gap-2 text-xs font-semibold cursor-pointer">
                <input
                  type="checkbox"
                  checked={Boolean(settings.browserPushEnabled)}
                  onChange={(e) => {
                    if (e.target.checked && permission !== 'granted') {
                      handleEnableBrowserPush();
                    } else {
                      onUpdateSettings({ ...settings, browserPushEnabled: e.target.checked });
                    }
                  }}
                  className="rounded border-slate-700"
                />
                <span>Desktop / PWA Push Notification</span>
              </label>

              <label className="inline-flex items-center gap-2 text-xs font-semibold cursor-pointer">
                <input
                  type="checkbox"
                  checked={Boolean(settings.audioChimeEnabled)}
                  onChange={(e) =>
                    onUpdateSettings({ ...settings, audioChimeEnabled: e.target.checked })
                  }
                  className="rounded border-slate-700"
                />
                <Volume2 className="w-3.5 h-3.5 text-amber-400" />
                <span>Play Audio Chime</span>
              </label>

              <div className="ml-auto flex items-center gap-2">
                {permission !== 'granted' && permission !== 'unsupported' && (
                  <button
                    type="button"
                    onClick={handleEnableBrowserPush}
                    className="px-3 py-1.5 rounded-lg text-xs font-bold bg-cyan-600 hover:bg-cyan-500 text-white"
                  >
                    Grant Permission
                  </button>
                )}
                <button
                  type="button"
                  onClick={handleTestSignal}
                  className="px-3 py-1.5 rounded-lg text-xs font-bold bg-slate-800 hover:bg-slate-700 text-emerald-300 border border-slate-700"
                >
                  Test Chime &amp; Alert
                </button>
              </div>
            </div>
          </div>

          {/* 2. Thresholds & Watchlist Filter */}
          <div className={`p-4 rounded-2xl border space-y-4 ${cardCls}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <SlidersHorizontal className="w-4 h-4 text-emerald-400" />
                <h3 className="text-sm font-extrabold">Custom Signal Thresholds &amp; Filters</h3>
              </div>
              <label className="inline-flex items-center gap-2 text-xs font-bold text-amber-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={Boolean(settings.watchlistOnlyAlerts)}
                  onChange={(e) =>
                    onUpdateSettings({ ...settings, watchlistOnlyAlerts: e.target.checked })
                  }
                  className="rounded border-slate-700"
                />
                <span>Watchlist Only ({watchlistCount} starred)</span>
              </label>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <div className="flex justify-between text-xs font-bold mb-1">
                  <span className="text-slate-400">Min Probability</span>
                  <span className="text-emerald-400">{settings.minProbabilityPct}%</span>
                </div>
                <input
                  type="range"
                  min={55}
                  max={95}
                  step={1}
                  value={settings.minProbabilityPct}
                  onChange={(e) =>
                    onUpdateSettings({ ...settings, minProbabilityPct: Number(e.target.value) })
                  }
                  className="w-full accent-emerald-500"
                />
              </div>

              <div>
                <div className="flex justify-between text-xs font-bold mb-1">
                  <span className="text-slate-400">Min Confidence</span>
                  <span className="text-cyan-400">{settings.minConfidence}%</span>
                </div>
                <input
                  type="range"
                  min={50}
                  max={95}
                  step={1}
                  value={settings.minConfidence}
                  onChange={(e) =>
                    onUpdateSettings({ ...settings, minConfidence: Number(e.target.value) })
                  }
                  className="w-full accent-cyan-500"
                />
              </div>

              <div>
                <div className="flex justify-between text-xs font-bold mb-1">
                  <span className="text-slate-400">Min Mins Remaining</span>
                  <span className="text-amber-400">{settings.minTimeRemaining}&apos;</span>
                </div>
                <input
                  type="range"
                  min={5}
                  max={60}
                  step={1}
                  value={settings.minTimeRemaining}
                  onChange={(e) =>
                    onUpdateSettings({ ...settings, minTimeRemaining: Number(e.target.value) })
                  }
                  className="w-full accent-amber-500"
                />
              </div>
            </div>

            {/* Allowed Signal Markets */}
            <div>
              <div className="text-xs font-bold text-slate-400 mb-2">Alert Markets</div>
              <div className="flex flex-wrap gap-2">
                {ALL_SIGNAL_MARKETS.map((mkt) => {
                  const active = (settings.allowedMarkets || []).includes(mkt);
                  return (
                    <button
                      key={mkt}
                      type="button"
                      onClick={() => toggleMarket(mkt)}
                      className={`px-2.5 py-1 rounded-lg text-xs font-bold border transition-all inline-flex items-center gap-1.5 ${
                        active
                          ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-300'
                          : 'bg-slate-900/50 border-slate-800 text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {active && <Check className="w-3 h-3" />}
                      {mkt}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Allowed Leagues */}
            <div>
              <div className="flex items-center justify-between gap-2 mb-2">
                <span className="text-xs font-bold text-slate-400">
                  League Filter ({settings.allowedLeagues?.length || 0} selected — empty = All Competitions)
                </span>
                {settings.allowedLeagues?.length > 0 && (
                  <button
                    type="button"
                    onClick={() => onUpdateSettings({ ...settings, allowedLeagues: [] })}
                    className="text-[11px] font-bold text-rose-400 hover:underline"
                  >
                    Clear Leagues
                  </button>
                )}
              </div>
              <input
                type="text"
                value={leagueQuery}
                onChange={(e) => setLeagueQuery(e.target.value)}
                placeholder="Filter competition names..."
                className={`w-full mb-2 px-3 py-1.5 rounded-lg border text-xs ${
                  darkMode
                    ? 'bg-slate-900 border-slate-800 text-slate-200'
                    : 'bg-white border-slate-300 text-slate-800'
                }`}
              />
              <div className="flex flex-wrap gap-1.5 max-h-28 overflow-y-auto">
                {filteredLeagues.map((lg) => {
                  const active = (settings.allowedLeagues || []).includes(lg);
                  return (
                    <button
                      key={lg}
                      type="button"
                      onClick={() => toggleLeague(lg)}
                      className={`px-2 py-0.5 rounded-md text-[11px] font-semibold border ${
                        active
                          ? 'bg-cyan-500/20 border-cyan-500/50 text-cyan-300'
                          : 'bg-slate-900/40 border-slate-800 text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {lg}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* 3. Server Telegram Bot Rules & Quiet Hours */}
          <div className={`p-4 rounded-2xl border space-y-3 ${cardCls}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-rose-400" />
                <h3 className="text-sm font-extrabold">Server Telegram Broadcast &amp; Quiet Hours</h3>
              </div>
              {loadingServer && <RefreshCw className="w-3.5 h-3.5 animate-spin text-slate-400" />}
            </div>

            {serverPrefs ? (
              <div className="space-y-3">
                <div className="flex flex-wrap gap-4 text-xs font-semibold">
                  <label className="inline-flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={Boolean(serverPrefs.telegram_live_enabled)}
                      onChange={(e) =>
                        setServerPrefs({ ...serverPrefs, telegram_live_enabled: e.target.checked })
                      }
                    />
                    <span>Telegram Live Goal Signals</span>
                  </label>
                  <label className="inline-flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={Boolean(serverPrefs.telegram_digest_enabled)}
                      onChange={(e) =>
                        setServerPrefs({ ...serverPrefs, telegram_digest_enabled: e.target.checked })
                      }
                    />
                    <span>Scheduled 3-Odds Digests</span>
                  </label>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
                  <div>
                    <label className="text-[11px] font-bold text-slate-400 flex items-center gap-1 mb-1">
                      <Clock className="w-3 h-3" /> Quiet Start (UTC 0-23)
                    </label>
                    <input
                      type="number"
                      min={0}
                      max={23}
                      placeholder="Off"
                      value={serverPrefs.quiet_hours_start ?? ''}
                      onChange={(e) =>
                        setServerPrefs({
                          ...serverPrefs,
                          quiet_hours_start: e.target.value === '' ? null : Number(e.target.value),
                        })
                      }
                      className={`w-full px-2.5 py-1.5 rounded-lg border text-xs ${
                        darkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-300'
                      }`}
                    />
                  </div>
                  <div>
                    <label className="text-[11px] font-bold text-slate-400 flex items-center gap-1 mb-1">
                      <Clock className="w-3 h-3" /> Quiet End (UTC 0-23)
                    </label>
                    <input
                      type="number"
                      min={0}
                      max={23}
                      placeholder="Off"
                      value={serverPrefs.quiet_hours_end ?? ''}
                      onChange={(e) =>
                        setServerPrefs({
                          ...serverPrefs,
                          quiet_hours_end: e.target.value === '' ? null : Number(e.target.value),
                        })
                      }
                      className={`w-full px-2.5 py-1.5 rounded-lg border text-xs ${
                        darkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-300'
                      }`}
                    />
                  </div>
                  <div>
                    <label className="text-[11px] font-bold text-slate-400 flex items-center gap-1 mb-1">
                      <Lock className="w-3 h-3" /> Admin Key (if enabled)
                    </label>
                    <input
                      type="password"
                      value={adminKeyInput}
                      onChange={(e) => setAdminKeyInput(e.target.value)}
                      placeholder="Optional X-Admin-Key"
                      className={`w-full px-2.5 py-1.5 rounded-lg border text-xs ${
                        darkMode ? 'bg-slate-900 border-slate-800' : 'bg-white border-slate-300'
                      }`}
                    />
                  </div>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                  {serverMsg ? (
                    <span
                      className={`text-xs font-bold ${
                        serverMsg.type === 'ok' ? 'text-emerald-400' : 'text-rose-400'
                      }`}
                    >
                      {serverMsg.text}
                    </span>
                  ) : (
                    <span className="text-[11px] text-slate-500">
                      Local alert settings save automatically; click below to sync Telegram rules to backend.
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={handleSaveServerRules}
                    disabled={savingServer}
                    className="px-4 py-1.5 rounded-xl text-xs font-extrabold bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50"
                  >
                    {savingServer ? 'Saving...' : 'Sync Rules to Server'}
                  </button>
                </div>
              </div>
            ) : (
              <p className="text-xs text-slate-500">Server preferences unavailable while offline.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

