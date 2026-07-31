import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { 
  Activity, 
  XCircle, 
  RefreshCw, 
  Server, 
  Search, 
  Calendar, 
  MapPin, 
  Target, 
  TrendingUp, 
  BarChart2, 
  Zap, 
  Sparkles, 
  ChevronRight, 
  ChevronDown,
  ChevronUp,
  Layers, 
  X,
  Filter,
  Sun,
  Moon,
  AlertCircle,
  List,
  Grid,
  Shield,
  CheckCircle2,
  Award,
  Flame,
  Info,
  ArrowUpRight,
  Trophy,
  Clock,
  Crown,
  PieChart,
  Split,
  Radio
} from 'lucide-react';

const getApiBaseUrl = () => {
  if (import.meta.env.VITE_API_BASE_URL) return import.meta.env.VITE_API_BASE_URL;
  if (typeof window !== 'undefined' && window.location.hostname) {
    const host = window.location.hostname;
    if (host.includes('.onrender.com')) {
      const apiHost = host.includes('-web.onrender.com') 
        ? host.replace(/-web\.onrender\.com$/, '-api.onrender.com')
        : 'soccer-goal-predictor-api.onrender.com';
      return `${window.location.protocol}//${apiHost}`;
    }
    if (window.location.port === '5173' || window.location.port === '3000' || host === 'localhost' || host === '127.0.0.1') {
      return `${window.location.protocol}//${host}:8000`;
    }
    return `${window.location.protocol}//${host}:8000`;
  }
  return 'http://127.0.0.1:8000';
};

const API_BASE_URL = getApiBaseUrl();

