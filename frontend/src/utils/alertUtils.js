const WATCHLIST_STORAGE_KEY = 'sgp_watchlist_ids';
const ALERT_SETTINGS_KEY = 'sgp_alert_settings';
const SENT_SIGNALS_KEY = 'sgp_notified_live_signals';
const SIGNAL_DEDUP_TTL_MS = 20 * 60 * 1000;

export const ALL_SIGNAL_MARKETS = [
  'At Least 1 More Goal',
  'Full Match Over 1.5 Goals',
  'Full Match Over 2.5 Goals',
  'Full Match Over 3.5 Goals',
];

export const DEFAULT_ALERT_SETTINGS = {
  browserPushEnabled: false,
  audioChimeEnabled: true,
  minProbabilityPct: 74,
  minConfidence: 70,
  minTimeRemaining: 10,
  watchlistOnlyAlerts: false,
  allowedLeagues: [],
  allowedMarkets: [...ALL_SIGNAL_MARKETS],
};

export function loadWatchlistIds() {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(WATCHLIST_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.map(Number).filter((n) => Number.isFinite(n) && n > 0) : [];
  } catch {
    return [];
  }
}

export function saveWatchlistIds(ids) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(Array.from(new Set(ids))));
  } catch {
    // Ignore quota errors cleanly
  }
}

export function loadAlertSettings() {
  if (typeof window === 'undefined') return { ...DEFAULT_ALERT_SETTINGS };
  try {
    const raw = window.localStorage.getItem(ALERT_SETTINGS_KEY);
    if (!raw) return { ...DEFAULT_ALERT_SETTINGS };
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return { ...DEFAULT_ALERT_SETTINGS };
    return {
      ...DEFAULT_ALERT_SETTINGS,
      ...parsed,
      allowedLeagues: Array.isArray(parsed.allowedLeagues) ? parsed.allowedLeagues : [],
      allowedMarkets: Array.isArray(parsed.allowedMarkets) ? parsed.allowedMarkets : [...ALL_SIGNAL_MARKETS],
    };
  } catch {
    return { ...DEFAULT_ALERT_SETTINGS };
  }
}

export function saveAlertSettings(settings) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(ALERT_SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Ignore quota errors cleanly
  }
}

export function getBrowserNotificationPermission() {
  if (typeof window === 'undefined' || typeof Notification === 'undefined') {
    return 'unsupported';
  }
  return Notification.permission || 'default';
}

export async function requestBrowserNotificationPermission() {
  if (typeof window === 'undefined' || typeof Notification === 'undefined') {
    return 'unsupported';
  }
  if (Notification.permission === 'granted') return 'granted';
  try {
    return await Notification.requestPermission();
  } catch {
    return Notification.permission || 'denied';
  }
}

export function playGoalAlertChime() {
  if (typeof window === 'undefined') return false;
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return false;
  try {
    const ctx = new AudioCtx();
    const notes = [1046.5, 1318.51, 1567.98]; // C6 - E6 - G6 harmonic triad
    const now = ctx.currentTime;
    notes.forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + idx * 0.09);
      gain.gain.setValueAtTime(0.0001, now + idx * 0.09);
      gain.gain.exponentialRampToValueAtTime(0.11, now + idx * 0.09 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.09 + 0.34);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + idx * 0.09);
      osc.stop(now + idx * 0.09 + 0.36);
    });
    setTimeout(() => {
      if (ctx.state !== 'closed') {
        ctx.close().catch(() => {});
      }
    }, 1000);
    return true;
  } catch {
    return false;
  }
}

