export const BOOKMAKER_META = {
  general: {
    id: 'general',
    name: 'General / Multi-Book',
    shortName: 'General',
    sportsbookUrl: null,
    accentClass: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
    bookingNote: 'Track selections using model fair odds or custom sportsbook odds.',
  },
  sportybet: {
    id: 'sportybet',
    name: 'SportyBet (NG)',
    shortName: 'SportyBet',
    sportsbookUrl: 'https://www.sportybet.com/ng/sport/football',
    accentClass: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
    bookingNote:
      'Add these picks on SportyBet, tap Share / Book to generate your Booking Code, and save that code on your Paper Ledger slip.',
  },
  bet9ja: {
    id: 'bet9ja',
    name: 'Bet9ja',
    shortName: 'Bet9ja',
    sportsbookUrl: 'https://web.bet9ja.com/Sport/Default.aspx',
    accentClass: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
    bookingNote:
      'Add these picks on Bet9ja, tap Book to generate your Bet9ja Code, and attach it on your Paper Ledger slip.',
  },
};

export const DISPLAY_TO_CANONICAL = {
  'over 0.5': 'over_0_5_goals',
  'over 0.5 goals': 'over_0_5_goals',
  'over 1.5': 'over_1_5_goals',
  'over 1.5 goals': 'over_1_5_goals',
  'full match over 1.5': 'over_1_5_goals',
  'full match over 1.5 goals': 'over_1_5_goals',
  'live o1.5': 'over_1_5_goals',
  'over 2.5': 'over_2_5_goals',
  'over 2.5 goals': 'over_2_5_goals',
  'full match over 2.5': 'over_2_5_goals',
  'full match over 2.5 goals': 'over_2_5_goals',
  'live o2.5': 'over_2_5_goals',
  'over 3.5': 'over_3_5_goals',
  'over 3.5 goals': 'over_3_5_goals',
  'full match over 3.5': 'over_3_5_goals',
  'under 2.5': 'under_2_5_goals',
  'under 2.5 goals': 'under_2_5_goals',
  'under 3.5': 'under_3_5_goals',
  'under 3.5 goals': 'under_3_5_goals',
  'btts yes': 'btts_yes',
  btts: 'btts_yes',
  'both teams to score': 'btts_yes',
  'btts no': 'btts_no',
  'home win': '1x2_home',
  '1': '1x2_home',
  draw: '1x2_draw',
  x: '1x2_draw',
  'away win': '1x2_away',
  '2': '1x2_away',
  '1x': 'dc_1x',
  'dc 1x': 'dc_1x',
  'double chance 1x': 'dc_1x',
  'home or draw': 'dc_1x',
  x2: 'dc_x2',
  'dc x2': 'dc_x2',
  'double chance x2': 'dc_x2',
  'draw or away': 'dc_x2',
  '12': 'dc_12',
  'dc 12': 'dc_12',
  'double chance 12': 'dc_12',
  'home or away': 'dc_12',
  '1h over 0.5': 'ht_over_0_5_goals',
  '1h over 0.5 goals': 'ht_over_0_5_goals',
  'over 7.5 corners': 'over_7_5_corners',
  'over 8.5 corners': 'over_8_5_corners',
  'over 9.5 corners': 'over_9_5_corners',
  'over 10.5 corners': 'over_10_5_corners',
  'over 2.5 cards': 'over_2_5_cards',
  'over 3.5 cards': 'over_3_5_cards',
  'over 4.5 cards': 'over_4_5_cards',
  'at least 1 more goal': 'at_least_1_more_goal',
};