// Resilient API Request helper with multi-URL candidate fallbacks & relative-first routing
const apiRequest = async (method, path, data = null, options = {}) => {
  const host = typeof window !== 'undefined' ? window.location.hostname : '';
  const isRender = host.includes('.onrender.com');

  const candidates = isRender
    ? [
        `${API_BASE_URL}${path}`,
        `https://soccer-goal-predictor-api.onrender.com${path}`,
        path,
      ]
    : [
        path,
        `${API_BASE_URL}${path}`,
        `http://127.0.0.1:8000${path}`,
        `http://localhost:8000${path}`,
        `https://soccer-goal-predictor-api.onrender.com${path}`,
      ];

  const urls = candidates.filter((v, i, a) => v && a.indexOf(v) === i);

  let lastErr = null;
  const timeoutMs = options.timeout || (isRender ? 25000 : 10000);

  for (const url of urls) {
    try {
      let res;
      if (method === 'get') {
        res = await axios.get(url, { timeout: timeoutMs, ...options });
      } else if (method === 'post') {
        res = await axios.post(url, data, { timeout: timeoutMs, ...options });
      }
      // Ensure valid JSON API payload object (rejects HTML fallback strings from static server rewrites)
      if (res && res.data && typeof res.data === 'object' && res.data.status === 'ok') {
        return res;
      } else if (res && res.data && typeof res.data === 'object') {
        return res;
      }
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error('Unable to connect to FastAPI backend server');
};

export default function App() {
  const [fixtures, setFixtures] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState(null);
  const [selectedFixture, setSelectedFixture] = useState(null);

  // Layout View Mode (Default: Compact Table/List View)
  const [viewMode, setViewMode] = useState('table'); // 'table' | 'grid'

  // Dark / Light Theme state (Default: Dark Mode)
  const [darkMode, setDarkMode] = useState(true);

  // Filter & Search states (Main Table Default: Ascending Order by Kickoff Date starting from current date upward)
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedLeague, setSelectedLeague] = useState('ALL');
  const [sortBy, setSortBy] = useState('DATE_ASC');

  // Dedicated Best 15 Over 1.5 Filter State
  const [showBest15Over15, setShowBest15Over15] = useState(false);

  // Main Tab Selection ('upcoming' | 'finished')
  const [activeTab, setActiveTab] = useState('upcoming');
  const [finishedFixtures, setFinishedFixtures] = useState([]);
  const [loadingFinished, setLoadingFinished] = useState(false);

  // Notification Banner State
  const [notification, setNotification] = useState(null);

  // Mobile Accordion state to track expanded fixture IDs
  const [expandedMobileRows, setExpandedMobileRows] = useState(new Set());

  const toggleMobileRow = (fixId) => {
    setExpandedMobileRows(prev => {
      const next = new Set(prev);
      if (next.has(fixId)) {
        next.delete(fixId);
      } else {
        next.add(fixId);
      }
      return next;
    });
  };

  // Backend health status state
  const [backendHealth, setBackendHealth] = useState({
    online: false,
    latency: null,
    lastChecked: null,
  });

  // Check Backend Health with retry and multi-URL candidates
  const checkHealth = async (retries = 2) => {
    const startTime = performance.now();
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const res = await apiRequest('get', '/health', null, { timeout: 5000 });
        const endTime = performance.now();
        if (res.data?.status === 'ok') {
          setBackendHealth({
            online: true,
            latency: Math.round(endTime - startTime),
            lastChecked: new Date().toLocaleTimeString(),
          });
          return;
        }
      } catch (err) {
        if (attempt === retries) {
          setBackendHealth(prev => ({ ...prev, online: false }));
        } else {
          await new Promise(r => setTimeout(r, 600));
        }
      }
    }
  };

  // Fetch upcoming fixtures from FastAPI with multi-URL fallback
  const fetchUpcomingFixtures = async (isSilent = false) => {
    if (!isSilent) setLoading(true);
    setError(null);
    try {
      const response = await apiRequest('get', '/api/fixtures/upcoming');
      if (response.data?.status === 'ok') {
        setFixtures(response.data.data || []);
      } else {
        if (!isSilent) setError('Failed to fetch upcoming match predictions');
      }
    } catch (err) {
      if (!isSilent) setError(err.message || 'Unable to connect to FastAPI backend server. Ensure backend is running.');
    } finally {
      if (!isSilent) setLoading(false);
    }
  };

  // Fetch completed match results from FastAPI
  const fetchFinishedFixtures = async () => {
    setLoadingFinished(true);
    try {
      const response = await apiRequest('get', '/api/fixtures/finished');
      if (response.data?.status === 'ok') {
        setFinishedFixtures(response.data.data || []);
      }
    } catch (err) {
      console.error('Failed to fetch finished match results:', err);
    } finally {
      setLoadingFinished(false);
    }
  };

  // Trigger automated data sync & recalculation
  const handleSyncData = async () => {
    setSyncing(true);
    setNotification(null);
    try {
      await apiRequest('post', '/api/ingest/sync');
      await apiRequest('post', '/api/predictions/predict-all');
      await fetchUpcomingFixtures(true);
      await fetchFinishedFixtures();
      setNotification({ type: 'success', message: 'Live global fixtures & predictions refreshed successfully!' });
    } catch (err) {
      console.error('Sync failed:', err);
      setNotification({ type: 'error', message: 'Failed to sync data from backend server.' });
    } finally {
      setSyncing(false);
      setTimeout(() => setNotification(null), 5000);
    }
  };

  useEffect(() => {
    checkHealth();
    fetchUpcomingFixtures(false);
    fetchFinishedFixtures();
    // Auto refresh live scores silently every 20 seconds
    const interval = setInterval(() => {
      checkHealth();
      fetchUpcomingFixtures(true);
    }, 20000);
    return () => clearInterval(interval);
  }, []);

  // Helper to test if match is currently live
  const isMatchLive = (fix) => {
    if (!fix || !fix.status) return false;
    const st = String(fix.status).toUpperCase();
    return st === 'LIVE' || st === 'IN_PROGRESS' || st === 'HALFTIME' || st === 'FIRST_HALF' || st === 'SECOND_HALF';
  };

  // Count live matches
  const liveCount = useMemo(() => {
    return fixtures.filter(f => isMatchLive(f)).length;
  }, [fixtures]);

  // Unique list of leagues for filter dropdown
  const leaguesList = useMemo(() => {
    const leagues = new Set(fixtures.map(f => f.league?.name).filter(Boolean));
    return Array.from(leagues);
  }, [fixtures]);
  const parseMatchDate = (dateStr) => {
    if (!dateStr) return null;
    let s = String(dateStr).trim();
    if (!s.endsWith('Z') && !s.includes('+') && !s.includes('-', 10)) {
      s = s.replace(' ', 'T') + 'Z';
    } else {
      s = s.replace(' ', 'T');
    }
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  };

  // GMT+1 Date & Time Formatter
  const formatDateGMT1 = (dateStr) => {
    const d = parseMatchDate(dateStr);
    if (!d) return 'TBD';

    const datePart = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Europe/London',
      weekday: 'short',
      month: 'short',
      day: 'numeric'
    }).format(d);

    const timePart = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Europe/London',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true
    }).format(d);

    return `${datePart}, ${timePart} (GMT+1)`;
  };

  // Helper to extract GMT+1 YYYY-MM-DD string for grouping/filtering matches by day
  const getGMT1DayKey = (dateStr) => {
    const d = parseMatchDate(dateStr);
    if (!d) return '';
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/London',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    }).format(d);
    return parts;
  };

  // Helper to format GMT+1 day key into readable title (e.g. "Today (Jul 29)", "Jul 30")
  const formatDayTitle = (dayKey) => {
    if (!dayKey || dayKey === 'ALL_DAYS') return 'All Match Days';
    const todayKey = getGMT1DayKey(new Date().toISOString());
    
    const parts = dayKey.split('-').map(Number);
    if (parts.length < 3 || !parts[0] || !parts[1] || !parts[2]) return 'All Match Days';
    const [y, m, d] = parts;
    const dateObj = new Date(Date.UTC(y, m - 1, d, 12, 0, 0));
    
    const dateFormatted = new Intl.DateTimeFormat('en-US', {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      timeZone: 'UTC'
    }).format(dateObj);

    if (dayKey === todayKey) {
      return `Today (${dateFormatted})`;
    }
    return dateFormatted;
  };

  // Helper to resolve Percentage Brightness Color Class (Brighter for higher %, Dimmer for lower %)
  const getPercentageColorClass = (pct) => {
    if (pct >= 85) {
      return 'bg-gradient-to-r from-emerald-400 to-cyan-400 text-slate-950 shadow-md font-black ring-2 ring-emerald-300 animate-pulse';
    }
    if (pct >= 80) {
      return 'bg-emerald-400 text-slate-950 font-black shadow-sm';
    }
    if (pct >= 75) {
      return 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 font-bold';
    }
    if (pct >= 65) {
      return 'bg-amber-500/20 text-amber-300 border border-amber-500/30 font-semibold';
    }
    return 'bg-slate-800 text-slate-400 border border-slate-700/60 font-normal opacity-75';
  };

  // Helper for rendering Live Score / Match Status Badge
  const renderLiveStatusBadge = (fix) => {
    if (isMatchLive(fix)) {
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-black uppercase bg-rose-600 text-white shadow-lg ring-2 ring-rose-400 animate-pulse">
          <Radio className="w-3 h-3 text-white animate-spin" />
          <span>🔴 LIVE {fix.live_clock || ''} &bull; {fix.home_score ?? 0} - {fix.away_score ?? 0}</span>
        </span>
      );
    }
    if (fix.status === 'FINISHED') {
      return (
        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-extrabold uppercase bg-slate-800 text-slate-200 border border-slate-700">
          <span>FT &bull; {fix.home_score ?? 0} - {fix.away_score ?? 0}</span>
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase bg-slate-800/50 text-slate-400 border border-slate-700/50">
        <Clock className="w-3 h-3 text-slate-400" />
        <span>Scheduled</span>
      </span>
    );
  };

  // Combined List of distinct match days available across fixtures and finished matches
  const availableMatchDays = useMemo(() => {
    const allMatches = [...fixtures, ...finishedFixtures];
    const daysSet = new Set(allMatches.map(f => getGMT1DayKey(f.match_date)).filter(Boolean));
    const days = Array.from(daysSet).sort();
    return days;
  }, [fixtures, finishedFixtures]);

  // Selected Particular Day state for Top 10 Daily Picks (Default: Today's Date)
  const [selectedPickDay, setSelectedPickDay] = useState(() => {
    return getGMT1DayKey(new Date().toISOString()) || 'ALL_DAYS';
  });

  // Automatically default Table 1 to current date (Today) if matches exist, or fallback to closest upcoming match day
  useEffect(() => {
    if (availableMatchDays.length > 0) {
      const todayKey = getGMT1DayKey(new Date().toISOString());
      if (availableMatchDays.includes(todayKey)) {
        if (!selectedPickDay || !availableMatchDays.includes(selectedPickDay)) {
          setSelectedPickDay(todayKey);
        }
      } else if (!availableMatchDays.includes(selectedPickDay) && selectedPickDay !== 'ALL_DAYS') {
        setSelectedPickDay(availableMatchDays[0]);
      }
    }
  }, [availableMatchDays]);

  // TOP 5 OVER 1.5 GOALS PICKS FOR PARTICULAR SELECTED DAY (UPCOMING)
  const top5Over15Picks = useMemo(() => {
    if (!fixtures.length) return [];
    
    let dayMatches = [...fixtures];
    if (selectedPickDay && selectedPickDay !== 'ALL_DAYS') {
      dayMatches = dayMatches.filter(f => getGMT1DayKey(f.match_date) === selectedPickDay);
    }
    
    return dayMatches
      .filter(f => f.prediction && (f.prediction.over_1_5_probability || 0) > 0)
      .sort((a, b) => (b.prediction?.over_1_5_probability || 0) - (a.prediction?.over_1_5_probability || 0))
      .slice(0, 5);
  }, [fixtures, selectedPickDay]);

  // TOP 5 FINISHED OVER 1.5 GOALS PICKS FOR PARTICULAR SELECTED DAY (RESULTS)
  const top5FinishedPicks = useMemo(() => {
    if (!finishedFixtures.length) return [];
    
    let dayMatches = [...finishedFixtures];
    if (selectedPickDay && selectedPickDay !== 'ALL_DAYS') {
      dayMatches = dayMatches.filter(f => getGMT1DayKey(f.match_date) === selectedPickDay);
    }
    
    return dayMatches
      .filter(f => f.prediction && (f.prediction.over_1_5_probability || 0) > 0)
      .sort((a, b) => (b.prediction?.over_1_5_probability || 0) - (a.prediction?.over_1_5_probability || 0))
      .slice(0, 5);
  }, [finishedFixtures, selectedPickDay]);

  // Filter & Sort Logic for Main Fixtures Table (Table 2: Up to 20 matches >= 50% Over 1.5 Goal probability sorted descending)
  const filteredFixtures = useMemo(() => {
    let result = fixtures.filter(fix => {
      const homeName = fix.home_team?.name?.toLowerCase() || '';
      const awayName = fix.away_team?.name?.toLowerCase() || '';
      const leagueName = fix.league?.name?.toLowerCase() || '';
      const query = searchTerm.toLowerCase();
      const matchesSearch = homeName.includes(query) || awayName.includes(query) || leagueName.includes(query);
      const matchesLeague = selectedLeague === 'ALL' || fix.league?.name === selectedLeague;
      const matchesDay = selectedPickDay === 'ALL_DAYS' || getGMT1DayKey(fix.match_date) === selectedPickDay;
      const isOver50Percent = (fix.prediction?.over_1_5_probability || 0) >= 0.50;
      return matchesSearch && matchesLeague && matchesDay && isOver50Percent;
    });

    result = result
      .sort((a, b) => (b.prediction?.over_1_5_probability || 0) - (a.prediction?.over_1_5_probability || 0))
      .slice(0, 20);

    return result;
  }, [fixtures, searchTerm, selectedLeague, selectedPickDay]);

  // Filtered Finished Fixtures for Results Tab (Table 2: Up to 20 finished matches >= 50% Over 1.5 Goal probability sorted descending)
  const filteredFinishedFixtures = useMemo(() => {
    let result = finishedFixtures.filter(fix => {
      const homeName = fix.home_team?.name?.toLowerCase() || '';
      const awayName = fix.away_team?.name?.toLowerCase() || '';
      const leagueName = fix.league?.name?.toLowerCase() || '';
      const query = searchTerm.toLowerCase();
      const matchesSearch = homeName.includes(query) || awayName.includes(query) || leagueName.includes(query);
      const matchesLeague = selectedLeague === 'ALL' || fix.league?.name === selectedLeague;
      const matchesDay = selectedPickDay === 'ALL_DAYS' || getGMT1DayKey(fix.match_date) === selectedPickDay;
      const isOver50Percent = (fix.prediction?.over_1_5_probability || 0) >= 0.50;
      return matchesSearch && matchesLeague && matchesDay && isOver50Percent;
    });

    result = result
      .sort((a, b) => (b.prediction?.over_1_5_probability || 0) - (a.prediction?.over_1_5_probability || 0))
      .slice(0, 20);

    return result;
  }, [finishedFixtures, searchTerm, selectedLeague, selectedPickDay]);

  // Table 2 Pagination State (Default: 50 matches per page for fast DOM render)
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState('50');

  // Reset to page 1 whenever filters or search terms change
  useEffect(() => {
    setCurrentPage(1);
  }, [searchTerm, selectedLeague, sortBy, selectedPickDay, showBest15Over15]);

  // Paginated fixtures slice
  const paginatedFixtures = useMemo(() => {
    if (pageSize === 'ALL') return filteredFixtures;
    const size = Number(pageSize) || 50;
    const startIndex = (currentPage - 1) * size;
    return filteredFixtures.slice(startIndex, startIndex + size);
  }, [filteredFixtures, currentPage, pageSize]);

  // Total pagination pages
  const totalPages = useMemo(() => {
    if (pageSize === 'ALL') return 1;
    const size = Number(pageSize) || 50;
    return Math.ceil(filteredFixtures.length / size) || 1;
  }, [filteredFixtures, pageSize]);

  // Summary metrics calculated for the currently selected match day & active/upcoming matches
  const summaryStats = useMemo(() => {
    if (!fixtures.length) return { total: 0, highOver15Count: 0, highOver25Count: 0 };
    
    // Filter for selected match day
    let dayFixtures = fixtures;
    if (selectedPickDay && selectedPickDay !== 'ALL_DAYS') {
      dayFixtures = fixtures.filter(f => getGMT1DayKey(f.match_date) === selectedPickDay);
    }
    
    // Exclude finished fixtures so available counts decrease dynamically as each match ends
    const activeFixtures = dayFixtures.filter(f => 
      f.status !== 'FINISHED' && f.status !== 'FT' && f.status !== 'AET' && f.status !== 'PEN'
    );
    
    const total = activeFixtures.length;
    const highOver15Count = activeFixtures.filter(f => (f.prediction?.over_1_5_probability || 0) >= 0.75).length;
    const highOver25Count = activeFixtures.filter(f => (f.prediction?.over_2_5_probability || 0) >= 0.50).length;
    
    return {
      total,
      highOver15Count,
      highOver25Count,
    };
  }, [fixtures, selectedPickDay]);

  // Total upcoming fixtures for the selected date (or all days if ALL_DAYS)
  const dayUpcomingFixturesCount = useMemo(() => {
    if (selectedPickDay === 'ALL_DAYS') return fixtures.length;
    return fixtures.filter(f => getGMT1DayKey(f.match_date) === selectedPickDay).length;
  }, [fixtures, selectedPickDay]);

  // Helper for outcome badges
  const getBestOutcome = (pred) => {
    if (!pred) return { label: 'Over 1.5 Goals', pct: 0, level: 'low' };
    const o15 = Math.round((pred.over_1_5_probability || 0) * 100);
    const homeWin = Math.round((pred.home_win_probability || 0) * 100);
    const awayWin = Math.round((pred.away_win_probability || 0) * 100);

    if (o15 >= 75) {
      return { 
        label: 'Over 1.5 Goals', 
        pct: o15, 
        badge: 'Hot Pick', 
        level: 'very-high', 
        colorClass: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' 
      };
    }
    if (homeWin >= 60) {
      return { 
        label: 'Home Win (1)', 
        pct: homeWin, 
        badge: 'Home Fav', 
        level: 'high', 
        colorClass: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' 
      };
    }
    if (awayWin >= 60) {
      return { 
        label: 'Away Win (2)', 
        pct: awayWin, 
        badge: 'Away Fav', 
        level: 'high', 
        colorClass: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30' 
      };
    }
    return { 
      label: 'Over 1.5 Goals', 
      pct: o15, 
      badge: 'Moderate', 
      level: 'moderate', 
      colorClass: 'bg-amber-500/10 text-amber-400 border-amber-500/30' 
    };
  };

  return (
    <div className={`min-h-screen flex flex-col font-sans transition-colors duration-300 relative overflow-x-hidden w-full max-w-full ${
      darkMode ? 'bg-slate-950 text-slate-100 pitch-grid' : 'bg-slate-50 text-slate-900'
    }`}>
      {/* Background Glow Blobs */}
      {darkMode && (
        <>
          <div className="absolute top-[-10%] left-[-10%] w-[550px] h-[550px] bg-emerald-600/10 rounded-full blur-[150px] pointer-events-none" />
          <div className="absolute bottom-[-10%] right-[-10%] w-[550px] h-[550px] bg-cyan-600/10 rounded-full blur-[150px] pointer-events-none" />
        </>
      )}

      {/* Header */}
      <header className={`border-b sticky top-0 z-40 backdrop-blur-md transition-colors ${
        darkMode ? 'border-slate-800/80 bg-slate-950/80' : 'border-slate-200 bg-white/80'
      }`}>
        <div className="max-w-7xl mx-auto px-3 sm:px-6 lg:px-8 h-14 sm:h-16 flex items-center justify-between gap-2 overflow-hidden">
          
          {/* Brand */}
          <div className="flex items-center space-x-2 sm:space-x-3 shrink-0">
            <div className="p-1.5 sm:p-2 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-emerald-500 shrink-0">
              <Activity className="w-4 h-4 sm:w-6 sm:h-6 animate-pulse" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 sm:gap-2">
                <span className={`font-black text-xs sm:text-base md:text-xl tracking-tight leading-tight uppercase ${
                  darkMode ? 'bg-gradient-to-r from-white via-slate-200 to-emerald-400 bg-clip-text text-transparent' : 'text-slate-900'
                }`}>
                  <span className="block sm:inline">Soccer</span>
                  <span className="sm:ml-1 text-emerald-400">GoalPredictor</span>
                </span>
                <span className="hidden md:inline-block text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-500 border border-emerald-500/30 whitespace-nowrap">
                  GMT+1 Live Feed
                </span>
              </div>
              <p className="text-[11px] text-slate-400 hidden lg:block">
                Real-Time Match Timings (GMT+1) & Goal Expectation Analytics
              </p>
            </div>
          </div>

          {/* Action Controls */}
          <div className="flex items-center space-x-1.5 sm:space-x-3 shrink-0">
            
            {/* View Mode Toggle */}
            <div className={`flex items-center p-0.5 sm:p-1 rounded-xl border ${
              darkMode ? 'bg-slate-900 border-slate-800' : 'bg-slate-100 border-slate-200'
            }`}>
              <button
                onClick={() => setViewMode('table')}
                className={`p-1 sm:p-1.5 rounded-lg text-xs font-semibold flex items-center gap-1 transition-all ${
                  viewMode === 'table' 
                    ? 'bg-emerald-600 text-white shadow-sm' 
                    : darkMode ? 'text-slate-400 hover:text-white' : 'text-slate-600 hover:text-slate-900'
                }`}
                title="Compact Table / List View"
              >
                <List className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                <span className="hidden md:inline">List</span>
              </button>
              <button
                onClick={() => setViewMode('grid')}
                className={`p-1 sm:p-1.5 rounded-lg text-xs font-semibold flex items-center gap-1 transition-all ${
                  viewMode === 'grid' 
                    ? 'bg-emerald-600 text-white shadow-sm' 
                    : darkMode ? 'text-slate-400 hover:text-white' : 'text-slate-600 hover:text-slate-900'
                }`}
                title="Grid Cards View"
              >
                <Grid className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                <span className="hidden md:inline">Grid</span>
              </button>
            </div>

            {/* Theme Toggle */}
            <button
              onClick={() => setDarkMode(!darkMode)}
              className={`p-1.5 sm:p-2 rounded-xl border transition-all ${
                darkMode 
                  ? 'bg-slate-900 border-slate-800 text-amber-400 hover:bg-slate-800' 
                  : 'bg-slate-100 border-slate-300 text-slate-700 hover:bg-slate-200'
              }`}
              title={darkMode ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
            >
              {darkMode ? <Sun className="w-3.5 h-3.5 sm:w-4 sm:h-4" /> : <Moon className="w-3.5 h-3.5 sm:w-4 sm:h-4" />}
            </button>

            {/* API Health */}
            <div className={`flex items-center space-x-1.5 px-2 sm:px-3 py-1 sm:py-1.5 rounded-full text-[10px] sm:text-xs font-semibold border transition-all ${
              backendHealth.online 
                ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30' 
                : 'bg-rose-500/10 text-rose-500 border-rose-500/30'
            }`}>
              <span className={`w-1.5 h-1.5 sm:w-2 sm:h-2 rounded-full ${backendHealth.online ? 'bg-emerald-400 animate-ping' : 'bg-rose-400'}`} />
              <span className="hidden sm:inline">
                {backendHealth.online ? `Live (${backendHealth.latency}ms)` : 'Offline'}
              </span>
            </div>

            {/* Sync Button */}
            <button
              onClick={handleSyncData}
              disabled={syncing}
              className="flex items-center space-x-1 px-2.5 sm:px-3 py-1 sm:py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl transition-all shadow-md active:scale-95 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
              <span className="hidden sm:inline">{syncing ? 'Syncing...' : 'Sync'}</span>
            </button>
          </div>
        </div>
      </header>

      {/* Toast Notification Banner */}
      {notification && (
        <div className="max-w-7xl mx-auto px-3 sm:px-4 mt-3 sm:mt-4 w-full">
          <div className={`p-2.5 sm:p-3 rounded-2xl border text-xs font-bold flex items-center justify-between ${
            notification.type === 'success' 
              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400' 
              : 'bg-rose-500/10 border-rose-500/30 text-rose-400'
          }`}>
            <div className="flex items-center space-x-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{notification.message}</span>
            </div>
            <button onClick={() => setNotification(null)}>
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Main Container */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-3 sm:px-6 lg:px-8 py-4 sm:py-8 flex flex-col gap-5 sm:gap-8 overflow-x-hidden">
        
        {/* Main Tab Navigation Bar */}
        <div className="flex items-center gap-2 sm:gap-3 border-b pb-3 border-slate-800/80 w-full overflow-x-auto custom-scrollbar">
          <button
            onClick={() => setActiveTab('upcoming')}
            className={`px-4 sm:px-5 py-2 rounded-xl text-xs sm:text-sm font-extrabold flex items-center gap-2 transition-all shrink-0 ${
              activeTab === 'upcoming'
                ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-600/20'
                : darkMode ? 'bg-slate-900 text-slate-400 hover:text-white border border-slate-800' : 'bg-slate-200 text-slate-700 hover:bg-slate-300'
            }`}
          >
            <Activity className="w-4 h-4 text-emerald-400" />
            <span>Upcoming & Live Fixtures</span>
            <span className="ml-1 px-2 py-0.5 rounded-full text-[10px] bg-slate-950/60 text-emerald-400 border border-emerald-500/30">
              {dayUpcomingFixturesCount}
            </span>
          </button>

          <button
            onClick={() => {
              setActiveTab('finished');
              if (finishedFixtures.length === 0) fetchFinishedFixtures();
            }}
            className={`px-4 sm:px-5 py-2 rounded-xl text-xs sm:text-sm font-extrabold flex items-center gap-2 transition-all shrink-0 ${
              activeTab === 'finished'
                ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-600/20'
                : darkMode ? 'bg-slate-900 text-slate-400 hover:text-white border border-slate-800' : 'bg-slate-200 text-slate-700 hover:bg-slate-300'
            }`}
          >
            <Trophy className="w-4 h-4 text-amber-400" />
            <span>Finished Matches & Scores</span>
            <span className="ml-1 px-2 py-0.5 rounded-full text-[10px] bg-slate-950/60 text-amber-400 border border-amber-500/30">
              {filteredFinishedFixtures.length}
            </span>
          </button>
        </div>

        {activeTab === 'finished' ? (
          <section className="space-y-6">
            {/* Finished Matches Banner Header */}
            <div className={`rounded-2xl sm:rounded-3xl p-4 sm:p-8 border shadow-xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4 transition-colors ${
              darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200'
            }`}>
              <div className="space-y-1.5 max-w-2xl">
                <div className="flex items-center gap-2">
                  <div className="p-2 rounded-xl bg-amber-500/20 text-amber-400 border border-amber-500/40">
                    <Trophy className="w-5 h-5 sm:w-6 sm:h-6" />
                  </div>
                  <h1 className={`text-xl sm:text-3xl font-extrabold tracking-tight ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                    Finished Match Results & Scores
                  </h1>
                </div>
                <p className="text-xs sm:text-sm text-slate-400 leading-relaxed mt-1">
                  Completed match scores with verified Over 1.5 goals prediction outcomes and accuracy tracking for {selectedPickDay === 'ALL_DAYS' ? 'all match days' : formatDayTitle(selectedPickDay)}.
                </p>
              </div>

              {/* Finished Summary Stats */}
              <div className="grid grid-cols-3 gap-2 sm:gap-3 w-full md:w-auto shrink-0">
                <div className={`p-3 rounded-2xl border text-center ${darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-slate-50 border-slate-200'}`}>
                  <span className="text-[10px] font-bold text-slate-400 uppercase truncate">Results</span>
                  <span className="text-base sm:text-xl font-bold text-white block mt-0.5">{filteredFinishedFixtures.length}</span>
                  <span className="text-[8px] sm:text-[9px] font-semibold text-emerald-400 truncate mt-0.5">
                    {selectedPickDay === 'ALL_DAYS' ? 'All Days' : formatDayTitle(selectedPickDay)}
                  </span>
                </div>
                <div className={`p-3 rounded-2xl border text-center ${darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-slate-50 border-slate-200'}`}>
                  <span className="text-[10px] font-bold text-slate-400 uppercase truncate">&gt; 1.5 Hits</span>
                  <span className="text-base sm:text-xl font-bold text-emerald-400 block mt-0.5">
                    {filteredFinishedFixtures.filter(f => f.over_1_5_hit).length}
                  </span>
                  <span className="text-[8px] sm:text-[9px] font-semibold text-slate-500 truncate mt-0.5">Hit</span>
                </div>
                <div className={`p-3 rounded-2xl border text-center ${darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-slate-50 border-slate-200'}`}>
                  <span className="text-[10px] font-bold text-slate-400 uppercase truncate">Hit Rate</span>
                  <span className="text-base sm:text-xl font-bold text-cyan-400 block mt-0.5">
                    {filteredFinishedFixtures.length ? Math.round((filteredFinishedFixtures.filter(f => f.over_1_5_hit).length / filteredFinishedFixtures.length) * 100) : 0}%
                  </span>
                  <span className="text-[8px] sm:text-[9px] font-semibold text-slate-500 truncate mt-0.5">Accuracy</span>
                </div>
              </div>
            </div>

            {/* FEATURED TABLE 1: TOP 10 FINISHED OVER 1.5 GOALS PICKS FOR PARTICULAR SELECTED DAY */}
            <section className={`rounded-2xl sm:rounded-3xl border p-4 sm:p-6 space-y-4 shadow-2xl relative overflow-hidden transition-colors ${
              darkMode 
                ? 'bg-gradient-to-br from-slate-900 via-slate-900/90 to-emerald-950/30 border-emerald-500/40' 
                : 'bg-gradient-to-br from-white via-emerald-50/30 to-emerald-100/40 border-emerald-300'
            }`}>
              
              {/* Header & Particular Day Selector */}
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 sm:gap-4 border-b pb-3.5 border-emerald-500/20">
                <div className="flex items-center space-x-2.5 sm:space-x-3">
                  <div className="p-2 sm:p-2.5 rounded-xl sm:rounded-2xl bg-amber-500/20 text-amber-400 border border-amber-500/40 shrink-0">
                    <Crown className="w-5 h-5 sm:w-6 sm:h-6 animate-pulse" />
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap">
                      <h2 className={`text-base sm:text-lg font-extrabold tracking-tight ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                        Table 1: Top 5 Daily Finished Picks
                      </h2>
                      <span className="text-[9px] sm:text-[10px] font-bold uppercase px-2 py-0.5 rounded-full bg-amber-400 text-slate-950">
                        {selectedPickDay === 'ALL_DAYS' ? 'All Match Days' : formatDayTitle(selectedPickDay)}
                      </span>
                    </div>
                    <p className="text-[11px] sm:text-xs text-slate-400 mt-0.5">
                      Top 5 finished picks sorted by highest predicted percentage for {selectedPickDay === 'ALL_DAYS' ? 'all days' : formatDayTitle(selectedPickDay)}.
                    </p>
                  </div>
                </div>

                {/* Particular Day Filter Dropdown */}
                <div className={`flex items-center gap-2 border px-2.5 sm:px-3 py-1.5 rounded-xl shadow-sm shrink-0 w-full sm:w-auto justify-between sm:justify-start ${
                  darkMode ? 'bg-slate-950 border-slate-800' : 'bg-white border-slate-300'
                }`}>
                  <div className="flex items-center gap-1.5">
                    <Calendar className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-emerald-400" />
                    <span className="text-xs font-semibold text-slate-400">Match Day:</span>
                  </div>
                  <select
                    value={selectedPickDay}
                    onChange={(e) => setSelectedPickDay(e.target.value)}
                    className={`bg-transparent text-xs font-bold cursor-pointer focus:outline-none ${
                      darkMode ? 'text-emerald-400' : 'text-emerald-800'
                    }`}
                  >
                    <option value="ALL_DAYS" className={darkMode ? 'bg-slate-900' : 'bg-white'}>All Match Days</option>
                    {availableMatchDays.map(dayKey => (
                      <option key={dayKey} value={dayKey} className={darkMode ? 'bg-slate-900' : 'bg-white'}>
                        {formatDayTitle(dayKey)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Table 1: Featured Picks Content */}
              {top5FinishedPicks.length === 0 ? (
                <div className="p-6 text-center text-xs text-slate-400 font-semibold">
                  No finished top picks found for {selectedPickDay === 'ALL_DAYS' ? 'all match days' : formatDayTitle(selectedPickDay)}.
                </div>
              ) : (
                <>
                  {/* Mobile Accordion View for Table 1 */}
                  <div className="block sm:hidden space-y-2">
                    {top5FinishedPicks.map((fix, idx) => {
                      const over15Pct = Math.round((fix.prediction?.over_1_5_probability || 0) * 100);
                      const isRank1 = idx === 0;
                      const isTop3 = idx < 3;
                      const isExpanded = expandedMobileRows.has(`finished-top10-${fix.id}`);

                      return (
                        <div
                          key={`finished-top10-mobile-${fix.id}`}
                          className={`rounded-xl border transition-all overflow-hidden ${
                            isRank1
                              ? darkMode ? 'bg-amber-500/10 border-amber-500/40' : 'bg-amber-50 border-amber-300'
                              : darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-white border-slate-200'
                          }`}
                        >
                          <div
                            onClick={() => toggleMobileRow(`finished-top10-${fix.id}`)}
                            className="p-3 flex items-center justify-between gap-2 cursor-pointer active:bg-slate-800/40"
                          >
                            <div className="flex items-center gap-2.5 min-w-0 flex-1">
                              {isRank1 ? (
                                <span className="px-2 py-0.5 rounded-md font-extrabold text-[10px] bg-amber-400 text-slate-950 shadow-sm flex items-center gap-1 font-mono shrink-0">
                                  <Crown className="w-3 h-3 fill-current" />
                                  <span>#1</span>
                                </span>
                              ) : (
                                <span className={`w-5 h-5 rounded-md font-bold text-[10px] flex items-center justify-center font-mono shrink-0 ${
                                  isTop3 ? 'bg-emerald-400 text-slate-950' : 'bg-slate-800 text-slate-300 border border-slate-700'
                                }`}>
                                  #{idx + 1}
                                </span>
                              )}

                              <div className="flex flex-col min-w-0 flex-1">
                                <div className="flex items-center gap-1.5 truncate">
                                  <span className={`font-extrabold text-xs truncate ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                    {fix.home_team?.name}
                                  </span>
                                  <span className="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 font-mono font-extrabold text-[10px] border border-emerald-500/40 shrink-0">
                                    {fix.home_score} - {fix.away_score}
                                  </span>
                                  <span className={`font-extrabold text-xs truncate ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                    {fix.away_team?.name}
                                  </span>
                                </div>
                                <span className="text-[10px] text-slate-400 truncate">
                                  {fix.league?.name || 'League'}
                                </span>
                              </div>
                            </div>

                            <div className="flex items-center gap-1.5 shrink-0">
                              {fix.over_1_5_hit ? (
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 flex items-center gap-1">
                                  <CheckCircle2 className="w-3 h-3" />
                                  <span>Hit</span>
                                </span>
                              ) : (
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/40 flex items-center gap-1">
                                  <XCircle className="w-3 h-3" />
                                  <span>Under</span>
                                </span>
                              )}
                              <div className="p-1 rounded-lg text-slate-400">
                                {isExpanded ? <ChevronUp className="w-4 h-4 text-emerald-400" /> : <ChevronDown className="w-4 h-4" />}
                              </div>
                            </div>
                          </div>

                          {isExpanded && (
                            <div className={`p-3 border-t space-y-2 text-xs transition-all ${
                              darkMode ? 'border-slate-800/80 bg-slate-900/90' : 'border-slate-100 bg-slate-50'
                            }`}>
                              <div className="grid grid-cols-2 gap-2 text-[11px]">
                                <div className="p-2 rounded-lg bg-slate-800/40 border border-slate-700/50 flex flex-col justify-center">
                                  <span className="text-slate-400 font-semibold text-[10px]">Predicted Over 1.5%</span>
                                  <span className="font-mono font-bold text-emerald-400 text-xs mt-0.5">{over15Pct}%</span>
                                </div>
                                <div className="p-2 rounded-lg bg-slate-800/40 border border-slate-700/50 flex flex-col justify-center">
                                  <span className="text-slate-400 font-semibold text-[10px]">Actual Goals Scored</span>
                                  <span className="font-mono font-bold text-slate-200 text-xs mt-0.5">{fix.total_goals} Goals</span>
                                </div>
                              </div>

                              <button
                                onClick={(e) => { e.stopPropagation(); setSelectedFixture(fix); }}
                                className="w-full py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs flex items-center justify-center gap-1.5 shadow"
                              >
                                <span>View Full Prediction Analytics</span>
                                <ArrowUpRight className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* Desktop Table View for Table 1 */}
                  <div className="hidden sm:block overflow-x-auto custom-scrollbar">
                    <table className="w-full text-left border-collapse">
                      <thead>
                        <tr className={`border-b text-[10px] font-black uppercase tracking-wider ${
                          darkMode ? 'border-slate-800/80 text-emerald-400' : 'border-emerald-200 text-emerald-800'
                        }`}>
                          <th className="py-2.5 px-3">Rank</th>
                          <th className="py-2.5 px-3">Status / Match Date</th>
                          <th className="py-2.5 px-3">Competition</th>
                          <th className="py-2.5 px-3">Matchup & Final Score</th>
                          <th className="py-2.5 px-3 text-center">Over 1.5 Outcome</th>
                          <th className="py-2.5 px-3 text-center">Pred. Over 1.5%</th>
                          <th className="py-2.5 px-3 text-right">Action</th>
                        </tr>
                      </thead>
                      <tbody className={`divide-y text-xs ${darkMode ? 'divide-slate-800/60' : 'divide-emerald-100'}`}>
                        {top5FinishedPicks.map((fix, idx) => {
                          const over15Pct = Math.round((fix.prediction?.over_1_5_probability || 0) * 100);
                          const isRank1 = idx === 0;
                          const isTop3 = idx < 3;

                          return (
                            <tr
                              key={`finished-top10-${fix.id}`}
                              onClick={() => setSelectedFixture(fix)}
                              className={`cursor-pointer transition-all ${
                                isRank1
                                  ? darkMode ? 'bg-amber-500/10 border-l-4 border-l-amber-400 hover:bg-amber-500/20' : 'bg-amber-50 border-l-4 border-l-amber-500 hover:bg-amber-100'
                                  : isTop3
                                  ? darkMode ? 'bg-emerald-500/5 hover:bg-emerald-500/15' : 'bg-emerald-50/50 hover:bg-emerald-100/50'
                                  : darkMode ? 'hover:bg-slate-800/50' : 'hover:bg-slate-50'
                              }`}
                            >
                              <td className="py-3 px-3">
                                {isRank1 ? (
                                  <span className="px-2.5 py-1 rounded-xl font-black text-xs bg-amber-400 text-slate-950 shadow-md flex items-center gap-1 font-mono w-fit">
                                    <Crown className="w-3.5 h-3.5 fill-current" />
                                    <span>#1 TOP PICK</span>
                                  </span>
                                ) : isTop3 ? (
                                  <span className="w-7 h-7 rounded-xl font-black text-xs bg-emerald-400 text-slate-950 shadow-sm flex items-center justify-center font-mono">
                                    #{idx + 1}
                                  </span>
                                ) : (
                                  <span className="w-7 h-7 rounded-xl font-black text-xs bg-slate-800 text-slate-300 border border-slate-700 flex items-center justify-center font-mono">
                                    #{idx + 1}
                                  </span>
                                )}
                              </td>

                              <td className="py-3 px-3">
                                <div className="flex flex-col space-y-1">
                                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-extrabold uppercase bg-slate-800 text-slate-200 border border-slate-700 w-fit">
                                    FT &bull; {fix.home_score} - {fix.away_score}
                                  </span>
                                  <span className={`text-[11px] font-bold ${darkMode ? 'text-slate-300' : 'text-slate-700'}`}>
                                    {formatDateGMT1(fix.match_date)}
                                  </span>
                                </div>
                              </td>

                              <td className="py-3 px-3">
                                <span className="text-[11px] font-extrabold uppercase text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-lg border border-emerald-500/20">
                                  {fix.league?.name || 'League'}
                                </span>
                              </td>

                              <td className="py-3 px-3">
                                <div className="flex items-center space-x-2.5">
                                  <span className={`font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                    {fix.home_team?.name}
                                  </span>
                                  <span className="px-2 py-0.5 rounded-lg bg-emerald-500/20 text-emerald-400 font-mono font-black text-xs border border-emerald-500/40 shadow-sm">
                                    {fix.home_score} - {fix.away_score} (FT)
                                  </span>
                                  <span className={`font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                    {fix.away_team?.name}
                                  </span>
                                </div>
                              </td>

                              <td className="py-3 px-3 text-center">
                                {fix.over_1_5_hit ? (
                                  <span className="px-2.5 py-1 rounded-full text-xs font-extrabold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 inline-flex items-center gap-1">
                                    <CheckCircle2 className="w-3.5 h-3.5" />
                                    <span>Over 1.5 HIT</span>
                                  </span>
                                ) : (
                                  <span className="px-2.5 py-1 rounded-full text-xs font-extrabold bg-rose-500/20 text-rose-400 border border-rose-500/40 inline-flex items-center gap-1">
                                    <XCircle className="w-3.5 h-3.5" />
                                    <span>Under 1.5</span>
                                  </span>
                                )}
                              </td>

                              <td className="py-3 px-3 text-center font-mono font-bold text-emerald-400">
                                {over15Pct}%
                              </td>

                              <td className="py-3 px-3 text-right">
                                <button
                                  onClick={(e) => { e.stopPropagation(); setSelectedFixture(fix); }}
                                  className="px-2.5 py-1 rounded-lg text-emerald-400 hover:text-white hover:bg-emerald-600/20 border border-emerald-500/30 transition-all inline-flex items-center gap-1 font-semibold text-xs"
                                >
                                  <span>Details</span>
                                  <ArrowUpRight className="w-3.5 h-3.5" />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </section>

            {/* MAIN TABLE 2: TOP 20 FINISHED FIXTURES OF THE SELECTED DAY SORTED DESCENDING */}
            <section className="space-y-4">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 border-b pb-3 border-slate-800">
                <div className="flex items-center space-x-2">
                  <div className="p-2 rounded-xl bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    <List className="w-5 h-5" />
                  </div>
                  <div>
                    <h2 className={`text-base sm:text-lg font-extrabold tracking-tight ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                      Table 2: Top 20 Finished Fixtures ({selectedPickDay === 'ALL_DAYS' ? 'All Match Days' : formatDayTitle(selectedPickDay)})
                    </h2>
                    <p className="text-xs text-slate-400">
                      Top 20 finished matches with &ge; 50% Over 1.5 probability sorted in descending order for {selectedPickDay === 'ALL_DAYS' ? 'all match days' : formatDayTitle(selectedPickDay)}.
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2 w-full sm:w-auto justify-between sm:justify-end">
                  <span className="text-xs font-semibold text-slate-400">
                    Showing <strong className="text-emerald-400">{filteredFinishedFixtures.length}</strong> results
                  </span>
                  <button
                    onClick={fetchFinishedFixtures}
                    className="p-2 rounded-xl border bg-slate-900 border-slate-800 text-slate-300 hover:text-white"
                    title="Refresh Finished Results"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${loadingFinished ? 'animate-spin' : ''}`} />
                  </button>
                </div>
              </div>

              {/* Finished Controls / Search Bar */}
              <div className={`rounded-2xl p-3.5 sm:p-4 border shadow-md ${darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200'}`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="relative flex-1 min-w-[200px] max-w-sm">
                    <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                    <input
                      type="text"
                      placeholder="Search finished match or league..."
                      value={searchTerm}
                      onChange={(e) => setSearchTerm(e.target.value)}
                      className={`w-full h-10 border rounded-xl pl-10 pr-3.5 text-xs focus:outline-none focus:border-emerald-500 ${
                        darkMode ? 'bg-slate-950 border-slate-800 text-white' : 'bg-slate-50 border-slate-300 text-slate-900'
                      }`}
                    />
                  </div>

                  <div className="flex items-center gap-2">
                    <select
                      value={selectedLeague}
                      onChange={(e) => setSelectedLeague(e.target.value)}
                      className={`h-10 border px-3 rounded-xl text-xs font-bold focus:outline-none ${
                        darkMode ? 'bg-slate-950 border-slate-800 text-emerald-400' : 'bg-slate-50 border-slate-300 text-emerald-800'
                      }`}
                    >
                      <option value="ALL">All Competitions</option>
                      {Array.from(new Set(finishedFixtures.map(f => f.league?.name).filter(Boolean))).map(l => (
                        <option key={l} value={l}>{l}</option>
                      ))}
                    </select>
                  </div>
                </div>
              </div>

              {/* Finished Results Table & Mobile View */}
              {loadingFinished ? (
                <div className="p-12 text-center space-y-3">
                  <RefreshCw className="w-8 h-8 text-emerald-500 animate-spin mx-auto" />
                  <p className="text-xs font-bold text-slate-400">Loading completed match results...</p>
                </div>
              ) : filteredFinishedFixtures.length === 0 ? (
                <div className="p-12 text-center space-y-3 rounded-3xl border border-slate-800 bg-slate-900/60">
                  <Layers className="w-10 h-10 text-slate-500 mx-auto" />
                  <p className="text-sm font-bold text-slate-300">No finished matches found for {selectedPickDay === 'ALL_DAYS' ? 'all match days' : formatDayTitle(selectedPickDay)}</p>
                  <p className="text-xs text-slate-400">Try selecting another match day from the dropdown above.</p>
                </div>
              ) : (
                <div className={`rounded-3xl border overflow-hidden transition-colors shadow-lg ${
                  darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200'
                }`}>
                  {/* Mobile Accordion View (< sm) */}
                  <div className="block sm:hidden p-2 space-y-2">
                    {filteredFinishedFixtures.map(fix => {
                      const over15Pct = Math.round((fix.prediction?.over_1_5_probability || 0) * 100);
                      const isExpanded = expandedMobileRows.has(`finished-${fix.id}`);

                      return (
                        <div
                          key={`finished-mobile-${fix.id}`}
                          className={`rounded-xl border transition-all overflow-hidden ${
                            darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-white border-slate-200'
                          }`}
                        >
                          <div
                            onClick={() => toggleMobileRow(`finished-${fix.id}`)}
                            className="p-3 flex items-center justify-between gap-2 cursor-pointer active:bg-slate-800/40"
                          >
                            <div className="flex flex-col min-w-0 flex-1">
                              <div className="flex items-center gap-1.5 truncate">
                                <span className={`font-extrabold text-xs truncate ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                  {fix.home_team?.name}
                                </span>
                                <span className="px-2 py-0.5 rounded-lg bg-emerald-500/20 text-emerald-400 font-mono font-black text-xs border border-emerald-500/40 shrink-0">
                                  {fix.home_score} - {fix.away_score}
                                </span>
                                <span className={`font-extrabold text-xs truncate ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                  {fix.away_team?.name}
                                </span>
                              </div>
                              <span className="text-[10px] text-slate-400 truncate">
                                {fix.league?.name} &bull; {formatDateGMT1(fix.match_date)}
                              </span>
                            </div>

                            <div className="flex items-center gap-1.5 shrink-0">
                              {fix.over_1_5_hit ? (
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 flex items-center gap-1">
                                  <CheckCircle2 className="w-3 h-3" />
                                  <span>Hit</span>
                                </span>
                              ) : (
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/40 flex items-center gap-1">
                                  <XCircle className="w-3 h-3" />
                                  <span>Under</span>
                                </span>
                              )}
                              <div className="p-1 rounded-lg text-slate-400">
                                {isExpanded ? <ChevronUp className="w-4 h-4 text-emerald-400" /> : <ChevronDown className="w-4 h-4" />}
                              </div>
                            </div>
                          </div>

                          {isExpanded && (
                            <div className={`p-3 border-t space-y-2 text-xs transition-all ${
                              darkMode ? 'border-slate-800/80 bg-slate-900/90' : 'border-slate-100 bg-slate-50'
                            }`}>
                              <div className="grid grid-cols-2 gap-2 text-[11px]">
                                <div className="p-2 rounded-lg bg-slate-800/40 border border-slate-700/50 flex flex-col justify-center">
                                  <span className="text-slate-400 font-semibold text-[10px]">Predicted Over 1.5%</span>
                                  <span className="font-mono font-bold text-emerald-400 text-xs mt-0.5">{over15Pct}%</span>
                                </div>
                                <div className="p-2 rounded-lg bg-slate-800/40 border border-slate-700/50 flex flex-col justify-center">
                                  <span className="text-slate-400 font-semibold text-[10px]">Actual Goals Scored</span>
                                  <span className="font-mono font-bold text-slate-200 text-xs mt-0.5">{fix.total_goals} Goals</span>
                                </div>
                              </div>

                              <button
                                onClick={(e) => { e.stopPropagation(); setSelectedFixture(fix); }}
                                className="w-full py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs flex items-center justify-center gap-1.5 shadow"
                              >
                                <span>View Full Prediction Analytics</span>
                                <ArrowUpRight className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* Desktop Table View (>= sm) */}
                  <div className="hidden sm:block overflow-x-auto custom-scrollbar">
                    <table className="w-full text-left border-collapse">
                      <thead>
                        <tr className={`border-b text-[11px] font-extrabold uppercase tracking-wider ${
                          darkMode ? 'border-slate-800 bg-slate-950/80 text-slate-400' : 'border-slate-200 bg-slate-100 text-slate-600'
                        }`}>
                          <th className="py-3.5 px-4">Match Date (GMT+1)</th>
                          <th className="py-3.5 px-4">Competition</th>
                          <th className="py-3.5 px-4">Matchup & Final Score</th>
                          <th className="py-3.5 px-4 text-center">Actual Goals</th>
                          <th className="py-3.5 px-4 text-center">Over 1.5 Outcome</th>
                          <th className="py-3.5 px-4 text-center">Pred. Over 1.5%</th>
                          <th className="py-3.5 px-4 text-right">Details</th>
                        </tr>
                      </thead>
                      <tbody className={`divide-y text-xs ${darkMode ? 'divide-slate-800/80' : 'divide-slate-100'}`}>
                        {filteredFinishedFixtures.map(fix => {
                          const over15Pct = Math.round((fix.prediction?.over_1_5_probability || 0) * 100);

                          return (
                            <tr
                              key={`finished-${fix.id}`}
                              onClick={() => setSelectedFixture(fix)}
                              className={`cursor-pointer transition-all ${
                                darkMode ? 'hover:bg-slate-800/50' : 'hover:bg-slate-50'
                              }`}
                            >
                              <td className="py-3.5 px-4 font-bold text-slate-300">
                                {formatDateGMT1(fix.match_date)}
                              </td>
                              <td className="py-3.5 px-4">
                                <span className="text-[11px] font-extrabold uppercase tracking-wide text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-md border border-emerald-500/20">
                                  {fix.league?.name || 'League'}
                                </span>
                              </td>
                              <td className="py-3.5 px-4">
                                <div className="flex items-center space-x-2.5">
                                  <span className={`font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                    {fix.home_team?.name}
                                  </span>
                                  <span className="px-2.5 py-1 rounded-lg bg-emerald-500/20 text-emerald-400 font-mono font-black text-xs border border-emerald-500/40 shadow-sm">
                                    {fix.home_score} - {fix.away_score} (FT)
                                  </span>
                                  <span className={`font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                    {fix.away_team?.name}
                                  </span>
                                </div>
                              </td>
                              <td className="py-3.5 px-4 text-center font-mono font-bold text-slate-200">
                                {fix.total_goals} Goals
                              </td>
                              <td className="py-3.5 px-4 text-center">
                                {fix.over_1_5_hit ? (
                                  <span className="px-3 py-1 rounded-full text-xs font-extrabold bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 inline-flex items-center gap-1">
                                    <CheckCircle2 className="w-3.5 h-3.5" />
                                    <span>Over 1.5 HIT</span>
                                  </span>
                                ) : (
                                  <span className="px-3 py-1 rounded-full text-xs font-extrabold bg-rose-500/20 text-rose-400 border border-rose-500/40 inline-flex items-center gap-1">
                                    <XCircle className="w-3.5 h-3.5" />
                                    <span>Under 1.5</span>
                                  </span>
                                )}
                              </td>
                              <td className="py-3.5 px-4 text-center font-mono font-bold text-emerald-400">
                                {over15Pct}%
                              </td>
                              <td className="py-3.5 px-4 text-right">
                                <button
                                  onClick={(e) => { e.stopPropagation(); setSelectedFixture(fix); }}
                                  className="p-1.5 rounded-lg text-emerald-400 hover:text-white hover:bg-emerald-600/20 border border-emerald-500/30 transition-all inline-flex items-center gap-1 font-semibold text-xs"
                                >
                                  <span>Details</span>
                                  <ArrowUpRight className="w-3.5 h-3.5" />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </section>
          </section>
        ) : (
          <>
        
        {/* Banner Card */}
        <section className={`rounded-2xl sm:rounded-3xl p-4 sm:p-8 border shadow-xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4 sm:gap-6 transition-colors ${
          darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200'
        }`}>
          <div className="space-y-1.5 max-w-2xl">
            <h1 className={`text-xl sm:text-3xl md:text-4xl font-extrabold tracking-tight ${darkMode ? 'text-white' : 'text-slate-900'}`}>
              Soccer Goal Expectations Dashboard
            </h1>
            <p className="text-xs sm:text-sm text-slate-400 leading-relaxed">
              Real-time Poisson goal expectation analytics and probability predictions across Home, Away, 1st Half, and 2nd Half match goal thresholds.
            </p>
          </div>

          {/* Metric Stats (3-column grid) */}
          <div className="grid grid-cols-3 gap-2 sm:gap-3 w-full md:w-auto shrink-0">
            <div className={`p-2.5 sm:p-3.5 rounded-xl sm:rounded-2xl border text-center flex flex-col justify-center min-w-0 ${
              darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-slate-50 border-slate-200'
            }`}>
              <span className="text-[9px] sm:text-[10px] font-bold text-slate-400 uppercase tracking-wider truncate">Fixtures</span>
              <span className={`text-base sm:text-xl font-bold mt-0.5 ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                {summaryStats.total}
              </span>
              <span className="text-[8px] sm:text-[9px] font-semibold text-emerald-400 truncate mt-0.5">
                {selectedPickDay === 'ALL_DAYS' ? 'All Days' : formatDayTitle(selectedPickDay)}
              </span>
            </div>
            <div className={`p-2.5 sm:p-3.5 rounded-xl sm:rounded-2xl border text-center flex flex-col justify-center min-w-0 ${
              darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-slate-50 border-slate-200'
            }`}>
              <span className="text-[9px] sm:text-[10px] font-bold text-slate-400 uppercase tracking-wider truncate">&gt; 1.5 Goals</span>
              <span className="text-base sm:text-xl font-bold text-emerald-400 mt-0.5">{summaryStats.highOver15Count}</span>
              <span className="text-[8px] sm:text-[9px] font-semibold text-slate-500 truncate mt-0.5">Active</span>
            </div>
            <div className={`p-2.5 sm:p-3.5 rounded-xl sm:rounded-2xl border text-center flex flex-col justify-center min-w-0 ${
              darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-slate-50 border-slate-200'
            }`}>
              <span className="text-[9px] sm:text-[10px] font-bold text-slate-400 uppercase tracking-wider truncate">&gt; 2.5 Goals</span>
              <span className="text-base sm:text-xl font-bold text-cyan-400 mt-0.5">{summaryStats.highOver25Count}</span>
              <span className="text-[8px] sm:text-[9px] font-semibold text-slate-500 truncate mt-0.5">Active</span>
            </div>
          </div>
        </section>

        {/* FEATURED TABLE 1: TOP 10 DAILY OVER 1.5 GOALS PICKS (PERCENTAGE COLUMN AFTER MATCHUP) */}
        <section className={`rounded-2xl sm:rounded-3xl border p-4 sm:p-6 space-y-4 shadow-2xl relative overflow-hidden transition-colors ${
          darkMode 
            ? 'bg-gradient-to-br from-slate-900 via-slate-900/90 to-emerald-950/30 border-emerald-500/40' 
            : 'bg-gradient-to-br from-white via-emerald-50/30 to-emerald-100/40 border-emerald-300'
        }`}>
          
          {/* Header & Particular Day Selector */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 sm:gap-4 border-b pb-3.5 border-emerald-500/20">
            <div className="flex items-center space-x-2.5 sm:space-x-3">
              <div className="p-2 sm:p-2.5 rounded-xl sm:rounded-2xl bg-amber-500/20 text-amber-400 border border-amber-500/40 shrink-0">
                <Crown className="w-5 h-5 sm:w-6 sm:h-6 animate-pulse" />
              </div>
              <div>
                <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap">
                  <h2 className={`text-base sm:text-lg font-extrabold tracking-tight ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                    Table 1: Top 5 Daily Picks
                  </h2>
                  <span className="text-[9px] sm:text-[10px] font-bold uppercase px-2 py-0.5 rounded-full bg-amber-400 text-slate-950">
                    {selectedPickDay === 'ALL_DAYS' ? 'All Match Days' : formatDayTitle(selectedPickDay)}
                  </span>
                </div>
                <p className="text-[11px] sm:text-xs text-slate-400 mt-0.5">
                  Top 5 daily picks sorted strictly from highest percentage down for {selectedPickDay === 'ALL_DAYS' ? 'all days' : formatDayTitle(selectedPickDay)}.
                </p>
              </div>
            </div>

            {/* Particular Day Filter Dropdown */}
            <div className={`flex items-center gap-2 border px-2.5 sm:px-3 py-1.5 rounded-xl shadow-sm shrink-0 w-full sm:w-auto justify-between sm:justify-start ${
              darkMode ? 'bg-slate-950 border-slate-800' : 'bg-white border-slate-300'
            }`}>
              <div className="flex items-center gap-1.5">
                <Calendar className="w-3.5 h-3.5 sm:w-4 sm:h-4 text-emerald-400" />
                <span className="text-xs font-semibold text-slate-400">Match Day:</span>
              </div>
              <select
                value={selectedPickDay}
                onChange={(e) => setSelectedPickDay(e.target.value)}
                className={`bg-transparent text-xs font-bold cursor-pointer focus:outline-none ${
                  darkMode ? 'text-emerald-400' : 'text-emerald-800'
                }`}
              >
                <option value="ALL_DAYS" className={darkMode ? 'bg-slate-900' : 'bg-white'}>All Match Days</option>
                {availableMatchDays.map(dayKey => (
                  <option key={dayKey} value={dayKey} className={darkMode ? 'bg-slate-900' : 'bg-white'}>
                    {formatDayTitle(dayKey)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Table 1: Featured Picks for Selected Day */}
          {top5Over15Picks.length === 0 ? (
            <div className="p-8 text-center space-y-3">
              <Layers className="w-8 h-8 text-slate-500 mx-auto" />
              <p className="text-xs font-bold text-slate-400">
                No scheduled matches with predictions found for {selectedPickDay === 'ALL_DAYS' ? 'all days' : formatDayTitle(selectedPickDay)}.
              </p>
              {selectedPickDay !== 'ALL_DAYS' && (
                <button
                  onClick={() => setSelectedPickDay('ALL_DAYS')}
                  className="px-4 py-2 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white transition-all shadow-md active:scale-95"
                >
                  Show All Match Days
                </button>
              )}
            </div>
          ) : (
            <>
              {/* MOBILE ACCORDION VIEW (< sm BREAKPOINT) */}
              <div className="block sm:hidden space-y-2">
                {top5Over15Picks.map((fix, idx) => {
                  const over15Pct = Math.round((fix.prediction?.over_1_5_probability || 0) * 100);
                  const isRank1 = idx === 0;
                  const isTop3 = idx < 3;
                  const isLive = isMatchLive(fix);
                  const isExpanded = expandedMobileRows.has(`top10-${fix.id}`);
                  const brightnessClass = getPercentageColorClass(over15Pct);

                  return (
                    <div 
                      key={`top10-mobile-${fix.id}`}
                      className={`rounded-xl border transition-all overflow-hidden ${
                        isLive
                          ? 'bg-rose-500/10 border-rose-500/40'
                          : isRank1
                          ? darkMode ? 'bg-amber-500/10 border-amber-500/40' : 'bg-amber-50 border-amber-300'
                          : darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-white border-slate-200'
                      }`}
                    >
                      {/* Compact Header Row (Ranking & Team Names) */}
                      <div 
                        onClick={() => toggleMobileRow(`top10-${fix.id}`)}
                        className="p-3 flex items-center justify-between gap-2 cursor-pointer active:bg-slate-800/40"
                      >
                        {/* Left: Rank & Team Matchup */}
                        <div className="flex items-center gap-2.5 min-w-0 flex-1">
                          {/* Rank Badge */}
                          {isRank1 ? (
                            <span className="px-2 py-0.5 rounded-md font-extrabold text-[10px] bg-amber-400 text-slate-950 shadow-sm flex items-center gap-1 font-mono shrink-0">
                              <Crown className="w-3 h-3 fill-current" />
                              <span>#1</span>
                            </span>
                          ) : (
                            <span className={`w-5 h-5 rounded-md font-bold text-[10px] flex items-center justify-center font-mono shrink-0 ${
                              isTop3 ? 'bg-emerald-400 text-slate-950' : 'bg-slate-800 text-slate-300 border border-slate-700'
                            }`}>
                              #{idx + 1}
                            </span>
                          )}

                          {/* Team Names */}
                          <div className="flex flex-col min-w-0 flex-1">
                            <div className="flex items-center gap-1 truncate">
                              <span className={`font-extrabold text-xs truncate ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                {fix.home_team?.name}
                              </span>
                              <span className="text-[10px] font-bold text-slate-400 shrink-0">
                                {(isLive || fix.status === 'FINISHED') ? `${fix.home_score ?? 0}-${fix.away_score ?? 0}` : 'vs'}
                              </span>
                              <span className={`font-extrabold text-xs truncate ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                {fix.away_team?.name}
                              </span>
                            </div>
                            <span className="text-[10px] text-slate-400 truncate">
                              {fix.league?.name || 'League'}
                            </span>
                          </div>
                        </div>

                        {/* Right: Over 1.5 % Badge & Dropdown Chevron */}
                        <div className="flex items-center gap-1.5 shrink-0">
                          <span className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-bold ${brightnessClass}`}>
                            {over15Pct}%
                          </span>
                          <div className="p-1 rounded-lg text-slate-400">
                            {isExpanded ? <ChevronUp className="w-4 h-4 text-emerald-400" /> : <ChevronDown className="w-4 h-4" />}
                          </div>
                        </div>
                      </div>

                      {/* Dropdown Accordion Panel (Clicked Details) */}
                      {isExpanded && (
                        <div className={`p-3 border-t space-y-2 text-xs transition-all ${
                          darkMode ? 'border-slate-800/80 bg-slate-900/90' : 'border-slate-100 bg-slate-50'
                        }`}>
                          <div className="grid grid-cols-2 gap-2 text-[11px]">
                            <div className="p-2 rounded-lg bg-slate-800/40 border border-slate-700/50 flex flex-col justify-center">
                              <span className="text-slate-400 font-semibold text-[10px]">Status & Time (GMT+1)</span>
                              <div className="mt-1">{renderLiveStatusBadge(fix)}</div>
                              <span className="text-[10px] text-slate-300 font-bold mt-1">{formatDateGMT1(fix.match_date)}</span>
                            </div>
                            <div className="p-2 rounded-lg bg-slate-800/40 border border-slate-700/50 flex flex-col justify-center">
                              <span className="text-slate-400 font-semibold text-[10px]">Total Expected Goals (xG)</span>
                              <span className="font-mono font-bold text-slate-200 text-xs mt-0.5">
                                {fix.prediction?.expected_goals_xg?.toFixed(2) || '0.00'}
                              </span>
                            </div>
                          </div>

                          {/* Over 1.5 & Over 2.5 Probability Bar */}
                          <div className="p-2.5 rounded-lg bg-slate-800/40 border border-slate-700/50 space-y-1">
                            <div className="flex justify-between text-[10px] font-bold text-slate-400">
                              <span>Over 1.5 Goals: {over15Pct}%</span>
                              <span>Over 2.5 Goals: {Math.round((fix.prediction?.over_2_5_probability || 0) * 100)}%</span>
                            </div>
                            <div className="w-full bg-slate-700/50 rounded-full h-1.5 overflow-hidden flex">
                              <div className="bg-emerald-400 h-full" style={{ width: `${over15Pct}%` }} />
                              <div className="bg-cyan-400 h-full" style={{ width: `${100 - over15Pct}%` }} />
                            </div>
                          </div>

                          {/* Full Odds Button */}
                          <button
                            onClick={(e) => { e.stopPropagation(); setSelectedFixture(fix); }}
                            className="w-full py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs flex items-center justify-center gap-1.5 shadow"
                          >
                            <span>View Full Odds & Match Analytics</span>
                            <ArrowUpRight className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* DESKTOP MULTI-COLUMN TABLE (>= sm BREAKPOINT) */}
              <div className="hidden sm:block overflow-x-auto custom-scrollbar">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className={`border-b text-[10px] font-black uppercase tracking-wider ${
                      darkMode ? 'border-slate-800/80 text-emerald-400' : 'border-emerald-200 text-emerald-800'
                    }`}>
                      <th className="py-2.5 px-3">Rank</th>
                      <th className="py-2.5 px-3">Status / Kickoff (GMT+1)</th>
                      <th className="py-2.5 px-3">Competition</th>
                      <th className="py-2.5 px-3">Matchup & Live Score</th>
                      <th className="py-2.5 px-3 text-center">Over 1.5 Goals %</th>
                      <th className="py-2.5 px-3 text-center">Total xG</th>
                      <th className="py-2.5 px-3 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className={`divide-y text-xs ${darkMode ? 'divide-slate-800/60' : 'divide-emerald-100'}`}>
                    {top5Over15Picks.map((fix, idx) => {
                      const over15Pct = Math.round((fix.prediction?.over_1_5_probability || 0) * 100);
                      const isRank1 = idx === 0;
                      const isTop3 = idx < 3;
                      const isLive = isMatchLive(fix);
                      const brightnessClass = getPercentageColorClass(over15Pct);

                      return (
                        <tr 
                          key={`top10-${fix.id}`}
                          onClick={() => setSelectedFixture(fix)}
                          className={`cursor-pointer transition-all ${
                            isLive
                              ? 'bg-rose-500/20 border-l-4 border-l-rose-500 shadow-lg font-bold'
                              : isRank1
                              ? darkMode ? 'bg-amber-500/10 border-l-4 border-l-amber-400 hover:bg-amber-500/20' : 'bg-amber-50 border-l-4 border-l-amber-500 hover:bg-amber-100'
                              : isTop3
                              ? darkMode ? 'bg-emerald-500/5 hover:bg-emerald-500/15' : 'bg-emerald-50/50 hover:bg-emerald-100/50'
                              : darkMode ? 'hover:bg-slate-800/50' : 'hover:bg-slate-50'
                          }`}
                        >
                          {/* Rank Badge */}
                          <td className="py-3 px-3">
                            {isRank1 ? (
                              <span className="px-2.5 py-1 rounded-xl font-black text-xs bg-amber-400 text-slate-950 shadow-md flex items-center gap-1 font-mono w-fit">
                                <Crown className="w-3.5 h-3.5 fill-current" />
                                <span>#1 TOP PICK</span>
                              </span>
                            ) : isTop3 ? (
                              <span className="w-7 h-7 rounded-xl font-black text-xs bg-emerald-400 text-slate-950 shadow-sm flex items-center justify-center font-mono">
                                #{idx + 1}
                              </span>
                            ) : (
                              <span className="w-7 h-7 rounded-xl font-black text-xs bg-slate-800 text-slate-300 border border-slate-700 flex items-center justify-center font-mono">
                                #{idx + 1}
                              </span>
                            )}
                          </td>

                          {/* Live Status & Kickoff Date/Time */}
                          <td className="py-3 px-3">
                            <div className="flex flex-col space-y-1">
                              {renderLiveStatusBadge(fix)}
                              <span className={`text-[11px] font-bold ${darkMode ? 'text-slate-300' : 'text-slate-700'}`}>
                                {formatDateGMT1(fix.match_date)}
                              </span>
                            </div>
                          </td>

                          {/* Competition Name Badge */}
                          <td className="py-3 px-3">
                            <span className="text-[11px] font-extrabold uppercase text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-lg border border-emerald-500/20">
                              {fix.league?.name || 'League'}
                            </span>
                          </td>

                          {/* Team Matchup & Live Score */}
                          <td className="py-3 px-3">
                            <div className="flex items-center space-x-2.5">
                              <div className="flex items-center space-x-1.5">
                                {fix.home_team?.logo_url ? (
                                  <img src={fix.home_team.logo_url} alt={fix.home_team.name} className="w-5 h-5 object-contain" />
                                ) : (
                                  <span className="w-5 h-5 rounded bg-slate-800 text-[10px] flex items-center justify-center font-bold text-slate-300">
                                    {fix.home_team?.short_code?.substring(0, 2)}
                                  </span>
                                )}
                                <span className={`font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                  {fix.home_team?.name}
                                </span>
                              </div>
                              
                              {/* Prominent Live Score Box or VS */}
                              {(isLive || fix.status === 'FINISHED') ? (
                                <span className={`px-2.5 py-1 rounded-lg font-mono font-black text-xs shadow-md border ${
                                  isLive ? 'bg-rose-600 text-white border-rose-400 animate-pulse' : 'bg-slate-800 text-slate-200 border-slate-700'
                                }`}>
                                  {fix.home_score ?? 0} - {fix.away_score ?? 0}
                                </span>
                              ) : (
                                <span className="text-[10px] font-black text-slate-500">vs</span>
                              )}

                              <div className="flex items-center space-x-1.5">
                                {fix.away_team?.logo_url ? (
                                  <img src={fix.away_team.logo_url} alt={fix.away_team.name} className="w-5 h-5 object-contain" />
                                ) : (
                                  <span className="w-5 h-5 rounded bg-slate-800 text-[10px] flex items-center justify-center font-bold text-slate-300">
                                    {fix.away_team?.short_code?.substring(0, 2)}
                                  </span>
                                )}
                                <span className={`font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                                  {fix.away_team?.name}
                                </span>
                              </div>
                            </div>
                          </td>

                          {/* Over 1.5 Goals % Badge */}
                          <td className="py-3 px-3 text-center">
                            <span className={`px-3 py-1 rounded-full text-xs font-mono inline-flex items-center gap-1 ${brightnessClass}`}>
                              <Flame className="w-3 h-3 fill-current" />
                              <span>Over 1.5: {over15Pct}%</span>
                            </span>
                          </td>

                          {/* Total xG */}
                          <td className="py-3 px-3 text-center font-mono font-bold text-slate-300">
                            {fix.prediction?.expected_goals_xg?.toFixed(2) || '0.00'}
                          </td>

                          {/* Detail Link */}
                          <td className="py-3 px-3 text-right">
                            <button 
                              onClick={(e) => { e.stopPropagation(); setSelectedFixture(fix); }}
                              className="px-2.5 py-1 rounded-lg bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500 hover:text-slate-950 border border-emerald-500/40 text-xs font-extrabold transition-all inline-flex items-center gap-1"
                            >
                              <span>Odds</span>
                              <ArrowUpRight className="w-3.5 h-3.5" />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>

        {/* Controls Header: Search & Filters Bar */}
        <section className={`rounded-2xl p-3.5 sm:p-4 border shadow-md transition-colors ${
          darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200 shadow-sm'
        }`}>
          <div className="flex flex-wrap items-center justify-between gap-2.5 sm:gap-3 w-full">
            
            {/* Left Group: Search Input & Best 15 Button */}
            <div className="flex flex-wrap items-center gap-2.5 flex-1 min-w-[280px]">
              <div className="relative flex-1 min-w-[180px] max-w-xs sm:max-w-sm">
                <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                <input
                  type="text"
                  placeholder="Search team or league..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className={`w-full h-10 border rounded-xl pl-10 pr-3.5 text-xs transition-all focus:outline-none focus:border-emerald-500 ${
                    darkMode 
                      ? 'bg-slate-950 border-slate-800 text-slate-100 placeholder-slate-500' 
                      : 'bg-slate-50 border-slate-200 text-slate-900 placeholder-slate-400'
                  }`}
                />
              </div>

              {/* Dedicated Best 15 Over 1.5 Toggle Button */}
              <button
                onClick={() => setShowBest15Over15(!showBest15Over15)}
                className={`h-10 px-3.5 rounded-xl border text-xs font-black transition-all flex items-center gap-1.5 shadow-sm shrink-0 ${
                  showBest15Over15
                    ? 'bg-amber-400 text-slate-950 border-amber-300 ring-2 ring-amber-300 animate-pulse'
                    : darkMode 
                    ? 'bg-slate-950 border-slate-800 text-amber-400 hover:bg-slate-900' 
                    : 'bg-slate-50 border-slate-200 text-amber-600 hover:bg-slate-100'
                }`}
              >
                <Flame className="w-3.5 h-3.5 fill-current text-amber-400 shrink-0" />
                <span className="whitespace-nowrap">🔥 Best 15 Over 1.5</span>
              </button>
            </div>

            {/* Right Group: Filter Dropdowns & Sort Selector */}
            <div className="flex flex-wrap items-center gap-2 sm:gap-2.5">
              
              {/* Match Day Selector Dropdown */}
              <div className={`h-10 flex items-center gap-1.5 sm:gap-2 border px-2.5 sm:px-3 rounded-xl shrink-0 ${
                darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
              }`}>
                <Calendar className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                <span className="text-xs font-semibold text-slate-400 shrink-0">Day:</span>
                <select
                  value={selectedPickDay}
                  onChange={(e) => setSelectedPickDay(e.target.value)}
                  className={`bg-transparent text-xs font-bold focus:outline-none cursor-pointer ${
                    darkMode ? 'text-emerald-400' : 'text-emerald-800'
                  }`}
                >
                  <option value="ALL_DAYS" className={darkMode ? 'bg-slate-900' : 'bg-white'}>All Match Days</option>
                  {availableMatchDays.map(dayKey => (
                    <option key={dayKey} value={dayKey} className={darkMode ? 'bg-slate-900' : 'bg-white'}>
                      {formatDayTitle(dayKey)}
                    </option>
                  ))}
                </select>
              </div>

              {/* League Dropdown */}
              <div className={`h-10 flex items-center gap-1.5 sm:gap-2 border px-2.5 sm:px-3 rounded-xl shrink-0 ${
                darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
              }`}>
                <Filter className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                <span className="text-xs font-semibold text-slate-400 shrink-0">League:</span>
                <select
                  value={selectedLeague}
                  onChange={(e) => setSelectedLeague(e.target.value)}
                  className={`bg-transparent text-xs font-bold focus:outline-none cursor-pointer ${
                    darkMode ? 'text-slate-200' : 'text-slate-800'
                  }`}
                >
                  <option value="ALL" className={darkMode ? 'bg-slate-900' : 'bg-white'}>All Competitions</option>
                  {leaguesList.map(l => (
                    <option key={l} value={l} className={darkMode ? 'bg-slate-900' : 'bg-white'}>{l}</option>
                  ))}
                </select>
              </div>

              {/* Sort Selector */}
              <div className={`h-10 flex items-center gap-1.5 sm:gap-2 border px-2.5 sm:px-3 rounded-xl shrink-0 ${
                darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
              }`}>
                <BarChart2 className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                <span className="text-xs font-semibold text-slate-400 shrink-0">Sort:</span>
                <select
                  value={sortBy}
                  onChange={(e) => setSortBy(e.target.value)}
                  className={`bg-transparent text-xs font-bold focus:outline-none cursor-pointer ${
                    darkMode ? 'text-slate-200' : 'text-slate-800'
                  }`}
                >
                  <option value="DATE_ASC" className={darkMode ? 'bg-slate-900' : 'bg-white'}>Kickoff Date (Earliest First)</option>
                  <option value="OVER_1_5_DESC" className={darkMode ? 'bg-slate-900' : 'bg-white'}>Highest Over 1.5 %</option>
                  <option value="HOME_OVER_1_5_DESC" className={darkMode ? 'bg-slate-900' : 'bg-white'}>Highest Home Over 1.5 %</option>
                  <option value="AWAY_OVER_1_5_DESC" className={darkMode ? 'bg-slate-900' : 'bg-white'}>Highest Away Over 1.5 %</option>
                  <option value="FIRST_HALF_OVER_0_5_DESC" className={darkMode ? 'bg-slate-900' : 'bg-white'}>Highest 1st Half Over 0.5 %</option>
                  <option value="SECOND_HALF_OVER_0_5_DESC" className={darkMode ? 'bg-slate-900' : 'bg-white'}>Highest 2nd Half Over 0.5 %</option>
                  <option value="OVER_2_5_DESC" className={darkMode ? 'bg-slate-900' : 'bg-white'}>Highest Over 2.5 %</option>
                  <option value="XG_DESC" className={darkMode ? 'bg-slate-900' : 'bg-white'}>Highest Total xG</option>
                </select>
              </div>

              {/* Clear Filters */}
              {(searchTerm || selectedLeague !== 'ALL' || sortBy !== 'DATE_ASC' || showBest15Over15 || selectedPickDay !== 'ALL_DAYS') && (
                <button
                  onClick={() => { setSearchTerm(''); setSelectedLeague('ALL'); setSortBy('DATE_ASC'); setShowBest15Over15(false); setSelectedPickDay('ALL_DAYS'); }}
                  className="h-10 px-2.5 sm:px-3 flex items-center text-xs text-rose-400 hover:text-rose-300 font-semibold transition-colors shrink-0"
                >
                  Clear Filters
                </button>
              )}
            </div>

          </div>
        </section>

        {/* TABLE 2: MAIN FIXTURES TABLE (MAINTAINED IN ASCENDING DATE ORDER - PERCENTAGE COLUMN AFTER MATCHUP) */}
        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3, 4, 5].map(i => (
              <div key={i} className={`h-16 rounded-2xl border animate-pulse ${
                darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200'
              }`} />
            ))}
          </div>
        ) : error ? (
          <div className={`rounded-3xl p-8 border text-center max-w-xl mx-auto space-y-4 ${
            darkMode ? 'bg-rose-500/10 border-rose-500/30 text-rose-300' : 'bg-rose-50 border-rose-200 text-rose-800'
          }`}>
            <XCircle className="w-12 h-12 text-rose-500 mx-auto" />
            <h3 className="text-lg font-bold">Unable to Load Match Predictions</h3>
            <p className="text-xs opacity-90">{error}</p>
            <button
              onClick={fetchUpcomingFixtures}
              className="px-4 py-2 bg-slate-900 text-white rounded-xl text-xs font-semibold shadow"
            >
              Retry Connection
            </button>
          </div>
        ) : filteredFixtures.length === 0 ? (
          <div className={`rounded-3xl p-12 border text-center max-w-md mx-auto space-y-4 ${
            darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200 shadow-sm'
          }`}>
            <Layers className="w-12 h-12 text-slate-500 mx-auto" />
            <h3 className={`text-lg font-bold ${darkMode ? 'text-white' : 'text-slate-900'}`}>No Matching Fixtures</h3>
            <p className="text-xs text-slate-400">Try clearing search filters or trigger dataset sync.</p>
            <button
              onClick={handleSyncData}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl text-xs font-semibold"
            >
              Sync Dataset
            </button>
          </div>
        ) : viewMode === 'table' ? (
          
          /* COMPACT LIST / TABLE VIEW (MAIN TABLE MAINTAINED IN ASCENDING DATE ORDER) */
          <div className={`rounded-3xl border overflow-hidden transition-colors shadow-lg ${
            darkMode ? 'bg-slate-900/60 border-slate-800' : 'bg-white border-slate-200'
          }`}>
            <div className="p-3 sm:p-4 border-b flex flex-wrap items-center justify-between gap-2 border-slate-800/80">
              <span className="text-xs font-extrabold uppercase text-slate-300 flex items-center gap-2">
                <Calendar className="w-4 h-4 text-emerald-400" />
                {`Table 2: Top 20 Over 1.5 Goal Matches (${selectedPickDay === 'ALL_DAYS' ? 'All Match Days' : formatDayTitle(selectedPickDay)})`}
              </span>
              <div className="flex items-center gap-2 sm:gap-3 text-[10px] sm:text-[11px] font-bold text-slate-400">
                <span>Showing {paginatedFixtures.length} of {filteredFixtures.length} matches</span>
                <span className="px-2 py-0.5 rounded-full bg-slate-800 border border-slate-700 text-emerald-400">
                  Page {currentPage} of {totalPages}
                </span>
              </div>
            </div>

            {/* MOBILE ACCORDION VIEW (< sm BREAKPOINT) */}
            <div className="block sm:hidden p-2 space-y-2">
              {paginatedFixtures.map(fix => {
                const pred = fix.prediction || {};
                const over15Pct = Math.round((pred.over_1_5_probability || 0) * 100);
                const homeWinPct = Math.round((pred.home_win_probability || 0) * 100);
                const drawPct = Math.round((pred.draw_probability || 0) * 100);
                const awayWinPct = Math.round((pred.away_win_probability || 0) * 100);
                const isLive = isMatchLive(fix);
                const isExpanded = expandedMobileRows.has(`table2-${fix.id}`);
                const brightnessClass = getPercentageColorClass(over15Pct);

                return (
                  <div 
                    key={`table2-mobile-${fix.id}`}
                    className={`rounded-xl border transition-all overflow-hidden ${
                      isLive
                        ? 'bg-rose-500/10 border-rose-500/40'
                        : darkMode ? 'bg-slate-950/80 border-slate-800' : 'bg-white border-slate-200'
                    }`}
                  >
                    {/* Compact Header Row (Team Names & Live Score) */}
                    <div 
                      onClick={() => toggleMobileRow(`table2-${fix.id}`)}
                      className="p-3 flex items-center justify-between gap-2 cursor-pointer active:bg-slate-800/40"
                    >
                      {/* Teams Matchup */}
                      <div className="flex flex-col min-w-0 flex-1">
                        <div className="flex items-center gap-1.5 truncate">
                          {fix.home_team?.logo_url && (
                            <img src={fix.home_team.logo_url} alt="" className="w-4 h-4 object-contain shrink-0" />
                          )}
                          <span className={`font-extrabold text-xs truncate ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                            {fix.home_team?.name}
                          </span>
                          <span className="text-[10px] font-bold text-slate-400 shrink-0">
                            {(isLive || fix.status === 'FINISHED') ? `${fix.home_score ?? 0}-${fix.away_score ?? 0}` : 'vs'}
                          </span>
                          {fix.away_team?.logo_url && (
                            <img src={fix.away_team.logo_url} alt="" className="w-4 h-4 object-contain shrink-0" />
                          )}
                          <span className={`font-extrabold text-xs truncate ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                            {fix.away_team?.name}
                          </span>
                        </div>
                        <span className="text-[10px] text-slate-400 truncate">
                          {fix.league?.name || 'League'} &bull; {formatDateGMT1(fix.match_date)}
                        </span>
                      </div>

                      {/* Right: Over 1.5 % Badge & Chevron Toggle */}
                      <div className="flex items-center gap-1.5 shrink-0">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-bold ${brightnessClass}`}>
                          {over15Pct}%
                        </span>
                        <div className="p-1 rounded-lg text-slate-400">
                          {isExpanded ? <ChevronUp className="w-4 h-4 text-emerald-400" /> : <ChevronDown className="w-4 h-4" />}
                        </div>
                      </div>
                    </div>

                    {/* Accordion Expanded Details Panel */}
                    {isExpanded && (
                      <div className={`p-3 border-t space-y-2 text-xs transition-all ${
                        darkMode ? 'border-slate-800/80 bg-slate-900/90' : 'border-slate-100 bg-slate-50'
                      }`}>
                        <div className="grid grid-cols-2 gap-2 text-[11px]">
                          <div className="p-2 rounded-lg bg-slate-800/40 border border-slate-700/50 flex flex-col justify-center">
                            <span className="text-slate-400 font-semibold text-[10px]">Status & Kickoff</span>
                            <div className="mt-1">{renderLiveStatusBadge(fix)}</div>
                            <span className="text-[10px] text-slate-300 font-bold mt-1">{formatDateGMT1(fix.match_date)}</span>
                          </div>
                          <div className="p-2 rounded-lg bg-slate-800/40 border border-slate-700/50 flex flex-col justify-center">
                            <span className="text-slate-400 font-semibold text-[10px]">Expected Goals (xG)</span>
                            <span className="font-mono font-bold text-slate-200 text-xs mt-0.5">
                              {pred.predicted_home_score?.toFixed(2) || '0.00'} - {pred.predicted_away_score?.toFixed(2) || '0.00'} (xG: {pred.expected_goals_xg?.toFixed(2) || '0.00'})
                            </span>
                          </div>
                        </div>

                        {/* 1X2 Probabilities Bar */}
                        <div className="p-2.5 rounded-lg bg-slate-800/40 border border-slate-700/50 space-y-1">
                          <div className="flex justify-between text-[10px] font-bold text-slate-400">
                            <span className="text-emerald-400">Home Win: {homeWinPct}%</span>
                            <span className="text-slate-300">Draw: {drawPct}%</span>
                            <span className="text-cyan-400">Away Win: {awayWinPct}%</span>
                          </div>
                          <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden flex">
                            <div style={{ width: `${homeWinPct}%` }} className="bg-emerald-500" />
                            <div style={{ width: `${drawPct}%` }} className="bg-slate-400" />
                            <div style={{ width: `${awayWinPct}%` }} className="bg-cyan-500" />
                          </div>
                        </div>

                        {/* Action Button */}
                        <button
                          onClick={(e) => { e.stopPropagation(); setSelectedFixture(fix); }}
                          className="w-full py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs flex items-center justify-center gap-1.5 shadow"
                        >
                          <span>View Full Odds & Match Analytics</span>
                          <ArrowUpRight className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* DESKTOP MULTI-COLUMN TABLE (>= sm BREAKPOINT) */}
            <div className="hidden sm:block overflow-x-auto custom-scrollbar">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className={`border-b text-[11px] font-extrabold uppercase tracking-wider ${
                    darkMode ? 'border-slate-800 bg-slate-950/80 text-slate-400' : 'border-slate-200 bg-slate-100 text-slate-600'
                  }`}>
                    <th className="py-3.5 px-4">Status / Kickoff (GMT+1)</th>
                    <th className="py-3.5 px-4">Competition / League</th>
                    <th className="py-3.5 px-4">Matchup & Live Score</th>
                    <th className="py-3.5 px-4 text-center">Over 1.5 Goals %</th>
                    <th className="py-3.5 px-4 text-center">xG (H - A)</th>
                    <th className="py-3.5 px-4 text-center">1X2 Odds</th>
                    <th className="py-3.5 px-4 text-right">Details</th>
                  </tr>
                </thead>
                <tbody className={`divide-y text-xs ${darkMode ? 'divide-slate-800/80' : 'divide-slate-100'}`}>
                  {paginatedFixtures.map(fix => {
                    const pred = fix.prediction || {};
                    const over15Pct = Math.round((pred.over_1_5_probability || 0) * 100);
                    const homeWinPct = Math.round((pred.home_win_probability || 0) * 100);
                    const drawPct = Math.round((pred.draw_probability || 0) * 100);
                    const awayWinPct = Math.round((pred.away_win_probability || 0) * 100);
                    const isLive = isMatchLive(fix);
                    const brightnessClass = getPercentageColorClass(over15Pct);

                    return (
                      <tr 
                        key={fix.id} 
                        onClick={() => setSelectedFixture(fix)}
                        className={`group cursor-pointer transition-all ${
                          isLive
                            ? 'bg-rose-500/20 border-l-4 border-l-rose-500 shadow-lg font-bold'
                            : darkMode ? 'hover:bg-slate-800/50' : 'hover:bg-slate-50'
                        }`}
                      >
                        {/* Status & Kickoff Time */}
                        <td className="py-3 px-4">
                          <div className="flex flex-col space-y-1">
                            {renderLiveStatusBadge(fix)}
                            <span className={`text-[11px] font-bold ${darkMode ? 'text-slate-300' : 'text-slate-700'}`}>
                              {formatDateGMT1(fix.match_date)}
                            </span>
                          </div>
                        </td>

                        {/* Competition / League Name */}
                        <td className="py-3 px-4">
                          <span className="text-[11px] font-extrabold uppercase tracking-wide text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-md border border-emerald-500/20">
                            {fix.league?.name || 'League'}
                          </span>
                        </td>

                        {/* Teams Matchup & Live Score */}
                        <td className="py-3 px-4">
                          <div className="flex items-center space-x-3">
                            <div className="flex items-center space-x-2">
                              {fix.home_team?.logo_url ? (
                                <img src={fix.home_team.logo_url} alt={fix.home_team.name} className="w-5 h-5 object-contain" />
                              ) : (
                                <span className="w-5 h-5 rounded bg-slate-800 text-[10px] flex items-center justify-center font-bold text-slate-300">
                                  {fix.home_team?.short_code?.substring(0, 2)}
                                </span>
                              )}
                              <span className={`font-bold ${darkMode ? 'text-slate-100' : 'text-slate-900'}`}>
                                {fix.home_team?.name}
                              </span>
                            </div>

                            {/* Live Score Pill or VS */}
                            {(isLive || fix.status === 'FINISHED') ? (
                              <span className={`px-2.5 py-1 rounded-lg font-mono font-black text-xs shadow-md border ${
                                isLive ? 'bg-rose-600 text-white border-rose-400 animate-pulse' : 'bg-slate-800 text-slate-200 border-slate-700'
                              }`}>
                                {fix.home_score ?? 0} - {fix.away_score ?? 0}
                              </span>
                            ) : (
                              <span className="text-[10px] font-black text-slate-500 px-1.5 py-0.5 rounded bg-slate-800/40">VS</span>
                            )}

                            <div className="flex items-center space-x-2">
                              {fix.away_team?.logo_url ? (
                                <img src={fix.away_team.logo_url} alt={fix.away_team.name} className="w-5 h-5 object-contain" />
                              ) : (
                                <span className="w-5 h-5 rounded bg-slate-800 text-[10px] flex items-center justify-center font-bold text-slate-300">
                                  {fix.away_team?.short_code?.substring(0, 2)}
                                </span>
                              )}
                              <span className={`font-bold ${darkMode ? 'text-slate-100' : 'text-slate-900'}`}>
                                {fix.away_team?.name}
                              </span>
                            </div>
                          </div>
                        </td>

                        {/* Over 1.5 Goals % Badge */}
                        <td className="py-3 px-4 text-center">
                          <span className={`px-3 py-1 rounded-full text-xs font-mono inline-flex items-center gap-1 ${brightnessClass}`}>
                            <Flame className="w-3 h-3 fill-current" />
                            <span>Over 1.5: {over15Pct}%</span>
                          </span>
                        </td>

                        {/* Expected Goals xG */}
                        <td className="py-3 px-4 text-center">
                          <div className="flex flex-col items-center">
                            <span className="font-mono font-black text-xs text-slate-200">
                              {pred.predicted_home_score?.toFixed(2) || '0.00'} - {pred.predicted_away_score?.toFixed(2) || '0.00'}
                            </span>
                            <span className="text-[10px] text-slate-400">Total: {pred.expected_goals_xg?.toFixed(2) || '0.00'}</span>
                          </div>
                        </td>

                        {/* 1X2 Odds Quick Bar */}
                        <td className="py-3 px-4 text-center w-36">
                          <div className="space-y-1">
                            <div className="flex justify-between text-[10px] font-bold text-slate-400">
                              <span className="text-emerald-400">{homeWinPct}%</span>
                              <span className="text-slate-300">{drawPct}%</span>
                              <span className="text-cyan-400">{awayWinPct}%</span>
                            </div>
                            <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden flex">
                              <div style={{ width: `${homeWinPct}%` }} className="bg-emerald-500" />
                              <div style={{ width: `${drawPct}%` }} className="bg-slate-400" />
                              <div style={{ width: `${awayWinPct}%` }} className="bg-cyan-500" />
                            </div>
                          </div>
                        </td>

                        {/* Detail Button */}
                        <td className="py-3 px-4 text-right">
                          <button 
                            onClick={(e) => { e.stopPropagation(); setSelectedFixture(fix); }}
                            className="p-1.5 rounded-lg text-emerald-400 hover:text-white hover:bg-emerald-600/20 border border-emerald-500/30 transition-all inline-flex items-center gap-1 font-semibold text-xs"
                          >
                            <span>Details</span>
                            <ArrowUpRight className="w-3.5 h-3.5" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Table 2 Pagination Footer Bar */}
            <div className="p-4 border-t border-slate-800/80 flex flex-col sm:flex-row items-center justify-between gap-4">
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <span>Matches per page:</span>
                <select
                  value={pageSize}
                  onChange={(e) => { setPageSize(e.target.value); setCurrentPage(1); }}
                  className={`border px-2.5 py-1 rounded-lg text-xs font-bold focus:outline-none ${
                    darkMode ? 'bg-slate-950 border-slate-800 text-emerald-400' : 'bg-slate-50 border-slate-300 text-emerald-800'
                  }`}
                >
                  <option value="25" className={darkMode ? 'bg-slate-900' : 'bg-white'}>25</option>
                  <option value="50" className={darkMode ? 'bg-slate-900' : 'bg-white'}>50</option>
                  <option value="100" className={darkMode ? 'bg-slate-900' : 'bg-white'}>100</option>
                  <option value="ALL" className={darkMode ? 'bg-slate-900' : 'bg-white'}>All</option>
                </select>
              </div>

              {pageSize !== 'ALL' && totalPages > 1 && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
                    disabled={currentPage === 1}
                    className="px-3 py-1.5 text-xs font-bold rounded-xl border transition-all disabled:opacity-40 disabled:cursor-not-allowed bg-slate-900 border-slate-800 text-slate-200 hover:bg-slate-800"
                  >
                    Previous
                  </button>

                  <span className="text-xs font-extrabold text-slate-300 px-3 py-1 rounded-lg bg-slate-950 border border-slate-800">
                    {currentPage} / {totalPages}
                  </span>

                  <button
                    onClick={() => setCurrentPage(prev => Math.min(prev + 1, totalPages))}
                    disabled={currentPage === totalPages}
                    className="px-3 py-1.5 text-xs font-bold rounded-xl border transition-all disabled:opacity-40 disabled:cursor-not-allowed bg-slate-900 border-slate-800 text-slate-200 hover:bg-slate-800"
                  >
                    Next
                  </button>
                </div>
              )}
            </div>
          </div>
        ) : (
          
          /* GRID VIEW MODE */
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredFixtures.map(fix => {
              const pred = fix.prediction || {};
              const over15Pct = Math.round((pred.over_1_5_probability || 0) * 100);
              const homeWinPct = Math.round((pred.home_win_probability || 0) * 100);
              const drawPct = Math.round((pred.draw_probability || 0) * 100);
              const awayWinPct = Math.round((pred.away_win_probability || 0) * 100);
              const isLive = isMatchLive(fix);
              const brightnessClass = getPercentageColorClass(over15Pct);

              return (
                <div 
                  key={fix.id}
                  onClick={() => setSelectedFixture(fix)}
                  className={`rounded-3xl p-6 border flex flex-col justify-between space-y-5 cursor-pointer transition-all group ${
                    isLive
                      ? 'bg-slate-900 border-rose-500 shadow-rose-500/20 shadow-2xl animate-pulse ring-2 ring-rose-500'
                      : darkMode 
                      ? 'bg-slate-900/60 border-slate-800 hover:border-emerald-500/40 hover:bg-slate-900/90' 
                      : 'bg-white border-slate-200 hover:border-emerald-500/40 shadow-sm hover:shadow-md'
                  }`}
                >
                  {/* Card Header: Stating Competition & Live Status */}
                  <div className={`flex items-center justify-between border-b pb-3 ${
                    darkMode ? 'border-slate-800/80' : 'border-slate-100'
                  }`}>
                    <span className="text-[11px] font-extrabold uppercase tracking-wider text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-md border border-emerald-500/20">
                      {fix.league?.name || 'League'}
                    </span>
                    {renderLiveStatusBadge(fix)}
                  </div>

                  <div className="flex items-center gap-1.5 text-xs text-slate-300 font-semibold">
                    <Clock className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                    <span>{formatDateGMT1(fix.match_date)}</span>
                  </div>

                  {/* Team Matchup Banner */}
                  <div className="grid grid-cols-7 items-center gap-2">
                    
                    {/* Home Team */}
                    <div className="col-span-3 flex flex-col items-center text-center space-y-1.5">
                      <div className={`w-12 h-12 rounded-2xl border flex items-center justify-center p-2 shadow-inner ${
                        darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-100 border-slate-200'
                      }`}>
                        {fix.home_team?.logo_url ? (
                          <img src={fix.home_team.logo_url} alt={fix.home_team.name} className="w-8 h-8 object-contain" />
                        ) : (
                          <span className="font-black text-sm text-emerald-500">
                            {fix.home_team?.short_code || fix.home_team?.name?.substring(0, 3).toUpperCase()}
                          </span>
                        )}
                      </div>
                      <span className={`text-xs font-bold line-clamp-1 ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                        {fix.home_team?.name}
                      </span>
                    </div>

                    {/* Live Score or VS Badge */}
                    <div className="col-span-1 flex flex-col items-center justify-center">
                      {(isLive || fix.status === 'FINISHED') ? (
                        <span className={`text-xs font-black text-white font-mono px-2 py-1 rounded-lg shadow border ${
                          isLive ? 'bg-rose-600 border-rose-400 animate-pulse' : 'bg-slate-800 border-slate-700'
                        }`}>
                          {fix.home_score ?? 0}-{fix.away_score ?? 0}
                        </span>
                      ) : (
                        <span className={`text-[11px] font-black text-slate-400 border w-7 h-7 rounded-full flex items-center justify-center ${
                          darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-100 border-slate-200'
                        }`}>
                          VS
                        </span>
                      )}
                    </div>

                    {/* Away Team */}
                    <div className="col-span-3 flex flex-col items-center text-center space-y-1.5">
                      <div className={`w-12 h-12 rounded-2xl border flex items-center justify-center p-2 shadow-inner ${
                        darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-100 border-slate-200'
                      }`}>
                        {fix.away_team?.logo_url ? (
                          <img src={fix.away_team.logo_url} alt={fix.away_team.name} className="w-8 h-8 object-contain" />
                        ) : (
                          <span className="font-black text-sm text-cyan-500">
                            {fix.away_team?.short_code || fix.away_team?.name?.substring(0, 3).toUpperCase()}
                          </span>
                        )}
                      </div>
                      <span className={`text-xs font-bold line-clamp-1 ${darkMode ? 'text-white' : 'text-slate-900'}`}>
                        {fix.away_team?.name}
                      </span>
                    </div>
                  </div>

                  {/* Over 1.5 Goals Outcome (Right After Matchup - Brightness Color Differentiated) */}
                  <div className={`p-3 rounded-2xl border flex items-center justify-between ${
                    over15Pct >= 80 ? 'bg-emerald-500/20 border-emerald-500/50' : 'bg-slate-900/80 border-slate-800'
                  }`}>
                    <div>
                      <span className="text-[10px] font-bold uppercase text-slate-400 block">Over 1.5 Goals Outcome</span>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-mono inline-block mt-0.5 ${brightnessClass}`}>
                        Over 1.5: {over15Pct}%
                      </span>
                    </div>
                    <Flame className="w-5 h-5 text-amber-400" />
                  </div>

                  {/* 1X2 Bar */}
                  <div className="space-y-1.5">
                    <div className="flex justify-between text-[11px] font-bold text-slate-400">
                      <span className="text-emerald-400">1: {homeWinPct}%</span>
                      <span className="text-slate-300">X: {drawPct}%</span>
                      <span className="text-cyan-400">2: {awayWinPct}%</span>
                    </div>
                    <div className="h-2 rounded-full flex overflow-hidden border bg-slate-950 border-slate-800">
                      <div style={{ width: `${homeWinPct}%` }} className="bg-emerald-500" />
                      <div style={{ width: `${drawPct}%` }} className="bg-slate-400" />
                      <div style={{ width: `${awayWinPct}%` }} className="bg-cyan-500" />
                    </div>
                  </div>

                  {/* Footer */}
                  <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-400">
                    <span>xG: {pred.expected_goals_xg?.toFixed(2) || '0.00'}</span>
                    <span className="text-emerald-400 font-semibold flex items-center gap-1">
                      Full Details <ChevronRight className="w-3.5 h-3.5" />
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
          </>
        )}
      </main>

      {/* FULL MATCH DETAILS MODAL */}
      {selectedFixture && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-slate-950/85 backdrop-blur-md animate-fadeIn overflow-x-hidden">
          <div className={`rounded-2xl sm:rounded-3xl p-3.5 sm:p-6 w-[95vw] sm:w-full max-w-2xl border space-y-4 sm:space-y-6 relative shadow-2xl max-h-[90vh] sm:max-h-[92vh] overflow-y-auto overflow-x-hidden custom-scrollbar ${
            darkMode ? 'bg-slate-900 border-slate-800 text-slate-100' : 'bg-white border-slate-200 text-slate-900'
          }`}>
            
            {/* Header */}
            <div className={`flex items-start justify-between pb-3.5 border-b gap-2 ${
              darkMode ? 'border-slate-800' : 'border-slate-200'
            }`}>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 flex-wrap mb-1">
                  <span className="text-[10px] sm:text-xs font-extrabold text-emerald-500 uppercase tracking-wider">
                    {selectedFixture.league?.name} &bull; {formatDateGMT1(selectedFixture.match_date)}
                  </span>
                  {renderLiveStatusBadge(selectedFixture)}
                </div>
                <h3 className="text-base sm:text-lg md:text-xl font-black mt-1 flex flex-wrap items-center gap-2">
                  <span>{selectedFixture.home_team?.name}</span>
                  {(isMatchLive(selectedFixture) || selectedFixture.status === 'FINISHED') ? (
                    <span className="px-2.5 py-0.5 rounded-lg bg-rose-600 text-white font-mono font-black text-sm sm:text-lg shadow border border-rose-400 animate-pulse">
                      {selectedFixture.home_score ?? 0} - {selectedFixture.away_score ?? 0}
                    </span>
                  ) : (
                    <span className="text-slate-400 font-extrabold text-xs px-1.5 py-0.5 rounded bg-slate-800/60">VS</span>
                  )}
                  <span>{selectedFixture.away_team?.name}</span>
                </h3>
                {selectedFixture.venue && (
                  <p className="text-[11px] sm:text-xs text-slate-400 mt-1 flex items-center gap-1">
                    <MapPin className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                    <span className="truncate">{selectedFixture.venue}</span>
                  </p>
                )}
              </div>
              <button
                onClick={() => setSelectedFixture(null)}
                className={`p-1.5 sm:p-2 rounded-xl border transition-colors shrink-0 ${
                  darkMode ? 'bg-slate-950 border-slate-800 text-slate-400 hover:text-white' : 'bg-slate-100 border-slate-200 text-slate-600 hover:text-slate-900'
                }`}
              >
                <X className="w-4 h-4 sm:w-5 sm:h-5" />
              </button>
            </div>

            {/* HIGHLIGHTED BEST OUTCOME CARD */}
            {(() => {
              const pred = selectedFixture.prediction || {};
              const best = getBestOutcome(pred);
              return (
                <div className="p-3 sm:p-4 rounded-xl sm:rounded-2xl bg-gradient-to-r from-emerald-500/20 via-emerald-600/10 to-cyan-500/20 border border-emerald-500/40 space-y-2">
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center space-x-2">
                      <Award className="w-4 h-4 sm:w-5 sm:h-5 text-amber-400 shrink-0" />
                      <span className="text-[10px] sm:text-xs font-extrabold uppercase text-emerald-400 tracking-wide">
                        Best Recommended Outcome
                      </span>
                    </div>
                    <span className="px-2 py-0.5 rounded-full text-[9px] sm:text-[10px] font-black uppercase bg-emerald-500 text-slate-950">
                      {best.badge}
                    </span>
                  </div>
                  <div className="flex items-baseline justify-between gap-2 flex-wrap">
                    <h4 className="text-base sm:text-lg font-black text-white">
                      {best.label}
                    </h4>
                    <span className="text-xl sm:text-2xl font-black text-emerald-400 font-mono">
                      {best.pct}%
                    </span>
                  </div>
                  <p className="text-[10px] sm:text-xs text-slate-300">
                    Calculated for {selectedFixture.league?.name || 'this competition'} kicking off at {formatDateGMT1(selectedFixture.match_date)}.
                  </p>
                </div>
              );
            })()}

            {/* FULL GOAL THRESHOLD OUTCOMES */}
            <div className="space-y-2.5">
              <h4 className="text-xs font-bold uppercase text-slate-400 flex items-center gap-2">
                <Target className="w-4 h-4 text-emerald-500" />
                Match Total Goal Probabilities
              </h4>

              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 sm:gap-3">
                <div className={`p-2.5 sm:p-3 rounded-xl sm:rounded-2xl border ${darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'}`}>
                  <span className="text-[10px] sm:text-[11px] font-semibold text-slate-400 block">Over 0.5 Goals</span>
                  <span className="text-base sm:text-lg font-black text-slate-100 font-mono">
                    {Math.round((selectedFixture.prediction?.over_0_5_probability || 0) * 100)}%
                  </span>
                </div>

                <div className="p-2.5 sm:p-3 rounded-xl sm:rounded-2xl border bg-emerald-500/10 border-emerald-500/30">
                  <span className="text-[10px] sm:text-[11px] font-bold text-emerald-400 block">Over 1.5 Goals</span>
                  <span className="text-base sm:text-lg font-black text-emerald-400 font-mono">
                    {Math.round((selectedFixture.prediction?.over_1_5_probability || 0) * 100)}%
                  </span>
                </div>

                <div className={`p-2.5 sm:p-3 rounded-xl sm:rounded-2xl border ${darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'}`}>
                  <span className="text-[10px] sm:text-[11px] font-semibold text-slate-400 block">Over 2.5 Goals</span>
                  <span className="text-base sm:text-lg font-black text-cyan-400 font-mono">
                    {Math.round((selectedFixture.prediction?.over_2_5_probability || 0) * 100)}%
                  </span>
                </div>

                <div className={`p-2.5 sm:p-3 rounded-xl sm:rounded-2xl border ${darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'}`}>
                  <span className="text-[10px] sm:text-[11px] font-semibold text-slate-400 block">Over 3.5 Goals</span>
                  <span className="text-base sm:text-lg font-black text-indigo-400 font-mono">
                    {Math.round((selectedFixture.prediction?.over_3_5_probability || 0) * 100)}%
                  </span>
                </div>

                <div className={`p-2.5 sm:p-3 rounded-xl sm:rounded-2xl border ${darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'}`}>
                  <span className="text-[10px] sm:text-[11px] font-semibold text-slate-400 block">Under 2.5 Goals</span>
                  <span className="text-base sm:text-lg font-black text-amber-400 font-mono">
                    {Math.round((selectedFixture.prediction?.under_2_5_probability || 0) * 100)}%
                  </span>
                </div>

                <div className={`p-2.5 sm:p-3 rounded-xl sm:rounded-2xl border ${darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'}`}>
                  <span className="text-[10px] sm:text-[11px] font-semibold text-slate-400 block">Both Teams to Score</span>
                  <span className="text-base sm:text-lg font-black text-emerald-400 font-mono">
                    {Math.round((selectedFixture.prediction?.btts_probability || 0) * 100)}%
                  </span>
                </div>
              </div>
            </div>

            {/* TEAM SPECIFIC GOAL THRESHOLDS */}
            <div className="space-y-2.5">
              <h4 className="text-xs font-bold uppercase text-slate-400 flex items-center gap-2">
                <Shield className="w-4 h-4 text-cyan-400" />
                Team Specific Goal Predictions
              </h4>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                
                {/* Home Team Goals */}
                <div className={`p-3 sm:p-4 rounded-xl sm:rounded-2xl border space-y-2 ${
                  darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
                }`}>
                  <span className="text-xs font-bold text-emerald-400 block truncate">
                    {selectedFixture.home_team?.name} (Home) Goals
                  </span>
                  <div className="grid grid-cols-3 gap-1.5 text-center">
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">Over 0.5</span>
                      <span className="text-xs sm:text-sm font-black text-emerald-400 font-mono">
                        {Math.round((selectedFixture.prediction?.home_over_0_5_probability || 0) * 100)}%
                      </span>
                    </div>
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">Over 1.5</span>
                      <span className="text-xs sm:text-sm font-black text-emerald-400 font-mono">
                        {Math.round((selectedFixture.prediction?.home_over_1_5_probability || 0) * 100)}%
                      </span>
                    </div>
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">Over 2.5</span>
                      <span className="text-xs sm:text-sm font-black text-emerald-400 font-mono">
                        {Math.round((selectedFixture.prediction?.home_over_2_5_probability || 0) * 100)}%
                      </span>
                    </div>
                  </div>
                </div>

                {/* Away Team Goals */}
                <div className={`p-3 sm:p-4 rounded-xl sm:rounded-2xl border space-y-2 ${
                  darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
                }`}>
                  <span className="text-xs font-bold text-cyan-400 block truncate">
                    {selectedFixture.away_team?.name} (Away) Goals
                  </span>
                  <div className="grid grid-cols-3 gap-1.5 text-center">
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">Over 0.5</span>
                      <span className="text-xs sm:text-sm font-black text-cyan-400 font-mono">
                        {Math.round((selectedFixture.prediction?.away_over_0_5_probability || 0) * 100)}%
                      </span>
                    </div>
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">Over 1.5</span>
                      <span className="text-xs sm:text-sm font-black text-cyan-400 font-mono">
                        {Math.round((selectedFixture.prediction?.away_over_1_5_probability || 0) * 100)}%
                      </span>
                    </div>
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">Over 2.5</span>
                      <span className="text-xs sm:text-sm font-black text-cyan-400 font-mono">
                        {Math.round((selectedFixture.prediction?.away_over_2_5_probability || 0) * 100)}%
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* HALF-BY-HALF BREAKDOWN */}
            <div className="space-y-2.5">
              <h4 className="text-xs font-bold uppercase text-slate-400 flex items-center gap-2">
                <Split className="w-4 h-4 text-amber-400" />
                1st Half & 2nd Half Goal Breakdown
              </h4>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                
                {/* 1st Half Goals */}
                <div className={`p-3 sm:p-4 rounded-xl sm:rounded-2xl border space-y-2 ${
                  darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
                }`}>
                  <div className="flex items-center justify-between border-b pb-1.5 border-slate-800">
                    <span className="text-xs font-extrabold text-amber-400">1st Half Expectations</span>
                    <span className="text-[10px] sm:text-[11px] font-mono text-slate-300">
                      xG: {selectedFixture.prediction?.first_half_xg?.toFixed(2) || '0.00'}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-center">
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">1H Over 0.5 Goals</span>
                      <span className="text-xs sm:text-base font-black text-amber-400 font-mono">
                        {Math.round((selectedFixture.prediction?.first_half_over_0_5_probability || 0) * 100)}%
                      </span>
                    </div>
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">1H Over 1.5 Goals</span>
                      <span className="text-xs sm:text-base font-black text-amber-400 font-mono">
                        {Math.round((selectedFixture.prediction?.first_half_over_1_5_probability || 0) * 100)}%
                      </span>
                    </div>
                  </div>
                </div>

                {/* 2nd Half Goals */}
                <div className={`p-3 sm:p-4 rounded-xl sm:rounded-2xl border space-y-2 ${
                  darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
                }`}>
                  <div className="flex items-center justify-between border-b pb-1.5 border-slate-800">
                    <span className="text-xs font-extrabold text-emerald-400">2nd Half Expectations</span>
                    <span className="text-[10px] sm:text-[11px] font-mono text-slate-300">
                      xG: {selectedFixture.prediction?.second_half_xg?.toFixed(2) || '0.00'}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-center">
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">2H Over 0.5 Goals</span>
                      <span className="text-xs sm:text-base font-black text-emerald-400 font-mono">
                        {Math.round((selectedFixture.prediction?.second_half_over_0_5_probability || 0) * 100)}%
                      </span>
                    </div>
                    <div className="p-1.5 rounded-lg bg-slate-900/60 border border-slate-800">
                      <span className="text-[9px] sm:text-[10px] text-slate-400 block">2H Over 1.5 Goals</span>
                      <span className="text-xs sm:text-base font-black text-emerald-400 font-mono">
                        {Math.round((selectedFixture.prediction?.second_half_over_1_5_probability || 0) * 100)}%
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* EXPECTED GOALS (xG) & 1X2 PROBABILITIES */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              
              {/* xG Card */}
              <div className={`p-3 sm:p-4 rounded-xl sm:rounded-2xl border space-y-2 ${
                darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
              }`}>
                <span className="text-xs font-bold uppercase text-slate-400 block">Expected Goals (xG)</span>
                <div className="grid grid-cols-3 gap-1.5 text-center">
                  <div>
                    <span className="text-[9px] sm:text-[10px] text-slate-400 block">Home xG</span>
                    <span className="text-sm sm:text-base font-black text-emerald-400 font-mono">
                      {selectedFixture.prediction?.predicted_home_score?.toFixed(2) || '0.00'}
                    </span>
                  </div>
                  <div className="border-x border-slate-800">
                    <span className="text-[9px] sm:text-[10px] text-slate-400 block">Away xG</span>
                    <span className="text-sm sm:text-base font-black text-cyan-400 font-mono">
                      {selectedFixture.prediction?.predicted_away_score?.toFixed(2) || '0.00'}
                    </span>
                  </div>
                  <div>
                    <span className="text-[9px] sm:text-[10px] text-slate-400 block">Total xG</span>
                    <span className="text-sm sm:text-base font-black text-white font-mono">
                      {selectedFixture.prediction?.expected_goals_xg?.toFixed(2) || '0.00'}
                    </span>
                  </div>
                </div>
              </div>

              {/* 1X2 Probabilities */}
              <div className={`p-3 sm:p-4 rounded-xl sm:rounded-2xl border space-y-2 ${
                darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
              }`}>
                <span className="text-xs font-bold uppercase text-slate-400 block">1X2 Match Result Probabilities</span>
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-emerald-400 font-bold">1 ({selectedFixture.home_team?.short_code}):</span>
                    <span className="font-mono font-bold">{Math.round((selectedFixture.prediction?.home_win_probability || 0) * 100)}%</span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-300 font-bold">X (Draw):</span>
                    <span className="font-mono font-bold">{Math.round((selectedFixture.prediction?.draw_probability || 0) * 100)}%</span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-cyan-400 font-bold">2 ({selectedFixture.away_team?.short_code}):</span>
                    <span className="font-mono font-bold">{Math.round((selectedFixture.prediction?.away_win_probability || 0) * 100)}%</span>
                  </div>
                </div>
              </div>
            </div>

            {/* TOP 5 SCIPY POISSON SCORELINE PROBABILITIES */}
            <div className="space-y-2.5">
              <h4 className="text-xs font-bold uppercase text-slate-400 flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-amber-500" />
                Top 5 SciPy Poisson Scoreline Predictions
              </h4>

              <div className="space-y-1.5">
                {(selectedFixture.prediction?.top_scorelines || []).map((sc, idx) => (
                  <div 
                    key={sc.score || idx}
                    className={`flex items-center justify-between p-2.5 sm:p-3 rounded-xl sm:rounded-2xl border ${
                      darkMode ? 'bg-slate-950 border-slate-800' : 'bg-slate-50 border-slate-200'
                    }`}
                  >
                    <div className="flex items-center space-x-2.5">
                      <span className={`w-5 h-5 sm:w-6 sm:h-6 rounded-lg font-bold text-[10px] sm:text-xs flex items-center justify-center ${
                        darkMode ? 'bg-slate-800 text-slate-300' : 'bg-slate-200 text-slate-700'
                      }`}>
                        #{idx + 1}
                      </span>
                      <span className="text-sm sm:text-base font-black font-mono">{sc.score}</span>
                    </div>
                    <span className="text-xs sm:text-sm font-black text-emerald-400 font-mono">
                      {(sc.probability * 100).toFixed(1)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Footer */}
            <div className="pt-2 flex justify-end">
              <button
                onClick={() => setSelectedFixture(null)}
                className={`w-full sm:w-auto px-5 py-2.5 rounded-xl text-xs font-bold border transition-colors ${
                  darkMode ? 'bg-slate-950 border-slate-800 hover:bg-slate-800 text-slate-300' : 'bg-slate-100 border-slate-200 hover:bg-slate-200 text-slate-700'
                }`}
              >
                Close Match Details
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Footer */}
      <footer className={`border-t mt-auto py-6 transition-colors ${
        darkMode ? 'border-slate-800/80 bg-slate-950/80' : 'border-slate-200 bg-white'
      }`}>
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between text-xs text-slate-400 gap-4">
          <div className="flex items-center space-x-2">
            <Zap className="w-4 h-4 text-emerald-500" />
            <span>Soccer Goal Predictor &bull; GMT+1 Timings & Goal Analytics</span>
          </div>
          <div>
            <span>FastAPI &bull; SciPy Poisson &bull; React &bull; Tailwind CSS</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