export async function dispatchLiveBrowserNotification(alertPayload) {
  if (typeof window === 'undefined' || typeof Notification === 'undefined') return false;
  if (Notification.permission !== 'granted') return false;

  const title = `⚽ ${alertPayload.strength}: ${alertPayload.market} (${alertPayload.probPct}%)`;
  const body = `${alertPayload.matchLabel} (${alertPayload.scoreText}, ${alertPayload.minute}') • ${alertPayload.league}`;
  const notifOptions = {
    body,
    icon: '/favicon.svg',
    badge: '/favicon.svg',
    tag: alertPayload.key || `sgp-${alertPayload.fixtureId}`,
    renotify: false,
  };

  try {
    if (navigator.serviceWorker) {
      const reg = await navigator.serviceWorker.getRegistration();
      if (reg && typeof reg.showNotification === 'function') {
        await reg.showNotification(title, notifOptions);
        return true;
      }
    }
    new Notification(title, notifOptions);
    return true;
  } catch {
    return false;
  }
}

function loadSentSignalMap() {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(SENT_SIGNALS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    const now = Date.now();
    const cleaned = {};
    Object.entries(parsed || {}).forEach(([k, ts]) => {
      if (typeof ts === 'number' && now - ts < SIGNAL_DEDUP_TTL_MS) {
        cleaned[k] = ts;
      }
    });
    return cleaned;
  } catch {
    return {};
  }
}

export function hasSignalBeenNotified(key) {
  const map = loadSentSignalMap();
  return Boolean(map[key]);
}

export function markSignalNotified(key) {
  if (typeof window === 'undefined') return;
  try {
    const map = loadSentSignalMap();
    map[key] = Date.now();
    window.localStorage.setItem(SENT_SIGNALS_KEY, JSON.stringify(map));
  } catch {
    // Ignore storage errors cleanly
  }
}

export function evaluateClientLiveAlert(liveFix, settings, watchlistSet) {
  if (!liveFix || !liveFix.fixture_id) return null;
  if (liveFix.freshness_status && liveFix.freshness_status !== 'FRESH') return null;

  const fid = Number(liveFix.fixture_id);
  if (settings.watchlistOnlyAlerts && watchlistSet && !watchlistSet.has(fid)) {
    return null;
  }

  const leagueName = String(liveFix.league_name || '');
  const allowedLeagues = Array.isArray(settings.allowedLeagues) ? settings.allowedLeagues : [];
  if (allowedLeagues.length > 0) {
    const leagueLower = leagueName.toLowerCase();
    const matched = allowedLeagues.some((al) => {
      const aLow = String(al || '').trim().toLowerCase();
      return aLow && (leagueLower.includes(aLow) || aLow.includes(leagueLower));
    });
    if (!matched) return null;
  }

  const overallConf = Number(liveFix.confidence || 0);
  if (overallConf < Number(settings.minConfidence || 70)) return null;

  const allowedMarkets = Array.isArray(settings.allowedMarkets) ? settings.allowedMarkets : [];
  const marketAllowed = (mkt) => {
    if (allowedMarkets.length === 0) return false;
    const mLow = String(mkt || '').toLowerCase();
    return allowedMarkets.some((am) => {
      const aLow = String(am || '').toLowerCase();
      return mLow.includes(aLow) || aLow.includes(mLow);
    });
  };

  const candidates = [];
  if (Array.isArray(liveFix.goal_signals)) {
    candidates.push(...liveFix.goal_signals);
  }
  if (liveFix.best_signal && String(liveFix.best_signal.category || '').toLowerCase() === 'goals') {
    candidates.push(liveFix.best_signal);
  }

  const minProb = Number(settings.minProbabilityPct || 74) / 100.0;
  const minRem = Number(settings.minTimeRemaining || 10);
  const minConf = Number(settings.minConfidence || 70);

  const qualifying = candidates.filter((sig) => {
    if (!sig || typeof sig !== 'object') return false;
    const mkt = String(sig.market || '').trim();
    if (!mkt || mkt.toUpperCase() === 'NO_SIGNAL') return false;
    const strength = String(sig.signal_strength || sig.label || '').toUpperCase();
    if (strength !== 'STRONG' && strength !== 'MODERATE') return false;
    if (!marketAllowed(mkt)) return false;
    const prob = Number(sig.probability || 0);
    const rem = Number(sig.time_remaining_minutes ?? 15);
    const sigConf = Number(sig.confidence_score || sig.signal_score || overallConf);
    return prob >= minProb && rem >= minRem && Math.max(overallConf, sigConf) >= minConf;
  });

  if (qualifying.length === 0) return null;
  qualifying.sort((a, b) => Number(b.probability || 0) - Number(a.probability || 0));
  const picked = qualifying[0];
  const probPct = Math.round(Number(picked.probability || 0) * 100);
  const market = String(picked.market || '').trim();

  return {
    key: `${fid}::${market}`,
    fixtureId: fid,
    matchLabel: `${liveFix.home_team_name || 'Home'} vs ${liveFix.away_team_name || 'Away'}`,
    league: leagueName || 'Live Match',
    market,
    probPct,
    probability: Number(picked.probability || 0),
    minute: Number(liveFix.minute || 0),
    scoreText: `${liveFix.home_score ?? 0}-${liveFix.away_score ?? 0}`,
    strength: String(picked.signal_strength || picked.label || 'STRONG').toUpperCase(),
  };
}

