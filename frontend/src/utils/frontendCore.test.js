import assert from 'node:assert/strict';
import test from 'node:test';
import {
  canonicalizeMarket,
  formatBookmakerSlipText,
  getBookmakerSearchUrl,
  translateMarketForBookmaker,
} from './bookmakerMarkets.js';
import { sanitizeClientId } from './clientSession.js';
import { TABLE_MARKET_TABS, pctVal } from './marketTabs.js';
import { DEFAULT_ALERT_SETTINGS, evaluateClientLiveAlert, extractQuickSlipLeg } from './alertUtils.js';

test('translateMarketForBookmaker maps SportyBet and Bet9ja labels accurately', () => {
  const sb = translateMarketForBookmaker('Over 1.5 Goals', 'sportybet');
  assert.equal(sb.canonical, 'over_1_5_goals');
  assert.equal(sb.section, 'Goals');
  assert.equal(sb.market, 'Over/Under');
  assert.equal(sb.pick, 'Over 1.5');

  const b9 = translateMarketForBookmaker('BTTS Yes', 'bet9ja');
  assert.equal(b9.canonical, 'btts_yes');
  assert.equal(b9.market, 'Goal / No Goal');
  assert.equal(b9.pick, 'GG (Yes)');

  const dc = translateMarketForBookmaker('Double Chance 1X', 'sportybet');
  assert.equal(dc.canonical, 'dc_1x');
  assert.equal(dc.pick, '1X (Home or Draw)');
});

test('getBookmakerSearchUrl and formatBookmakerSlipText format clean URLs and share tickets', () => {
  const sbUrl = getBookmakerSearchUrl('sportybet', 'Arsenal', 'Chelsea');
  assert.match(sbUrl, /sportybet\.com.*Arsenal%20Chelsea/);

  const b9Url = getBookmakerSearchUrl('bet9ja', 'Real Madrid', 'Barcelona');
  assert.match(b9Url, /bet9ja\.com.*Real%20Madrid%20Barcelona/);

  const txt = formatBookmakerSlipText({
    bookmaker: 'sportybet',
    title: 'Weekend Safe',
    bookingCode: 'sb99x',
    combinedOdds: 2.34,
    legs: [{ match_label: 'Arsenal vs Chelsea', league_name: 'Premier League', market: 'Over 1.5 Goals', model_prob: 0.82 }],
  });
  assert.match(txt, /Booking Code: SB99X/);
  assert.match(txt, /Goals -> Over\/Under -> Over 1\.5/);
});

test('sanitizeClientId enforces alphanumeric length bounds and strips unsafe chars', () => {
  assert.equal(sanitizeClientId('ab'), '');
  assert.equal(sanitizeClientId('SGP-8F3K-29XA!@#'), 'SGP-8F3K-29XA');
  assert.equal(canonicalizeMarket('1H Over 0.5 Goals'), 'ht_over_0_5_goals');
});

test('marketTabs pctVal and quick slip extraction handle live and pre-match fixtures', () => {
  assert.equal(TABLE_MARKET_TABS.length >= 5, true);
  assert.equal(pctVal(0.784), 78);
  assert.equal(pctVal(null), null);

  const fix = {
    id: 42,
    match_date: '2026-11-25T19:45:00Z',
    league: { name: 'Premier League' },
    home_team: { name: 'Liverpool' },
    away_team: { name: 'Arsenal' },
    prediction: { over_1_5_probability: 0.81, over_2_5_probability: 0.61 },
  };
  const leg = extractQuickSlipLeg(fix, 'overview', null);
  assert.equal(leg.fixture_id, 42);
  assert.equal(leg.market, 'Over 1.5 Goals');
  assert.equal(leg.model_prob, 0.81);
});

test('evaluateClientLiveAlert enforces probability, confidence, and watchlist filters', () => {
  const liveFix = {
    fixture_id: 101,
    home_team: 'Bayern Munich',
    away_team: 'Dortmund',
    league_name: 'Bundesliga',
    minute: 62,
    home_score: 1,
    away_score: 1,
    confidence: 82,
    best_signal: {
      category: 'goals',
      signal_strength: 'STRONG',
      market: 'At Least 1 More Goal',
      probability: 0.79,
      time_remaining_minutes: 28,
    },
  };
  const hit = evaluateClientLiveAlert(
    liveFix,
    { ...DEFAULT_ALERT_SETTINGS, watchlistOnlyAlerts: false },
    new Set()
  );
  assert.ok(hit);
  assert.equal(hit.fixtureId, 101);

  const blockedByWatchlist = evaluateClientLiveAlert(
    liveFix,
    { ...DEFAULT_ALERT_SETTINGS, watchlistOnlyAlerts: true },
    new Set([999])
  );
  assert.equal(blockedByWatchlist, null);
});