export const MARKET_TRANSLATIONS = {
  sportybet: {
    over_0_5_goals: { section: 'Goals', market: 'Over/Under', pick: 'Over 0.5', hint: 'Goals -> Over/Under -> Over 0.5' },
    over_1_5_goals: { section: 'Goals', market: 'Over/Under', pick: 'Over 1.5', hint: 'Goals -> Over/Under -> Over 1.5' },
    over_2_5_goals: { section: 'Goals', market: 'Over/Under', pick: 'Over 2.5', hint: 'Goals -> Over/Under -> Over 2.5' },
    over_3_5_goals: { section: 'Goals', market: 'Over/Under', pick: 'Over 3.5', hint: 'Goals -> Over/Under -> Over 3.5' },
    under_2_5_goals: { section: 'Goals', market: 'Over/Under', pick: 'Under 2.5', hint: 'Goals -> Over/Under -> Under 2.5' },
    under_3_5_goals: { section: 'Goals', market: 'Over/Under', pick: 'Under 3.5', hint: 'Goals -> Over/Under -> Under 3.5' },
    btts_yes: { section: 'Goals', market: 'GG/NG', pick: 'Yes (GG)', hint: 'Goals -> GG/NG -> Yes' },
    btts_no: { section: 'Goals', market: 'GG/NG', pick: 'No (NG)', hint: 'Goals -> GG/NG -> No' },
    '1x2_home': { section: 'Main', market: '1X2', pick: 'Home (1)', hint: 'Main -> 1X2 -> Home' },
    '1x2_draw': { section: 'Main', market: '1X2', pick: 'Draw (X)', hint: 'Main -> 1X2 -> Draw' },
    '1x2_away': { section: 'Main', market: '1X2', pick: 'Away (2)', hint: 'Main -> 1X2 -> Away' },
    dc_1x: { section: 'Main', market: 'Double Chance', pick: '1X (Home or Draw)', hint: 'Main -> Double Chance -> 1X' },
    dc_x2: { section: 'Main', market: 'Double Chance', pick: 'X2 (Draw or Away)', hint: 'Main -> Double Chance -> X2' },
    dc_12: { section: 'Main', market: 'Double Chance', pick: '12 (Home or Away)', hint: 'Main -> Double Chance -> 12' },
    ht_over_0_5_goals: { section: 'Half', market: '1st Half - Over/Under', pick: 'Over 0.5', hint: 'Half -> 1st Half O/U -> Over 0.5' },
    over_7_5_corners: { section: 'Corners', market: 'Corners - Over/Under', pick: 'Over 7.5', hint: 'Corners -> Over/Under -> Over 7.5' },
    over_8_5_corners: { section: 'Corners', market: 'Corners - Over/Under', pick: 'Over 8.5', hint: 'Corners -> Over/Under -> Over 8.5' },
    over_9_5_corners: { section: 'Corners', market: 'Corners - Over/Under', pick: 'Over 9.5', hint: 'Corners -> Over/Under -> Over 9.5' },
    over_10_5_corners: { section: 'Corners', market: 'Corners - Over/Under', pick: 'Over 10.5', hint: 'Corners -> Over/Under -> Over 10.5' },
    over_2_5_cards: { section: 'Cards', market: 'Bookings - Over/Under', pick: 'Over 2.5', hint: 'Cards -> Bookings O/U -> Over 2.5' },
    over_3_5_cards: { section: 'Cards', market: 'Bookings - Over/Under', pick: 'Over 3.5', hint: 'Cards -> Bookings O/U -> Over 3.5' },
    over_4_5_cards: { section: 'Cards', market: 'Bookings - Over/Under', pick: 'Over 4.5', hint: 'Cards -> Bookings O/U -> Over 4.5' },
    at_least_1_more_goal: {
      section: 'Live Goals',
      market: 'Next Goal / Total Goals Over Line',
      pick: '+1 More Goal',
      hint: 'Live -> Total Goals -> Over Current + 0.5',
    },
  },
  bet9ja: {
    over_0_5_goals: { section: 'Goals', market: 'Over/Under 0.5', pick: 'Over (0.5)', hint: 'Goals -> O/U 0.5 -> Over' },
    over_1_5_goals: { section: 'Goals', market: 'Over/Under 1.5', pick: 'Over (1.5)', hint: 'Goals -> O/U 1.5 -> Over' },
    over_2_5_goals: { section: 'Goals', market: 'Over/Under 2.5', pick: 'Over (2.5)', hint: 'Goals -> O/U 2.5 -> Over' },
    over_3_5_goals: { section: 'Goals', market: 'Over/Under 3.5', pick: 'Over (3.5)', hint: 'Goals -> O/U 3.5 -> Over' },
    under_2_5_goals: { section: 'Goals', market: 'Over/Under 2.5', pick: 'Under (2.5)', hint: 'Goals -> O/U 2.5 -> Under' },
    under_3_5_goals: { section: 'Goals', market: 'Over/Under 3.5', pick: 'Under (3.5)', hint: 'Goals -> O/U 3.5 -> Under' },
    btts_yes: { section: 'Goals', market: 'Goal / No Goal', pick: 'GG (Yes)', hint: 'Goals -> GG/NG -> GG' },
    btts_no: { section: 'Goals', market: 'Goal / No Goal', pick: 'NG (No)', hint: 'Goals -> GG/NG -> NG' },
    '1x2_home': { section: 'Main', market: '1X2', pick: '1 (Home)', hint: 'Main -> 1X2 -> 1' },
    '1x2_draw': { section: 'Main', market: '1X2', pick: 'X (Draw)', hint: 'Main -> 1X2 -> X' },
    '1x2_away': { section: 'Main', market: '1X2', pick: '2 (Away)', hint: 'Main -> 1X2 -> 2' },
    dc_1x: { section: 'Main', market: 'Double Chance', pick: '1X', hint: 'Main -> DC -> 1X' },
    dc_x2: { section: 'Main', market: 'Double Chance', pick: 'X2', hint: 'Main -> DC -> X2' },
    dc_12: { section: 'Main', market: 'Double Chance', pick: '12', hint: 'Main -> DC -> 12' },
    ht_over_0_5_goals: { section: '1st Half', market: '1st Half O/U 0.5', pick: 'Over (0.5)', hint: '1st Half -> O/U 0.5 -> Over' },
    over_7_5_corners: { section: 'Corners', market: 'Total Corners O/U', pick: 'Over 7.5', hint: 'Corners -> Total Corners -> Over 7.5' },
    over_8_5_corners: { section: 'Corners', market: 'Total Corners O/U', pick: 'Over 8.5', hint: 'Corners -> Total Corners -> Over 8.5' },
    over_9_5_corners: { section: 'Corners', market: 'Total Corners O/U', pick: 'Over 9.5', hint: 'Corners -> Total Corners -> Over 9.5' },
    over_10_5_corners: { section: 'Corners', market: 'Total Corners O/U', pick: 'Over 10.5', hint: 'Corners -> Total Corners -> Over 10.5' },
    over_2_5_cards: { section: 'Cards', market: 'Total Cards O/U', pick: 'Over 2.5', hint: 'Cards -> Total Cards -> Over 2.5' },
    over_3_5_cards: { section: 'Cards', market: 'Total Cards O/U', pick: 'Over 3.5', hint: 'Cards -> Total Cards -> Over 3.5' },
    over_4_5_cards: { section: 'Cards', market: 'Total Cards O/U', pick: 'Over 4.5', hint: 'Cards -> Total Cards -> Over 4.5' },
    at_least_1_more_goal: {
      section: 'Live Goals',
      market: 'Live Total Goals Over Line',
      pick: '+1 More Goal',
      hint: 'Live -> Total Goals -> Over Current + 0.5',
    },
  },
};

