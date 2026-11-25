const CLIENT_ID_STORAGE_KEY = 'sgp_client_id';
const ADMIN_KEY_STORAGE_KEY = 'sgp_admin_key';

function generateRandomProfileCode() {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const pick = (len) => {
    let out = '';
    for (let i = 0; i < len; i += 1) {
      out += alphabet[Math.floor(Math.random() * alphabet.length)];
    }
    return out;
  };
  return `SGP-${pick(4)}-${pick(4)}`;
}

export function sanitizeClientId(raw) {
  if (!raw) return '';
  const cleaned = String(raw).trim().replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64);
  return cleaned.length >= 4 ? cleaned : '';
}

export function getOrCreateClientId() {
  if (typeof window === 'undefined' || !window.localStorage) {
    return 'public';
  }
  try {
    const existing = sanitizeClientId(window.localStorage.getItem(CLIENT_ID_STORAGE_KEY));
    if (existing) return existing;
    const created = generateRandomProfileCode();
    window.localStorage.setItem(CLIENT_ID_STORAGE_KEY, created);
    return created;
  } catch {
    return 'public';
  }
}

export function setCustomClientId(nextCode) {
  const cleaned = sanitizeClientId(nextCode);
  if (!cleaned) return null;
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(CLIENT_ID_STORAGE_KEY, cleaned);
    }
  } catch {
    // Ignore storage quota errors
  }
  return cleaned;
}

export function getStoredAdminKey() {
  const envKey = (typeof import.meta !== 'undefined' && import.meta.env?.VITE_ADMIN_API_KEY) || '';
  if (envKey) return String(envKey).trim();
  if (typeof window === 'undefined' || !window.localStorage) return '';
  try {
    return String(window.localStorage.getItem(ADMIN_KEY_STORAGE_KEY) || '').trim();
  } catch {
    return '';
  }
}

export function saveStoredAdminKey(key) {
  if (typeof window === 'undefined' || !window.localStorage) return;
  try {
    const trimmed = String(key || '').trim();
    if (trimmed) {
      window.localStorage.setItem(ADMIN_KEY_STORAGE_KEY, trimmed);
    } else {
      window.localStorage.removeItem(ADMIN_KEY_STORAGE_KEY);
    }
  } catch {
    // Ignore storage errors
  }
}

export function readUrlViewState() {
  if (typeof window === 'undefined') {
    return { tab: null, day: null, league: null, matchId: null, liveId: null };
  }
  try {
    const params = new URLSearchParams(window.location.search);
    const tabRaw = params.get('tab');
    const tab = tabRaw && ['upcoming', 'live', 'finished'].includes(tabRaw) ? tabRaw : null;
    const day = params.get('day') || null;
    const league = params.get('league') || null;
    const mNum = Number(params.get('match'));
    const lNum = Number(params.get('live'));
    return {
      tab,
      day,
      league,
      matchId: Number.isFinite(mNum) && mNum > 0 ? mNum : null,
      liveId: Number.isFinite(lNum) && lNum > 0 ? lNum : null,
    };
  } catch {
    return { tab: null, day: null, league: null, matchId: null, liveId: null };
  }
}

export function writeUrlViewState({ tab, day, league, matchId, liveId }, push = false) {
  if (typeof window === 'undefined' || !window.history) return;
  try {
    const params = new URLSearchParams();
    if (tab && tab !== 'upcoming') params.set('tab', tab);
    if (day && day !== 'ALL_DAYS') params.set('day', day);
    if (league && league !== 'ALL') params.set('league', league);
    if (matchId) params.set('match', String(matchId));
    else if (liveId) params.set('live', String(liveId));

    const qs = params.toString();
    const nextUrl = `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash || ''}`;
    const curUrl = `${window.location.pathname}${window.location.search}${window.location.hash || ''}`;
    if (nextUrl === curUrl) return;
    if (push) {
      window.history.pushState({ sgpModal: Boolean(matchId || liveId) }, '', nextUrl);
    } else {
      window.history.replaceState({ sgpModal: Boolean(matchId || liveId) }, '', nextUrl);
    }
  } catch {
    // Ignore history API restrictions
  }
}
