import React from 'react';
import { Activity, Calculator, Radio, Trophy, Zap } from 'lucide-react';

export default function MobileBottomNav({
  activeTab,
  onSelectTab,
  onOpenBetSlips,
  onOpenSmartAccas,
  upcomingCount = 0,
  liveCount = 0,
  draftSlipCount = 0,
  darkMode = true,
}) {
  const barCls = darkMode
    ? 'bg-slate-950/95 border-slate-800 text-slate-300'
    : 'bg-white/95 border-slate-200 text-slate-700';

  return (
    <nav
      aria-label="Mobile Primary Navigation"
      className={`sm:hidden fixed bottom-0 inset-x-0 z-40 border-t backdrop-blur-lg px-2 py-1.5 shadow-2xl ${barCls}`}
    >
      <div className="grid grid-cols-5 gap-1 max-w-md mx-auto">
        <button
          type="button"
          onClick={() => onSelectTab('upcoming')}
          className={`flex flex-col items-center justify-center py-1 rounded-xl text-[10px] font-extrabold transition-all ${
            activeTab === 'upcoming' ? 'text-emerald-400 bg-emerald-500/10' : 'opacity-75 hover:opacity-100'
          }`}
        >
          <div className="relative">
            <Activity className="w-4 h-4" />
            {upcomingCount > 0 && (
              <span className="absolute -top-1.5 -right-3 px-1 rounded-full text-[8px] font-black bg-emerald-500 text-slate-950">
                {upcomingCount > 99 ? '99+' : upcomingCount}
              </span>
            )}
          </div>
          <span className="mt-0.5">Fixtures</span>
        </button>

        <button
          type="button"
          onClick={() => onSelectTab('live')}
          className={`flex flex-col items-center justify-center py-1 rounded-xl text-[10px] font-extrabold transition-all ${
            activeTab === 'live' ? 'text-rose-400 bg-rose-500/10' : 'opacity-75 hover:opacity-100'
          }`}
        >
          <div className="relative">
            <Radio className={`w-4 h-4 ${liveCount > 0 ? 'text-rose-400 animate-pulse' : ''}`} />
            {liveCount > 0 && (
              <span className="absolute -top-1.5 -right-2.5 px-1 rounded-full text-[8px] font-black bg-rose-500 text-white">
                {liveCount}
              </span>
            )}
          </div>
          <span className="mt-0.5">Live</span>
        </button>

        <button
          type="button"
          onClick={onOpenBetSlips}
          className="flex flex-col items-center justify-center py-1 rounded-xl text-[10px] font-extrabold text-amber-400 bg-amber-500/10 border border-amber-500/25"
        >
          <div className="relative">
            <Calculator className="w-4 h-4" />
            {draftSlipCount > 0 && (
              <span className="absolute -top-1.5 -right-2.5 px-1 rounded-full text-[8px] font-black bg-amber-400 text-slate-950">
                {draftSlipCount}
              </span>
            )}
          </div>
          <span className="mt-0.5">Bet Slips</span>
        </button>

        <button
          type="button"
          onClick={onOpenSmartAccas}
          className="flex flex-col items-center justify-center py-1 rounded-xl text-[10px] font-extrabold opacity-85 hover:opacity-100"
        >
          <Zap className="w-4 h-4 text-cyan-400" />
          <span className="mt-0.5">Accas</span>
        </button>

        <button
          type="button"
          onClick={() => onSelectTab('finished')}
          className={`flex flex-col items-center justify-center py-1 rounded-xl text-[10px] font-extrabold transition-all ${
            activeTab === 'finished' ? 'text-emerald-400 bg-emerald-500/10' : 'opacity-75 hover:opacity-100'
          }`}
        >
          <Trophy className="w-4 h-4 text-amber-400" />
          <span className="mt-0.5">Results</span>
        </button>
      </div>
    </nav>
  );
}