export function extractQuickSlipLeg(fix, activeMarketTab = 'overview', liveItem = null) {
  if (!fix) return null;

  if (liveItem && liveItem.fixture_id) {
    const goalSignals = Array.isArray(liveItem.goal_signals) ? liveItem.goal_signals : [];
    const activeSig = goalSignals.find((s) => s && s.market && s.market !== 'NO_SIGNAL');
    const pPlus1 = liveItem.live_goals?.at_least_1_more_goal?.probability;
    const prob = Number(activeSig?.probability ?? pPlus1 ?? 0.65);
    const market = activeSig?.market || 'At Least 1 More Goal';
    const baseGoals = Number(liveItem.home_score ?? 0) + Number(liveItem.away_score ?? 0);
    return {
      fixture_id: Number(liveItem.fixture_id),
      match_label: `${liveItem.home_team_name || 'Home'} vs ${liveItem.away_team_name || 'Away'}`,
      league_name: liveItem.league_name || '',
      match_date: null,
      market,
      model_prob: Math.max(0.05, Math.min(0.95, prob)),
      bookmaker_odds: '',
      baseline_goals: baseGoals,
    };
  }

  const pred = fix.prediction || {};
  let market = 'Over 1.5 Goals';
  let prob = Number(pred.over_1_5_probability || 0.72);

  if (activeMarketTab === 'goals_ou') {
    if (Number(pred.over_2_5_probability || 0) >= 0.54) {
      market = 'Over 2.5 Goals';
      prob = Number(pred.over_2_5_probability);
    } else if (Number(pred.btts_probability || 0) >= 0.56) {
      market = 'BTTS Yes';
      prob = Number(pred.btts_probability);
    }
  } else if (activeMarketTab === 'halves' && pred.first_half_over_0_5_probability != null) {
    market = '1H Over 0.5 Goals';
    prob = Number(pred.first_half_over_0_5_probability);
  } else if (activeMarketTab === 'corners' && fix.corners?.total_markets?.over_8_5 != null) {
    market = 'Over 8.5 Corners';
    prob = Number(fix.corners.total_markets.over_8_5);
  } else if (activeMarketTab === 'cards' && fix.cards?.total_markets?.over_3_5 != null) {
    market = 'Over 3.5 Cards';
    prob = Number(fix.cards.total_markets.over_3_5);
  } else if (activeMarketTab === 'team_goals') {
    const hWin = Number(pred.home_win_probability || 0);
    const aWin = Number(pred.away_win_probability || 0);
    if (hWin >= 0.55) {
      market = 'Home Win';
      prob = hWin;
    } else if (aWin >= 0.55) {
      market = 'Away Win';
      prob = aWin;
    }
  }

  return {
    fixture_id: Number(fix.id),
    match_label: `${fix.home_team?.name || 'Home'} vs ${fix.away_team?.name || 'Away'}`,
    league_name: fix.league?.name || '',
    match_date: fix.match_date || null,
    market,
    model_prob: Math.max(0.05, Math.min(0.95, Number.isFinite(prob) ? prob : 0.7)),
    bookmaker_odds: '',
    baseline_goals: null,
  };
}

