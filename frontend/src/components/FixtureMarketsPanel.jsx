import React from 'react';
import { TABLE_MARKET_TABS, pctVal } from '../utils/marketTabs';

const StatPill = ({ label, prob, expectedText = null, darkMode, highlightThreshold = 75, getPercentageColorClass }) => {
  const pct = pctVal(prob);
  const badgeClass =
    pct === null
      ? darkMode
        ? 'bg-slate-800/80 text-slate-400 border border-slate-700'
        : 'bg-slate-100 text-slate-500 border border-slate-200'
      : getPercentageColorClass
      ? getPercentageColorClass(pct)
      : pct >= highlightThreshold
      ? 'bg-emerald-500 text-slate-950 font-black'
      : 'bg-slate-800 text-slate-200 border border-slate-700';

  return (
    <div
      className={`p-2 rounded-xl border flex flex-col justify-between gap-1 ${
        darkMode ? 'bg-slate-900/80 border-slate-800' : 'bg-slate-50 border-slate-200'
      }`}
    >
      <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400 truncate">
        {label}
      </span>
      <div className="flex items-center justify-between gap-1.5">
        <span className={`px-2 py-0.5 rounded-full text-[11px] font-mono font-bold ${badgeClass}`}>
          {expectedText !== null ? expectedText : pct !== null ? `${pct}%` : '—'}
        </span>
      </div>
    </div>
  );
};

