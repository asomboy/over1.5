import React from 'react';
import { AlertTriangle, RefreshCw, Trash2 } from 'lucide-react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    console.error('React ErrorBoundary caught an unhandled error:', error, errorInfo);
  }

  handleReload = () => {
    window.location.reload();
  };

  handleClearCacheAndReload = async () => {
    try {
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      }
      if ('serviceWorker' in navigator) {
        const registrations = await navigator.serviceWorker.getRegistrations();
        for (const reg of registrations) {
          await reg.unregister();
        }
      }
      localStorage.clear();
      sessionStorage.clear();
    } catch (e) {
      console.error('Cache clearing failed:', e);
    }
    window.location.href = window.location.origin + window.location.pathname + '?reset=' + Date.now();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-center p-4 font-sans antialiased">
          <div className="max-w-md w-full p-6 sm:p-8 rounded-3xl bg-slate-900 border border-slate-800 shadow-2xl space-y-6 text-center animate-fadeIn">
            <div className="w-16 h-16 rounded-2xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-center mx-auto text-amber-400">
              <AlertTriangle className="w-8 h-8 animate-pulse" />
            </div>

            <div className="space-y-2">
              <h1 className="text-xl sm:text-2xl font-black text-white tracking-tight">
                Soccer Goal Predictor
              </h1>
              <p className="text-xs text-slate-400">
                An unexpected display issue occurred. Don't worry, your prediction data is safe.
              </p>
            </div>

            {this.state.error && (
              <div className="p-3 rounded-xl bg-slate-950 border border-slate-800 text-left font-mono text-[11px] text-rose-400 overflow-x-auto max-h-32 leading-relaxed">
                {this.state.error.toString()}
              </div>
            )}

            <div className="flex flex-col gap-3">
              <button
                onClick={this.handleReload}
                className="w-full py-3 px-4 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs flex items-center justify-center gap-2 shadow-lg shadow-emerald-600/20 transition-all active:scale-95"
              >
                <RefreshCw className="w-4 h-4" />
                <span>Reload Application</span>
              </button>

              <button
                onClick={this.handleClearCacheAndReload}
                className="w-full py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white font-semibold text-xs flex items-center justify-center gap-2 border border-slate-700 transition-all"
              >
                <Trash2 className="w-4 h-4 text-amber-400" />
                <span>Clear Cache & Force Refresh</span>
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
