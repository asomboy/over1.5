export const TABLE_MARKET_TABS = [
  { id: 'overview', label: 'All / O1.5', shortLabel: 'Overview' },
  { id: 'goals_ou', label: 'Over/Under & BTTS', shortLabel: 'O1.5 / O2.5' },
  { id: 'corners', label: 'Corners', shortLabel: 'Corners' },
  { id: 'cards', label: 'Cards', shortLabel: 'Cards' },
  { id: 'halves', label: '1H / 2H Halves', shortLabel: 'Halves' },
  { id: 'team_goals', label: 'Team Goals & 1X2', shortLabel: 'Teams / 1X2' },
];

export const pctVal = (prob) => {
  if (prob === null || prob === undefined || Number.isNaN(Number(prob))) return null;
  return Math.round(Number(prob) * 100);
};