export function canonicalizeMarket(marketName) {
  const raw = String(marketName || 'Over 1.5 Goals').trim().toLowerCase();
  return DISPLAY_TO_CANONICAL[raw] || raw.replace(/\s+/g, '_');
}

export function translateMarketForBookmaker(marketName, bookmaker = 'sportybet') {
  const bm = bookmaker === 'bet9ja' ? 'bet9ja' : 'sportybet';
  const raw = String(marketName || 'Over 1.5 Goals').trim();
  const canonical = canonicalizeMarket(raw);
  const found = MARKET_TRANSLATIONS[bm]?.[canonical];
  if (found) {
    return { canonical, ...found };
  }
  return {
    canonical,
    section: 'Match Markets',
    market: raw,
    pick: raw,
    hint: `${BOOKMAKER_META[bm].shortName} -> ${raw}`,
  };
}

export function getBookmakerSearchUrl(bookmaker, homeTeam, awayTeam, matchLabel = '') {
  const bm = bookmaker === 'bet9ja' ? 'bet9ja' : 'sportybet';
  const queryText = [homeTeam, awayTeam].filter(Boolean).join(' ').trim() || matchLabel || 'football';
  const q = encodeURIComponent(queryText);
  if (bm === 'bet9ja') {
    return `https://web.bet9ja.com/Sport/Default.aspx?Search=${q}`;
  }
  return `https://www.sportybet.com/ng/sport/football?search=${q}`;
}

export function formatBookmakerSlipText({ bookmaker = 'sportybet', title = '', legs = [], combinedOdds = 1.0, bookingCode = '' }) {
  const meta = BOOKMAKER_META[bookmaker] || BOOKMAKER_META.sportybet;
  const lines = [`⚽ ${meta.name.toUpperCase()} SLIP${title ? ` — ${title.toUpperCase()}` : ''}`];
  if (bookingCode) {
    lines.push(`Booking Code: ${String(bookingCode).toUpperCase()}`);
  }
  lines.push(`Combined Odds: ${combinedOdds}x`);
  lines.push('');
  (legs || []).forEach((leg, idx) => {
    const label = leg.match_label || leg.match || `Fixture #${leg.fixture_id}`;
    const league = leg.league_name || leg.league || '';
    const mkt = leg.placement_hint || translateMarketForBookmaker(leg.market, bookmaker).hint;
    const probPct = leg.probability ?? (leg.model_prob ? Math.round(Number(leg.model_prob) * 1000) / 10 : null);
    lines.push(`${idx + 1}. ${label}${league ? ` (${league})` : ''}`);
    lines.push(`   ${mkt}${probPct ? ` · Model ${probPct}%` : ''}`);
  });
  return lines.join('\n');
}

