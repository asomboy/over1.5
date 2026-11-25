import React, { useEffect, useRef, useState } from 'react';
import {
  Activity,
  BarChart2,
  BellRing,
  Calculator,
  Check,
  Grid,
  KeyRound,
  List,
  Lock,
  Moon,
  RefreshCw,
  SlidersHorizontal,
  Sun,
  Unlock,
  Zap,
} from 'lucide-react';
import { getStoredAdminKey, saveStoredAdminKey } from '../utils/clientSession';

export default function AppHeaderBar({
  darkMode,
  onToggleDarkMode,
  viewMode,
  onChangeViewMode,
  backendHealth,
  draftSlipCount = 0,
  browserPushEnabled = false,
  syncing = false,
  onOpenBetSlips,
  onOpenAlerts,
  onOpenAccas,
  onOpenModelDashboard,
  onSyncData,
  apiRequest,
  onNotifyBanner,
}) {
  const [toolsOpen, setToolsOpen] = useState(false);
  const [adminInput, setAdminInput] = useState(() => getStoredAdminKey());
  const [adminVerified, setAdminVerified] = useState(() => Boolean(getStoredAdminKey()));
  const [verifyingKey, setVerifyingKey] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    const handleOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setToolsOpen(false);
      }
    };
    if (toolsOpen) {
      document.addEventListener('mousedown', handleOutside);
    }
    return () => document.removeEventListener('mousedown', handleOutside);
  }, [toolsOpen]);

  const handleSaveAdminKey = async () => {
    const trimmed = adminInput.trim();
    if (!trimmed) {
      saveStoredAdminKey('');
      setAdminVerified(false);
      if (onNotifyBanner) {
        onNotifyBanner({ type: 'success', message: 'Admin key cleared from this browser.' });
      }
      return;
    }
    setVerifyingKey(true);
    saveStoredAdminKey(trimmed);
    try {
      const res = await apiRequest('get', '/api/admin/verify', null, {
        headers: { 'X-Admin-Key': trimmed },
        timeout: 12000,
      });
      if (res?.data?.authenticated) {
        setAdminVerified(true);
        if (onNotifyBanner) {
          onNotifyBanner({ type: 'success', message: 'Admin key verified — Sync & Operations unlocked!' });
        }
      }
    } catch (err) {
      const status = err?.response?.status;
      if (status === 401) {
        saveStoredAdminKey('');
        setAdminVerified(false);
        if (onNotifyBanner) {
          onNotifyBanner({ type: 'error', message: 'Invalid Admin API Key.' });
        }
      } else if (status === 503) {
        setAdminVerified(false);
        if (onNotifyBanner) {
          onNotifyBanner({ type: 'error', message: 'ADMIN_API_KEY is not configured on the production server.' });
        }
      } else {
        setAdminVerified(true);
      }
    } finally {
      setVerifyingKey(false);
    }
  };

  const isLocalDev =
    typeof window !== 'undefined' &&
    (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1');

  return (
    <header
      className={`border-b sticky top-0 z-40 backdrop-blur-md transition-colors ${
        darkMode ? 'border-slate-800/80 bg-slate-950/85' : 'border-slate-200 bg-white/90'
      }`}
    >
      <div className="max-w-7xl mx-auto px-3 sm:px-6 lg:px-8 h-14 sm:h-16 flex items-center justify-between gap-2">
        {/* Brand */}
        <div className="flex items-center space-x-2 sm:space-x-3 shrink-0">
          <div className="p-1.5 sm:p-2 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-emerald-500 shrink-0">
            <Activity className="w-4 h-4 sm:w-6 sm:h-6 animate-pulse" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 sm:gap-2">
              <span
                className={`font-black text-xs sm:text-base md:text-xl tracking-tight leading-tight uppercase ${
                  darkMode
                    ? 'bg-gradient-to-r from-white via-slate-200 to-emerald-400 bg-clip-text text-transparent'
                    : 'text-slate-900'
                }`}
              >
                <span className="block sm:inline">Soccer</span>
                <span className="sm:ml-1 text-emerald-500">GoalPredictor</span>
              </span>
              <span className="hidden lg:inline-block text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-500 border border-emerald-500/30 whitespace-nowrap">
                GMT+1 Live Feed
              </span>
            </div>
            <p className={`text-[11px] hidden lg:block ${darkMode ? 'text-slate-400' : 'text-slate-500'}`}>
              Calibrated Dixon-Coles Match Intelligence &amp; Multi-Market Analytics
            </p>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center space-x-1.5 sm:space-x-2.5 shrink-0">
          {/* View Mode Toggle */}
          <div
            className={`flex items-center p-0.5 sm:p-1 rounded-xl border ${
              darkMode ? 'bg-slate-900 border-slate-800' : 'bg-slate-100 border-slate-200'
            }`}
          >
            <button
              type="button"
              onClick={() => onChangeViewMode('table')}
              className={`p-1 sm:p-1.5 rounded-lg text-xs font-semibold flex items-center gap-1 transition-all ${
                viewMode === 'table'
                  ? 'bg-emerald-600 text-white shadow-sm'
                  : darkMode
                  ? 'text-slate-400 hover:text-white'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
              title="Compact Table / List View"
            >
              <List className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
              <span className="hidden md:inline">List</span>
            </button>
            <button
              type="button"
              onClick={() => onChangeViewMode('grid')}
              className={`p-1 sm:p-1.5 rounded-lg text-xs font-semibold flex items-center gap-1 transition-all ${
                viewMode === 'grid'
                  ? 'bg-emerald-600 text-white shadow-sm'
                  : darkMode
                  ? 'text-slate-400 hover:text-white'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
              title="Grid Cards View"
            >
              <Grid className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
              <span className="hidden md:inline">Grid</span>
            </button>
          </div>

          {/* API Health */}
          <div
            className={`flex items-center space-x-1.5 px-2 sm:px-3 py-1 sm:py-1.5 rounded-full text-[10px] sm:text-xs font-semibold border transition-all ${
              backendHealth.online
                ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
                : 'bg-rose-500/10 text-rose-500 border-rose-500/30'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 sm:w-2 sm:h-2 rounded-full ${
                backendHealth.online ? 'bg-emerald-400 animate-ping' : 'bg-rose-400'
              }`}
            />
            <span className="hidden md:inline">
              {backendHealth.online ? `Live (${backendHealth.latency}ms)` : 'Offline'}
            </span>
          </div>

          {/* Primary Desktop Buttons (Mobile uses Sticky Bottom Tab Bar) */}
          <button
            type="button"
            onClick={onOpenBetSlips}
            className="hidden sm:flex items-center space-x-1.5 px-3 py-1.5 text-xs font-extrabold bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/40 text-amber-500 dark:text-amber-300 rounded-xl transition-all shadow-sm active:scale-95"
            title="Open SportyBet & Bet9ja Slip Builder, +EV Calculator & Paper Ledger"
          >
            <Calculator className="w-3.5 h-3.5 text-amber-400" />
            <span>Bet Slips</span>
            {draftSlipCount > 0 && (
              <span className="px-1.5 py-0.2 rounded-full text-[10px] font-black bg-amber-500 text-slate-950">
                {draftSlipCount}
              </span>
            )}
          </button>

          <button
            type="button"
            onClick={onOpenAccas}
            className="hidden sm:flex items-center space-x-1.5 px-3 py-1.5 text-xs font-bold bg-gradient-to-r from-emerald-600 to-cyan-600 hover:from-emerald-500 hover:to-cyan-500 text-white rounded-xl transition-all shadow-md active:scale-95 border border-emerald-400/30"
            title="Generate Smart Accumulators"
          >
            <Zap className="w-3.5 h-3.5 text-amber-300 animate-pulse" />
            <span>Smart Accas</span>
          </button>

          <button
            type="button"
            onClick={onOpenAlerts}
            className={`p-1.5 sm:px-2.5 sm:py-1.5 text-xs font-bold rounded-xl border flex items-center gap-1 transition-all ${
              darkMode
                ? 'bg-slate-900 hover:bg-slate-800 border-slate-800 text-cyan-300'
                : 'bg-slate-100 hover:bg-slate-200 border-slate-300 text-cyan-700'
            }`}
            title="Live Browser Push, Audio Chime & Telegram Alert Preferences"
          >
            <BellRing className={`w-3.5 h-3.5 ${browserPushEnabled ? 'text-emerald-500' : 'text-cyan-500'}`} />
            <span className="hidden lg:inline">Alerts</span>
          </button>

          {/* Theme Toggle */}
          <button
            type="button"
            onClick={onToggleDarkMode}
            className={`p-1.5 sm:p-2 rounded-xl border transition-all ${
              darkMode
                ? 'bg-slate-900 border-slate-800 text-amber-400 hover:bg-slate-800'
                : 'bg-slate-100 border-slate-300 text-slate-700 hover:bg-slate-200'
            }`}
            title={darkMode ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
          >
            {darkMode ? <Sun className="w-3.5 h-3.5 sm:w-4 sm:h-4" /> : <Moon className="w-3.5 h-3.5 sm:w-4 sm:h-4" />}
          </button>

          {/* System Tools, Model Intel & Admin Key Popover */}
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setToolsOpen((v) => !v)}
              className={`p-1.5 sm:px-3 sm:py-1.5 rounded-xl border text-xs font-extrabold flex items-center gap-1.5 transition-all ${
                darkMode
                  ? 'bg-slate-900 hover:bg-slate-800 border-slate-800 text-slate-200'
                  : 'bg-slate-100 hover:bg-slate-200 border-slate-300 text-slate-800'
              }`}
              title="Model Intelligence, Dataset Sync & Admin Key"
            >
              <SlidersHorizontal className="w-3.5 h-3.5 text-cyan-500" />
              <span className="hidden md:inline">Tools</span>
              {adminVerified && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />}
            </button>

            {toolsOpen && (
              <div
                className={`absolute right-0 mt-2 w-72 rounded-2xl border shadow-2xl p-3.5 space-y-3 z-50 ${
                  darkMode ? 'bg-slate-900 border-slate-800 text-white' : 'bg-white border-slate-200 text-slate-900'
                }`}
              >
                <div className="space-y-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      setToolsOpen(false);
                      onOpenModelDashboard();
                    }}
                    className={`w-full px-3 py-2 rounded-xl border text-xs font-extrabold flex items-center justify-between transition-all ${
                      darkMode
                        ? 'bg-cyan-500/10 hover:bg-cyan-500/20 border-cyan-500/30 text-cyan-300'
                        : 'bg-cyan-50 hover:bg-cyan-100 border-cyan-200 text-cyan-800'
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      <BarChart2 className="w-4 h-4" />
                      Model Intelligence &amp; Calibration
                    </span>
                  </button>

                  {(adminVerified || isLocalDev) && (
                    <button
                      type="button"
                      onClick={() => {
                        setToolsOpen(false);
                        onSyncData();
                      }}
                      disabled={syncing}
                      className="w-full px-3 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-extrabold flex items-center justify-center gap-2 shadow disabled:opacity-50"
                    >
                      <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
                      <span>{syncing ? 'Syncing Dataset...' : 'Force Live Dataset Sync'}</span>
                    </button>
                  )}
                </div>

                <div className={`pt-2.5 border-t space-y-2 ${darkMode ? 'border-slate-800' : 'border-slate-200'}`}>
                  <div className="flex items-center justify-between text-[11px] font-bold">
                    <span className="flex items-center gap-1.5 text-slate-400">
                      <KeyRound className="w-3.5 h-3.5 text-amber-400" />
                      Admin Operations Key
                    </span>
                    <span
                      className={`inline-flex items-center gap-1 text-[10px] font-black ${
                        adminVerified ? 'text-emerald-400' : 'text-slate-400'
                      }`}
                    >
                      {adminVerified ? <Unlock className="w-3 h-3" /> : <Lock className="w-3 h-3" />}
                      {adminVerified ? 'Unlocked' : 'Locked'}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <input
                      type="password"
                      value={adminInput}
                      onChange={(e) => setAdminInput(e.target.value)}
                      placeholder="Enter X-Admin-Key..."
                      className={`flex-1 px-2.5 py-1.5 rounded-lg border text-xs font-mono ${
                        darkMode ? 'bg-slate-950 border-slate-800 text-white' : 'bg-slate-50 border-slate-300 text-slate-900'
                      }`}
                    />
                    <button
                      type="button"
                      onClick={handleSaveAdminKey}
                      disabled={verifyingKey}
                      className="px-2.5 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-slate-950 text-xs font-black flex items-center gap-1 disabled:opacity-50"
                    >
                      <Check className="w-3.5 h-3.5" />
                      <span>{verifyingKey ? '...' : 'Save'}</span>
                    </button>
                  </div>
                  <p className="text-[10px] text-slate-400 leading-snug">
                    Unlocks manual dataset sync, server alert rules, and backfill jobs in production.
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