export default function FixtureMarketsPanel({
  fix,
  activeRowTab,
  onChangeRowTab,
  darkMode,
  getPercentageColorClass,
  formatFixed,
  loadingExtra = false,
  compact = false,
  hideTabs = false,
  onOpenFullModal,
}) {
  const pred = fix?.prediction || {};
  const corners = fix?.corners || null;
  const cards = fix?.cards || null;
  const currentTab = !activeRowTab || activeRowTab === 'overview' ? 'goals_ou' : activeRowTab;

  const rowTabs = TABLE_MARKET_TABS.filter((t) => t.id !== 'overview');

  const homeName = fix?.home_team?.name || 'Home';
  const awayName = fix?.away_team?.name || 'Away';

  const bttsYes = pred.btts_probability;
  const bttsNo =
    pred.btts_no_probability !== undefined && pred.btts_no_probability !== null
      ? pred.btts_no_probability
      : bttsYes !== null && bttsYes !== undefined
      ? Math.max(0, 1 - Number(bttsYes))
      : null;

  return (
    <div
      onClick={(e) => e.stopPropagation()}
      className={`rounded-2xl border p-3 space-y-3 transition-colors ${
        darkMode
          ? 'bg-slate-950/90 border-slate-800/90 text-slate-200'
          : 'bg-white border-slate-200 text-slate-800 shadow-sm'
      }`}
    >
      {/* Market Tabs Strip inside the Row */}
      {!hideTabs && (
      <div className="flex items-center justify-between gap-2 flex-wrap border-b border-slate-800/60 pb-2">
        <div className="flex items-center gap-1 flex-wrap">
          {rowTabs.map((tab) => {
            const isSelected = currentTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onChangeRowTab(tab.id);
                }}
                className={`px-2.5 py-1 rounded-lg text-[11px] font-bold transition-all border ${
                  isSelected
                    ? 'bg-emerald-600 text-white border-emerald-400 shadow-sm'
                    : darkMode
                    ? 'bg-slate-900 border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
                    : 'bg-slate-100 border-slate-200 text-slate-600 hover:text-slate-900'
                }`}
              >
                {compact ? tab.shortLabel : tab.label}
              </button>
            );
          })}
        </div>

        {onOpenFullModal && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpenFullModal(fix.id, currentTab === 'corners' ? 'corners' : currentTab === 'cards' ? 'cards' : 'decision_intel');
            }}
            className="text-[11px] font-bold text-emerald-400 hover:text-emerald-300 underline underline-offset-2"
          >
            Full Match Intel &rarr;
          </button>
        )}
      </div>
      )}

      {/* Tab Content */}
      {currentTab === 'goals_ou' && (
        <div className={`grid gap-2 ${compact ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-2 sm:grid-cols-5 lg:grid-cols-9'}`}>
          <StatPill label="Over 0.5 Goals" prob={pred.over_0_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="Over 1.5 Goals" prob={pred.over_1_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="Over 2.5 Goals" prob={pred.over_2_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="Over 3.5 Goals" prob={pred.over_3_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="Over 4.5 Goals" prob={pred.over_4_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="Under 2.5 Goals" prob={pred.under_2_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="BTTS: Yes" prob={bttsYes} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="BTTS: No" prob={bttsNo} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill
            label="Top Scoreline"
            expectedText={pred.most_likely_score || '—'}
            darkMode={darkMode}
            getPercentageColorClass={getPercentageColorClass}
          />
        </div>
      )}

      {currentTab === 'corners' && (
        loadingExtra && !corners ? (
          <div className="py-3 text-center text-xs text-slate-400 animate-pulse">
            Loading Corners probabilities...
          </div>
        ) : !corners ? (
          <div className="py-3 text-center text-xs text-slate-400">
            Corner model predictions are still syncing for this fixture.
          </div>
        ) : (
          <div className={`grid gap-2 ${compact ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-2 sm:grid-cols-4 lg:grid-cols-8'}`}>
            <StatPill
              label="Expected Total"
              expectedText={corners.expected?.total != null ? `${formatFixed(corners.expected.total, 2)} (${formatFixed(corners.expected.home, 1)}-${formatFixed(corners.expected.away, 1)})` : '—'}
              darkMode={darkMode}
              getPercentageColorClass={getPercentageColorClass}
            />
            <StatPill label="Corners O 7.5" prob={corners.total_markets?.over_7_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label="Corners O 8.5" prob={corners.total_markets?.over_8_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label="Corners O 9.5" prob={corners.total_markets?.over_9_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label="Corners O 10.5" prob={corners.total_markets?.over_10_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label="Corners U 9.5" prob={corners.total_markets?.under_9_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label={`${homeName} O 4.5`} prob={corners.home_team?.over_4_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label={`${awayName} O 3.5`} prob={corners.away_team?.over_3_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          </div>
        )
      )}

      {currentTab === 'cards' && (
        loadingExtra && !cards ? (
          <div className="py-3 text-center text-xs text-slate-400 animate-pulse">
            Loading Disciplinary Cards probabilities...
          </div>
        ) : !cards ? (
          <div className="py-3 text-center text-xs text-slate-400">
            Card predictions are still syncing for this fixture.
          </div>
        ) : (
          <div className={`grid gap-2 ${compact ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-2 sm:grid-cols-4 lg:grid-cols-8'}`}>
            <StatPill
              label="Expected Cards"
              expectedText={cards.expected?.total != null ? `${formatFixed(cards.expected.total, 2)} (${formatFixed(cards.expected.home, 1)}-${formatFixed(cards.expected.away, 1)})` : '—'}
              darkMode={darkMode}
              getPercentageColorClass={getPercentageColorClass}
            />
            <StatPill label="Cards O 2.5" prob={cards.total_markets?.over_2_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label="Cards O 3.5" prob={cards.total_markets?.over_3_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label="Cards O 4.5" prob={cards.total_markets?.over_4_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label="Cards O 5.5" prob={cards.total_markets?.over_5_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label="Cards U 4.5" prob={cards.total_markets?.under_4_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label={`${homeName} O 1.5`} prob={cards.home_team?.over_1_5} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
            <StatPill label="Red Card Risk" prob={cards.red_card_risk?.any_red_prob} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          </div>
        )
      )}

      {currentTab === 'halves' && (
        <div className={`grid gap-2 ${compact ? 'grid-cols-2 sm:grid-cols-3' : 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-6'}`}>
          <StatPill
            label="1st Half xG"
            expectedText={pred.first_half_xg != null ? formatFixed(pred.first_half_xg, 2) : '—'}
            darkMode={darkMode}
            getPercentageColorClass={getPercentageColorClass}
          />
          <StatPill label="1st Half Over 0.5" prob={pred.first_half_over_0_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="1st Half Over 1.5" prob={pred.first_half_over_1_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill
            label="2nd Half xG"
            expectedText={pred.second_half_xg != null ? formatFixed(pred.second_half_xg, 2) : '—'}
            darkMode={darkMode}
            getPercentageColorClass={getPercentageColorClass}
          />
          <StatPill label="2nd Half Over 0.5" prob={pred.second_half_over_0_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="2nd Half Over 1.5" prob={pred.second_half_over_1_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
        </div>
      )}

      {currentTab === 'team_goals' && (
        <div className={`grid gap-2 ${compact ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-2 sm:grid-cols-4 lg:grid-cols-9'}`}>
          <StatPill label={`${homeName} Win (1)`} prob={pred.home_win_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label="Draw (X)" prob={pred.draw_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label={`${awayName} Win (2)`} prob={pred.away_win_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label={`${homeName} O 0.5`} prob={pred.home_over_0_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label={`${homeName} O 1.5`} prob={pred.home_over_1_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label={`${homeName} O 2.5`} prob={pred.home_over_2_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label={`${awayName} O 0.5`} prob={pred.away_over_0_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label={`${awayName} O 1.5`} prob={pred.away_over_1_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
          <StatPill label={`${awayName} O 2.5`} prob={pred.away_over_2_5_probability} darkMode={darkMode} getPercentageColorClass={getPercentageColorClass} />
        </div>
      )}
    </div>
  );
}
