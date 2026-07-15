/**
 * AkibaCryptoBot — Modern Matrix Dashboard
 * Wires every widget to the real FastAPI backend.
 *
 * REST endpoints used:
 *   GET  /api/status              -> header status, uptime
 *   GET  /api/balance             -> balance card
 *   GET  /api/trades              -> active positions table + count card
 *   GET  /api/metrics             -> PnL, win rate, sharpe, drawdown cards
 *   GET  /api/equity-curve        -> Lightweight Charts baseline chart
 *   GET  /api/ohlcv               -> candlestick chart with trade markers
 *   GET  /api/trades/history      -> recent activity feed
 *   GET  /api/strategy            -> strategy label + selector options
 *   GET  /api/config              -> config bar
 *   GET  /api/risk                -> risk panel
 *   GET  /api/correlations        -> correlation matrix
 *   GET  /api/heatmap             -> risk heatmap data
 *   GET  /api/circuit-breaker     -> circuit breaker status
 *   GET  /api/model/status        -> model health cards
 *   GET  /api/model/train/status  -> training job state
 *   GET  /api/strategy/performance -> per-strategy performance
 *   POST /api/strategy/switch     -> strategy switching
 *   POST /api/trading/pause       -> pause button
 *   POST /api/trading/resume      -> resume button
 *
 * WebSocket streams:
 *   /ws/status  -> live status, uptime, circuit breaker (every 5s)
 *   /ws/prices  -> real-time price ticks (every 1s)
 */

const API_BASE = window.location.origin + '/api';
const WS_BASE = (window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host + '/ws';

// ─── DOM Elements ────────────────────────────────────────────────

const dom = {
    // Header
    statusDot: document.getElementById('status-dot'),
    statusText: document.getElementById('status-text'),
    pauseBtn: document.getElementById('pause-btn'),
    tradingModeBadge: document.getElementById('trading-mode-badge'),
    versionBadge: document.getElementById('version-badge'),
    uptime: document.getElementById('uptime'),
    latency: document.getElementById('latency'),
    strategySelect: document.getElementById('strategy-select'),

    // Metric Cards
    pnlValue: document.getElementById('pnl-value'),
    pnlReturn: document.getElementById('pnl-return'),
    winRateValue: document.getElementById('win-rate-value'),
    winLossDetail: document.getElementById('win-loss-detail'),
    sharpeValue: document.getElementById('sharpe-value'),
    sortinoValue: document.getElementById('sortino-value'),
    maxDdValue: document.getElementById('max-dd-value'),
    currentDdValue: document.getElementById('current-dd-value'),
    activePositions: document.getElementById('active-positions'),
    balanceValue: document.getElementById('balance-value'),
    initialBalance: document.getElementById('initial-balance'),

    // Chart
    pnlChart: document.getElementById('pnlChart'),
    chartEmpty: document.getElementById('chart-empty'),
    strategyLabel: document.getElementById('strategy-label'),

    // Candle Chart
    candleChart: document.getElementById('candleChart'),
    candleEmpty: document.getElementById('candle-empty'),
    candlePairSelector: document.getElementById('candle-pair-selector'),

    // Activity Feed
    activityFeed: document.getElementById('activity-feed'),
    activityPagination: document.getElementById('activity-pagination'),
    activityPrev: document.getElementById('activity-prev'),
    activityNext: document.getElementById('activity-next'),
    activityPageLabel: document.getElementById('activity-page-label'),

    // Positions Table
    positionsTbody: document.getElementById('positions-tbody'),

    // Strategy Performance
    strategyPerfContainer: document.getElementById('strategy-perf-container'),
    ensembleSection: document.getElementById('ensemble-section'),
    ensembleComponents: document.getElementById('ensemble-components'),

    // Risk / Correlations / Model Health
    riskContainer: document.getElementById('risk-container'),
    correlationContainer: document.getElementById('correlation-container'),
    modelHealthContainer: document.getElementById('model-health-container'),

    // Config Bar
    configBar: document.getElementById('config-bar'),

    // Circuit Breaker
    cbBanner: document.getElementById('circuit-breaker-banner'),
    cbReason: document.getElementById('circuit-breaker-reason'),
};

// ─── State ───────────────────────────────────────────────────────

let isBotRunning = false;
let isTradingPaused = false;
let equityChart = null;
let equitySeries = null;
let equityRange = '7d';
let equityNeedsFit = true;
let candleChart = null;
let candleSeries = null;
let candleMarkersPlugin = null;
let candlePair = null;
let candleTf = '15m';
let candleNeedsFit = true;
let latestPrices = {};
let wsLastPing = Date.now();
let latestStatusSnapshot = null;
let latestModelSnapshot = null;
let latestTrainingSnapshot = null;

// ─── Initialization ──────────────────────────────────────────────

async function init() {
    setupPauseButton();
    setupStrategySelector();
    setupWebSockets();
    wireActivityPagination();
    wireChartRangeSelector();
    wireCandleTfSelector();

    // Initial REST data fetch (parallel)
    await Promise.allSettled([
        fetchStatus(),
        fetchBalance(),
        fetchTrades(),
        fetchMetrics(),
        fetchEquityCurve(),
        fetchTradeHistory(),
        fetchStrategy(),
        fetchConfig(),
        fetchRisk(),
        fetchCorrelations(),
        fetchModelHealth(),
        fetchTrainingStatus(),
        fetchStrategyPerformance(),
    ]);

    // Periodic polling
    setInterval(fetchBalance, 15000);
    setInterval(fetchTrades, 10000);
    setInterval(fetchMetrics, 30000);
    // Only auto-refresh when the user is viewing the newest page,
    // so polling doesn't yank them back while they're browsing history.
    setInterval(() => {
        if (activityState.offset === 0) fetchTradeHistory();
    }, 20000);
    setInterval(fetchEquityCurve, 60000);
    setInterval(fetchCandles, 60000);
    setInterval(fetchStrategyPerformance, 30000);
    setInterval(fetchRisk, 30000);
    setInterval(fetchCorrelations, 60000);
    setInterval(fetchModelHealth, 60000);
    setInterval(fetchTrainingStatus, 15000);
}

// ─── REST Fetchers ───────────────────────────────────────────────

async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/status`);
        const data = await res.json();
        latestStatusSnapshot = data;
        updateStatusUI(data);
        renderModelOperationsPanel();
    } catch (e) {
        setStatusIndicator(false, 'API UNREACHABLE');
    }
}

async function fetchBalance() {
    try {
        const res = await fetch(`${API_BASE}/balance`);
        const data = await res.json();
        let total = 0;
        if (typeof data === 'object') {
            total = data.USDT || Object.values(data).reduce((a, b) => a + b, 0);
        }
        dom.balanceValue.textContent = `$${total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    } catch (e) {
        dom.balanceValue.textContent = '—';
    }
}

async function fetchTrades() {
    try {
        const res = await fetch(`${API_BASE}/trades`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        let trades = [];
        let count = 0;

        if (data && typeof data === 'object') {
            if (data.trades !== undefined) {
                trades = data.trades;
                count = data.count !== undefined ? data.count : (Array.isArray(trades) ? trades.length : Object.keys(trades).length);
            } else {
                // Handle direct dict or array response
                trades = data;
                count = Array.isArray(trades) ? trades.length : Object.keys(trades).length;
            }
        }

        if (!Array.isArray(trades)) {
            trades = Object.values(trades);
        }

        // Update card
        dom.activePositions.innerHTML = `${count} <span class="text-sm text-gray-500 font-sans font-normal">Pair${count !== 1 ? 's' : ''}</span>`;

        // Update table (8 columns)
        if (trades.length === 0) {
            dom.positionsTbody.innerHTML = `<tr><td colspan="8" class="py-6 text-center text-matrix-dim">No open positions</td></tr>`;
            return;
        }

        dom.positionsTbody.innerHTML = trades.map(t => {
            if (!t) return '';
            const rawSymbol = String(t.symbol || '');
            const symbol = safeText(rawSymbol);
            const entryPrice = toFiniteNumber(t.entry_price);
            const currentPrice = toFiniteNumber(latestPrices[rawSymbol], entryPrice);
            const pnlPct = entryPrice > 0 ? ((currentPrice - entryPrice) / entryPrice * 100) : 0;
            const side = (t.side || 'long').toLowerCase();
            const isLong = side === 'long' || side === 'buy';
            const effectivePnl = isLong ? pnlPct : -pnlPct;
            const amount = toFiniteNumber(t.amount);

            // Map either entry_time or timestamp
            const rawTime = t.entry_time || t.timestamp;
            const entryTime = rawTime ? safeText(new Date(rawTime).toLocaleTimeString()) : '—';

            return `
                <tr class="border-b border-matrix-border/50 hover:bg-matrix-border/30 transition-colors">
                    <td class="py-3 text-white font-bold">${symbol}</td>
                    <td class="py-3 ${isLong ? 'text-matrix-neon' : 'text-red-500'}">${isLong ? 'LONG' : 'SHORT'}</td>
                    <td class="py-3 text-gray-400">${amount > 0 ? amount.toFixed(6) : '—'}</td>
                    <td class="py-3 text-gray-400">$${entryPrice.toLocaleString()}</td>
                    <td class="py-3 text-white">$${currentPrice.toLocaleString()}</td>
                    <td class="py-3 ${effectivePnl >= 0 ? 'text-matrix-neon' : 'text-red-500'}">${effectivePnl >= 0 ? '+' : ''}${effectivePnl.toFixed(2)}%</td>
                    <td class="py-3 text-matrix-dim">${safeText(t.strategy)}</td>
                    <td class="py-3 text-gray-500 text-xs">${entryTime}</td>
                </tr>`;
        }).join('');
    } catch (e) {
        console.error('Fetch trades error:', e);
        dom.positionsTbody.innerHTML = `<tr><td colspan="8" class="py-6 text-center text-matrix-dim">Failed to load positions</td></tr>`;
    }
}

async function fetchMetrics() {
    try {
        const res = await fetch(`${API_BASE}/metrics`);
        const data = await res.json();

        // Total PnL
        const pnl = data.total_pnl ?? null;
        if (pnl !== null) {
            dom.pnlValue.textContent = `${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
            dom.pnlValue.className = `text-3xl font-bold font-mono neon-glow ${pnl >= 0 ? 'text-matrix-neon' : 'text-red-500'}`;
        }

        // Total return percent
        const returnPct = data.total_return_percent ?? null;
        if (returnPct !== null) {
            dom.pnlReturn.textContent = `Return: ${returnPct >= 0 ? '+' : ''}${returnPct.toFixed(2)}%`;
        }

        // Win rate
        const winRate = data.win_rate ?? null;
        if (winRate !== null) {
            dom.winRateValue.textContent = `${(winRate * 100).toFixed(1)}%`;
            dom.winRateValue.className = `mt-2 text-3xl font-bold font-mono ${winRate >= 0.5 ? 'text-matrix-neon' : 'text-red-500'}`;
        }

        // Win/Loss detail
        const wt = data.winning_trades ?? null;
        const lt = data.losing_trades ?? null;
        if (wt !== null && lt !== null) {
            dom.winLossDetail.textContent = `W: ${wt} / L: ${lt}`;
        }

        // Sharpe ratio
        const sharpe = data.sharpe_ratio ?? null;
        if (sharpe !== null) {
            dom.sharpeValue.textContent = sharpe.toFixed(2);
            dom.sharpeValue.className = `mt-2 text-3xl font-bold font-mono ${sharpe >= 1 ? 'text-matrix-neon' : sharpe >= 0 ? 'text-white' : 'text-red-500'}`;
        }

        // Sortino ratio
        const sortino = data.sortino_ratio ?? null;
        if (sortino !== null) {
            dom.sortinoValue.textContent = `Sortino: ${sortino.toFixed(2)}`;
        }

        // Max drawdown
        const maxDd = data.max_drawdown_percent ?? null;
        if (maxDd !== null) {
            dom.maxDdValue.textContent = `-${Math.abs(maxDd).toFixed(2)}%`;
        }

        // Current drawdown
        const currDd = data.current_drawdown_percent ?? null;
        if (currDd !== null) {
            dom.currentDdValue.textContent = `Current: -${Math.abs(currDd).toFixed(2)}%`;
        }

        // Initial balance
        const initBal = data.initial_balance ?? null;
        if (initBal !== null) {
            dom.initialBalance.textContent = `Initial: $${initBal.toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
        }
    } catch (e) {
        // Metrics may not be available yet
    }
}

async function fetchEquityCurve() {
    try {
        const res = await fetch(`${API_BASE}/equity-curve?range=${equityRange}`);
        const data = await res.json();
        const points = (data.data || []).filter(
            p => Number.isFinite(p.time) && Number.isFinite(p.value)
        );

        if (points.length === 0) {
            dom.pnlChart.classList.add('hidden');
            dom.chartEmpty.classList.remove('hidden');
            return;
        }

        dom.pnlChart.classList.remove('hidden');
        dom.chartEmpty.classList.add('hidden');

        renderEquityChart(points, data.initial_balance);
    } catch (e) {
        dom.pnlChart.classList.add('hidden');
        dom.chartEmpty.classList.remove('hidden');
    }
}

function wireChartRangeSelector() {
    document.querySelectorAll('.chart-range-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.range === equityRange) return;
            equityRange = btn.dataset.range;
            equityNeedsFit = true;
            updateRangeButtons();
            fetchEquityCurve();
        });
    });
    updateRangeButtons();
}

function updateRangeButtons() {
    document.querySelectorAll('.chart-range-btn').forEach(btn => {
        const active = btn.dataset.range === equityRange;
        btn.classList.toggle('bg-matrix-neon/10', active);
        btn.classList.toggle('text-matrix-neon', active);
        btn.classList.toggle('text-matrix-dim', !active);
    });
}

const ACTIVITY_PAGE_SIZE = 15;
const activityState = {
    offset: 0,
    total: 0,
};

function renderActivityPagination() {
    if (!dom.activityPagination) return;
    const total = activityState.total || 0;
    if (total <= ACTIVITY_PAGE_SIZE) {
        dom.activityPagination.classList.add('hidden');
        return;
    }
    dom.activityPagination.classList.remove('hidden');
    const page = Math.floor(activityState.offset / ACTIVITY_PAGE_SIZE) + 1;
    const pages = Math.max(1, Math.ceil(total / ACTIVITY_PAGE_SIZE));
    dom.activityPageLabel.textContent = `Page ${page} of ${pages} (${total} trades)`;
    dom.activityPrev.disabled = activityState.offset <= 0;
    dom.activityNext.disabled = activityState.offset + ACTIVITY_PAGE_SIZE >= total;
}

async function fetchTradeHistory() {
    try {
        const res = await fetch(
            `${API_BASE}/trades/history?limit=${ACTIVITY_PAGE_SIZE}&offset=${activityState.offset}`
        );
        const data = await res.json();
        const trades = data.trades || [];
        activityState.total = Number.isFinite(data.total) ? data.total : trades.length;

        if (trades.length === 0) {
            dom.activityFeed.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">No completed trades yet</div>`;
            renderActivityPagination();
            return;
        }

        dom.activityFeed.innerHTML = trades.map(t => {
            const isBuy = (t.side || '').toLowerCase() === 'buy';
            const pnl = toFiniteNumber(t.pnl);
            const pnlPct = t.pnl_percent != null ? t.pnl_percent : null;
            const duration = safeText(t.duration, '');
            const exitReason = String(t.exit_reason || '');
            const time = t.exit_time ? safeText(new Date(t.exit_time).toLocaleTimeString(), '') : '';
            const symbol = safeText(t.symbol);
            const strategyName = safeText(t.strategy, '');

            const typeClass = isBuy
                ? 'text-matrix-neon bg-matrix-neon/10 border-matrix-neon/20'
                : 'text-red-500 bg-red-500/10 border-red-500/20';

            const exitBadge = exitReason ? `<span class="exit-badge ${getExitReasonClass(exitReason)}">${safeText(formatExitReason(exitReason), '')}</span>` : '';

            const pnlStr = pnlPct != null
                ? `<span class="${pnl >= 0 ? 'text-matrix-neon' : 'text-red-500'}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%)</span>`
                : `<span class="${pnl >= 0 ? 'text-matrix-neon' : 'text-red-500'}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</span>`;

            return `
                <div class="flex items-start space-x-3 p-2 rounded hover:bg-matrix-border/30 transition-colors border border-transparent hover:border-matrix-border/50">
                    <div class="${typeClass} px-2 py-0.5 rounded text-xs font-bold border shrink-0">${isBuy ? 'BUY' : 'SELL'}</div>
                    <div class="flex flex-col min-w-0 gap-0.5">
                        <span class="text-white text-xs truncate">${symbol} @ $${toFiniteNumber(t.exit_price).toLocaleString()} &rarr; ${pnlStr}</span>
                        <div class="flex items-center gap-2 flex-wrap">
                            ${exitBadge}
                            ${duration ? `<span class="text-matrix-dim text-[10px]">${duration}</span>` : ''}
                            <span class="text-matrix-dim text-[10px]">${strategyName}${strategyName && time ? ' &middot; ' : ''}${time}</span>
                        </div>
                    </div>
                </div>`;
        }).join('');
        renderActivityPagination();
    } catch (e) {
        dom.activityFeed.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">Failed to load history</div>`;
        renderActivityPagination();
    }
}

function wireActivityPagination() {
    if (!dom.activityPrev || !dom.activityNext) return;
    dom.activityPrev.addEventListener('click', () => {
        const next = Math.max(0, activityState.offset - ACTIVITY_PAGE_SIZE);
        if (next === activityState.offset) return;
        activityState.offset = next;
        fetchTradeHistory();
    });
    dom.activityNext.addEventListener('click', () => {
        const max = Math.max(0, activityState.total - ACTIVITY_PAGE_SIZE);
        const next = Math.min(max, activityState.offset + ACTIVITY_PAGE_SIZE);
        if (next === activityState.offset) return;
        activityState.offset = next;
        fetchTradeHistory();
    });
}

async function fetchStrategy() {
    try {
        const res = await fetch(`${API_BASE}/strategy`);
        const data = await res.json();

        if (data.active_strategy) {
            dom.strategyLabel.textContent = `Strategy: ${data.active_strategy}`;
        }

        // Populate strategy selector
        if (data.available_strategies && data.available_strategies.length > 0) {
            dom.strategySelect.innerHTML = data.available_strategies.map(s => {
                const selected = s === data.active_strategy ? ' selected' : '';
                return `<option value="${safeText(s, '')}"${selected}>${safeText(s, '')}</option>`;
            }).join('');
        }

        // Ensemble components
        if (data.components) {
            dom.ensembleSection.classList.remove('hidden');
            const comps = data.components;
            dom.ensembleComponents.innerHTML = Object.entries(comps).map(([key, val]) => {
                if (typeof val === 'object') {
                    return `<div class="text-matrix-dim">${safeText(key, '')}: <span class="text-white">${safeText(JSON.stringify(val), '')}</span></div>`;
                }
                return `<div class="text-matrix-dim">${safeText(key, '')}: <span class="text-white">${safeText(val, '')}</span></div>`;
            }).join('');
        } else {
            dom.ensembleSection.classList.add('hidden');
        }
    } catch (e) { /* silent */ }
}

async function fetchConfig() {
    try {
        const res = await fetch(`${API_BASE}/config`);
        const data = await res.json();

        const items = [];

        if (data.trading_mode) {
            const modeColor = data.trading_mode === 'paper' ? 'text-yellow-400 border-yellow-400/30' : 'text-red-400 border-red-400/30';
            items.push(`<span class="px-2 py-0.5 rounded border ${modeColor} font-semibold">${safeText(data.trading_mode.toUpperCase(), '')}</span>`);
        }

        if (data.pairs && data.pairs.length > 0) {
            const tags = data.pairs.map(p => `<span class="config-tag">${safeText(p, '')}</span>`).join('');
            items.push(`<span class="text-matrix-dim">Pairs:</span> ${tags}`);
            buildCandlePairSelector(data.pairs);
        }

        if (data.stop_loss_percent != null) {
            items.push(`<span class="text-matrix-dim">SL:</span> <span class="text-red-400">${(data.stop_loss_percent * 100).toFixed(1)}%</span>`);
        }

        if (data.take_profit_percent != null) {
            items.push(`<span class="text-matrix-dim">TP:</span> <span class="text-matrix-neon">${(data.take_profit_percent * 100).toFixed(1)}%</span>`);
        }

        if (data.max_open_trades != null) {
            items.push(`<span class="text-matrix-dim">Max Trades:</span> <span class="text-white">${data.max_open_trades}</span>`);
        }

        if (data.max_position_size != null) {
            items.push(`<span class="text-matrix-dim">Max Pos:</span> <span class="text-white">${(data.max_position_size * 100).toFixed(0)}%</span>`);
        }

        dom.configBar.innerHTML = items.join('<span class="text-matrix-border mx-1">|</span>');
    } catch (e) {
        dom.configBar.innerHTML = '<span class="text-matrix-dim">Config unavailable</span>';
    }
}

async function fetchRisk() {
    try {
        const [riskRes, heatmapRes, cbRes] = await Promise.allSettled([
            fetch(`${API_BASE}/risk`).then(r => r.json()),
            fetch(`${API_BASE}/heatmap`).then(r => r.json()),
            fetch(`${API_BASE}/circuit-breaker`).then(r => r.json()),
        ]);

        const risk = riskRes.status === 'fulfilled' ? riskRes.value : null;
        const heatmap = heatmapRes.status === 'fulfilled' ? heatmapRes.value : null;
        const cb = cbRes.status === 'fulfilled' ? cbRes.value : null;

        let html = '';

        // Portfolio risk score from heatmap
        if (heatmap && heatmap.total_portfolio_risk != null) {
            const riskScore = heatmap.total_portfolio_risk;
            const riskColor = riskScore >= 0.7 ? 'text-red-500' : riskScore >= 0.4 ? 'text-yellow-400' : 'text-matrix-neon';
            html += `
                <div class="flex justify-between items-center">
                    <span class="text-matrix-dim">Portfolio Risk</span>
                    <span class="${riskColor} font-bold text-lg">${(riskScore * 100).toFixed(1)}%</span>
                </div>`;
        }

        // Asset exposures from heatmap
        if (heatmap && heatmap.asset_exposures) {
            html += `<div class="space-y-2 mt-2">`;
            for (const [asset, exposure] of Object.entries(heatmap.asset_exposures)) {
                const exposureValue = toFiniteNumber(exposure);
                const pct = Math.min(Math.abs(exposureValue) * 100, 100);
                html += `
                    <div>
                        <div class="flex justify-between text-xs mb-1">
                            <span class="text-matrix-dim">${safeText(asset, '')}</span>
                            <span class="text-white">${(exposureValue * 100).toFixed(1)}%</span>
                        </div>
                        <div class="w-full bg-matrix-border rounded-full h-1.5">
                            <div class="bg-matrix-neon h-1.5 rounded-full" style="width: ${pct}%"></div>
                        </div>
                    </div>`;
            }
            html += `</div>`;
        }

        // Position sizing params from risk
        if (risk && risk.position_sizing_parameters) {
            const ps = risk.position_sizing_parameters;
            html += `
                <div class="mt-3 pt-3 border-t border-matrix-border space-y-1">
                    <div class="text-matrix-dim text-xs font-semibold tracking-wide mb-1">POSITION SIZING</div>
                    <div class="flex justify-between text-xs"><span class="text-matrix-dim">ATR Period</span><span class="text-white">${safeText(ps.atr_period, '—')}</span></div>
                    <div class="flex justify-between text-xs"><span class="text-matrix-dim">ATR Multiplier</span><span class="text-white">${safeText(ps.atr_multiplier, '—')}</span></div>
                    <div class="flex justify-between text-xs"><span class="text-matrix-dim">Kelly</span><span class="text-white">${ps.kelly_enabled ? `ON (cap: ${safeText(ps.kelly_cap, '—')})` : 'OFF'}</span></div>
                </div>`;
        }

        // Circuit breaker params from risk
        if (risk && risk.circuit_breaker_parameters) {
            const cbp = risk.circuit_breaker_parameters;
            html += `
                <div class="mt-3 pt-3 border-t border-matrix-border space-y-1">
                    <div class="text-matrix-dim text-xs font-semibold tracking-wide mb-1">CIRCUIT BREAKERS</div>
                    <div class="flex justify-between text-xs"><span class="text-matrix-dim">Volatility Thresh</span><span class="text-white">${(cbp.volatility_threshold * 100).toFixed(1)}%</span></div>
                    <div class="flex justify-between text-xs"><span class="text-matrix-dim">Drawdown Thresh</span><span class="text-white">${(cbp.drawdown_threshold * 100).toFixed(1)}%</span></div>
                </div>`;
        }

        // Circuit breaker active status
        if (cb) {
            if (cb.active) {
                dom.cbBanner.classList.remove('hidden');
                dom.cbReason.textContent = cb.reason || 'Unknown reason';
            } else {
                dom.cbBanner.classList.add('hidden');
            }
        }

        dom.riskContainer.innerHTML = html || '<div class="text-matrix-dim text-xs text-center py-4">No risk data available</div>';
    } catch (e) {
        dom.riskContainer.innerHTML = '<div class="text-matrix-dim text-xs text-center py-4">Failed to load risk data</div>';
    }
}

async function fetchCorrelations() {
    try {
        const res = await fetch(`${API_BASE}/correlations`);
        const data = await res.json();
        const corrs = data.correlations || {};

        if (Object.keys(corrs).length === 0) {
            dom.correlationContainer.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">${safeText(data.message, 'No correlation data yet')}</div>`;
            return;
        }

        // Extract unique symbols from pair keys (e.g., "BTC/USDT_ETH/USDT")
        const symbolSet = new Set();
        for (const key of Object.keys(corrs)) {
            const parts = key.split('_');
            if (parts.length === 2) {
                symbolSet.add(parts[0]);
                symbolSet.add(parts[1]);
            }
        }
        const symbols = [...symbolSet].sort();

        if (symbols.length === 0) {
            dom.correlationContainer.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">No correlation data yet</div>`;
            return;
        }

        // Build NxN grid table
        let tableHtml = '<table class="corr-table w-full border-collapse text-xs font-mono"><thead><tr><th></th>';
        for (const sym of symbols) {
            const short = safeText(sym.replace('/USDT', '').replace('/BTC', ''), '');
            tableHtml += `<th class="corr-header">${short}</th>`;
        }
        tableHtml += '</tr></thead><tbody>';

        for (const rowSym of symbols) {
            const rowShort = safeText(rowSym.replace('/USDT', '').replace('/BTC', ''), '');
            tableHtml += `<tr><td class="corr-header text-right pr-2">${rowShort}</td>`;
            for (const colSym of symbols) {
                if (rowSym === colSym) {
                    tableHtml += `<td class="corr-cell corr-self">1.00</td>`;
                } else {
                    const key1 = `${rowSym}_${colSym}`;
                    const key2 = `${colSym}_${rowSym}`;
                    const val = corrs[key1] ?? corrs[key2] ?? null;
                    const numericValue = val == null ? Number.NaN : toFiniteNumber(val, Number.NaN);
                    if (Number.isFinite(numericValue)) {
                        const cls = getCorrClass(numericValue);
                        tableHtml += `<td class="corr-cell ${cls}">${numericValue.toFixed(2)}</td>`;
                    } else {
                        tableHtml += `<td class="corr-cell text-gray-600">—</td>`;
                    }
                }
            }
            tableHtml += '</tr>';
        }
        tableHtml += '</tbody></table>';

        dom.correlationContainer.innerHTML = tableHtml;
    } catch (e) {
        dom.correlationContainer.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">Failed to load correlations</div>`;
    }
}

async function fetchModelHealth() {
    try {
        const res = await fetch(`${API_BASE}/model/status`);
        const data = await res.json();
        latestModelSnapshot = data;
        renderModelOperationsPanel();
    } catch (e) {
        latestModelSnapshot = {
            freqai_active: false,
            reason: 'Failed to load model status',
            models: [],
        };
        renderModelOperationsPanel();
    }
}

async function fetchTrainingStatus() {
    try {
        const res = await fetch(`${API_BASE}/model/train/status`);
        const data = await res.json();
        latestTrainingSnapshot = data;
        renderModelOperationsPanel();
    } catch (e) {
        latestTrainingSnapshot = {
            current_job: null,
            last_job: null,
            can_start_training: false,
            start_blocker: 'training_status_unavailable',
        };
        renderModelOperationsPanel();
    }
}

function renderModelOperationsPanel() {
    const modelData = latestModelSnapshot;
    const trainingData = latestTrainingSnapshot;
    const statusData = latestStatusSnapshot;

    if (!modelData && !trainingData && !statusData) {
        dom.modelHealthContainer.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">Loading model status...</div>`;
        return;
    }

    const sections = [
        renderTrainingSection(trainingData, modelData, statusData),
        renderOperationalAlertsSection(statusData),
        renderModelCardsSection(modelData),
    ].filter(Boolean);

    dom.modelHealthContainer.innerHTML = sections.join('');
}

async function fetchStrategyPerformance() {
    try {
        const res = await fetch(`${API_BASE}/strategy/performance`);
        const data = await res.json();

        const strategies = Object.entries(data);
        if (strategies.length === 0) {
            dom.strategyPerfContainer.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">No strategy data available</div>`;
            return;
        }

        dom.strategyPerfContainer.innerHTML = strategies.map(([name, perf]) => {
            const wr = perf.win_rate != null ? `${(toFiniteNumber(perf.win_rate) * 100).toFixed(1)}%` : '—';
            const pnl = perf.total_pnl != null ? toFiniteNumber(perf.total_pnl) : 0;
            const pnlColor = pnl >= 0 ? 'text-matrix-neon' : 'text-red-500';
            const signals = perf.total_signals ?? 0;
            const lastSig = safeText(perf.last_signal);
            const lastUpdate = perf.last_update ? safeText(new Date(perf.last_update).toLocaleTimeString()) : '—';

            return `
                <div class="border border-matrix-border rounded-lg p-3 hover:border-matrix-dim transition-colors">
                    <div class="flex justify-between items-start mb-2">
                        <span class="text-white text-sm font-bold">${safeText(name, '')}</span>
                        <span class="${pnlColor} text-sm font-mono font-bold">${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toFixed(2)}</span>
                    </div>
                    <div class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
                        <div class="flex justify-between"><span class="text-matrix-dim">Win Rate</span><span class="text-white">${wr}</span></div>
                        <div class="flex justify-between"><span class="text-matrix-dim">Signals</span><span class="text-white">${signals}</span></div>
                        <div class="flex justify-between"><span class="text-matrix-dim">Last Signal</span><span class="text-white">${lastSig}</span></div>
                        <div class="flex justify-between"><span class="text-matrix-dim">Updated</span><span class="text-white">${lastUpdate}</span></div>
                    </div>
                </div>`;
        }).join('');
    } catch (e) {
        dom.strategyPerfContainer.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">Failed to load strategy performance</div>`;
    }
}

// ─── Equity Chart (TradingView Lightweight Charts) ───────────────

function ensureEquityChart() {
    if (equityChart) return;

    equityChart = LightweightCharts.createChart(dom.pnlChart, {
        autoSize: true,
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#008F11',
            fontFamily: "'Space Grotesk', sans-serif",
            // Attribution is rendered as a visible link below the chart
            attributionLogo: false,
        },
        grid: {
            vertLines: { color: '#0A291A' },
            horzLines: { color: '#0A291A' },
        },
        rightPriceScale: { borderColor: '#0A291A' },
        timeScale: {
            borderColor: '#0A291A',
            timeVisible: true,
            secondsVisible: false,
        },
        crosshair: {
            vertLine: { color: '#008F11', labelBackgroundColor: '#06120E' },
            horzLine: { color: '#008F11', labelBackgroundColor: '#06120E' },
        },
        localization: {
            priceFormatter: p => '$' + p.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
            }),
        },
    });

    equitySeries = equityChart.addSeries(LightweightCharts.BaselineSeries, {
        baseValue: { type: 'price', price: 0 },
        topLineColor: '#00FF41',
        topFillColor1: 'rgba(0, 255, 65, 0.20)',
        topFillColor2: 'rgba(0, 255, 65, 0.02)',
        bottomLineColor: '#EF4444',
        bottomFillColor1: 'rgba(239, 68, 68, 0.02)',
        bottomFillColor2: 'rgba(239, 68, 68, 0.20)',
        lineWidth: 2,
        priceLineVisible: false,
    });
}

function renderEquityChart(points, initialBalance) {
    ensureEquityChart();

    // Green above the starting balance, red below it
    const baseline = Number.isFinite(initialBalance) ? initialBalance : points[0].value;
    equitySeries.applyOptions({ baseValue: { type: 'price', price: baseline } });
    equitySeries.setData(points);

    // Fit only on first load / range switch so polling doesn't reset zoom
    if (equityNeedsFit) {
        equityChart.timeScale().fitContent();
        equityNeedsFit = false;
    }
}

// ─── Candlestick Chart (per-pair price action + trade markers) ───

const CANDLE_TF_SECONDS = { '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400 };

function ensureCandleChart() {
    if (candleChart) return;

    candleChart = LightweightCharts.createChart(dom.candleChart, {
        autoSize: true,
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#008F11',
            fontFamily: "'Space Grotesk', sans-serif",
            attributionLogo: false,
        },
        grid: {
            vertLines: { color: '#0A291A' },
            horzLines: { color: '#0A291A' },
        },
        rightPriceScale: { borderColor: '#0A291A' },
        timeScale: {
            borderColor: '#0A291A',
            timeVisible: true,
            secondsVisible: false,
        },
        crosshair: {
            vertLine: { color: '#008F11', labelBackgroundColor: '#06120E' },
            horzLine: { color: '#008F11', labelBackgroundColor: '#06120E' },
        },
    });

    candleSeries = candleChart.addSeries(LightweightCharts.CandlestickSeries, {
        upColor: '#00FF41',
        downColor: '#EF4444',
        borderVisible: false,
        wickUpColor: '#00FF41',
        wickDownColor: '#EF4444',
    });

    candleMarkersPlugin = LightweightCharts.createSeriesMarkers(candleSeries, []);
}

function buildCandlePairSelector(pairs) {
    if (!dom.candlePairSelector || dom.candlePairSelector.childElementCount > 0) return;

    pairs.forEach(pair => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.dataset.pair = pair;
        btn.textContent = pair.replace('/USDT', '');
        btn.className = 'candle-pair-btn px-2.5 py-1 text-matrix-dim hover:text-matrix-neon transition-colors';
        btn.addEventListener('click', () => {
            if (candlePair === pair) return;
            candlePair = pair;
            candleNeedsFit = true;
            updateCandleSelectors();
            fetchCandles();
        });
        dom.candlePairSelector.appendChild(btn);
    });

    if (!candlePair && pairs.length > 0) {
        candlePair = pairs[0];
        updateCandleSelectors();
        fetchCandles();
    }
}

function wireCandleTfSelector() {
    document.querySelectorAll('.candle-tf-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.tf === candleTf) return;
            candleTf = btn.dataset.tf;
            candleNeedsFit = true;
            updateCandleSelectors();
            fetchCandles();
        });
    });
    updateCandleSelectors();
}

function updateCandleSelectors() {
    document.querySelectorAll('.candle-pair-btn').forEach(btn => {
        const active = btn.dataset.pair === candlePair;
        btn.classList.toggle('bg-matrix-neon/10', active);
        btn.classList.toggle('text-matrix-neon', active);
        btn.classList.toggle('text-matrix-dim', !active);
    });
    document.querySelectorAll('.candle-tf-btn').forEach(btn => {
        const active = btn.dataset.tf === candleTf;
        btn.classList.toggle('bg-matrix-neon/10', active);
        btn.classList.toggle('text-matrix-neon', active);
        btn.classList.toggle('text-matrix-dim', !active);
    });
}

async function fetchCandles() {
    if (!candlePair) return;

    try {
        const [ohlcvRes, tradesRes] = await Promise.all([
            fetch(`${API_BASE}/ohlcv?symbol=${encodeURIComponent(candlePair)}&timeframe=${candleTf}&limit=300`),
            fetch(`${API_BASE}/trades/history?limit=100`),
        ]);
        const ohlcv = await ohlcvRes.json();
        const bars = (ohlcv.data || []).filter(b => Number.isFinite(b.time));

        if (bars.length === 0) {
            dom.candleChart.classList.add('hidden');
            dom.candleEmpty.classList.remove('hidden');
            return;
        }

        dom.candleChart.classList.remove('hidden');
        dom.candleEmpty.classList.add('hidden');

        ensureCandleChart();
        candleSeries.setData(bars);

        let trades = [];
        try {
            trades = (await tradesRes.json()).trades || [];
        } catch (e) { /* markers are optional */ }
        candleMarkersPlugin.setMarkers(buildTradeMarkers(trades, bars[0].time));

        if (candleNeedsFit) {
            candleChart.timeScale().fitContent();
            candleNeedsFit = false;
        }
    } catch (e) {
        dom.candleChart.classList.add('hidden');
        dom.candleEmpty.classList.remove('hidden');
    }
}

function buildTradeMarkers(trades, firstBarTime) {
    const tfSec = CANDLE_TF_SECONDS[candleTf] || 900;
    // Snap trade timestamps onto candle buckets so markers attach to bars
    const toBarTime = iso => Math.floor(Date.parse(iso) / 1000 / tfSec) * tfSec;
    const markers = [];

    trades
        .filter(t => t.symbol === candlePair)
        .forEach(t => {
            if (t.entry_time) {
                const time = toBarTime(t.entry_time);
                if (time >= firstBarTime) {
                    const isBuy = t.side === 'buy';
                    markers.push({
                        time,
                        position: isBuy ? 'belowBar' : 'aboveBar',
                        shape: isBuy ? 'arrowUp' : 'arrowDown',
                        color: isBuy ? '#00FF41' : '#EF4444',
                        text: `${isBuy ? 'B' : 'S'} ${Number(t.entry_price).toLocaleString()}`,
                    });
                }
            }
            if (t.exit_time) {
                const time = toBarTime(t.exit_time);
                if (time >= firstBarTime) {
                    const won = (t.pnl ?? 0) >= 0;
                    markers.push({
                        time,
                        position: 'aboveBar',
                        shape: 'arrowDown',
                        color: won ? '#00FF41' : '#EF4444',
                        text: `X ${won ? '+' : ''}${Number(t.pnl ?? 0).toFixed(2)}`,
                    });
                }
            }
        });

    return markers.sort((a, b) => a.time - b.time);
}

// ─── WebSockets ──────────────────────────────────────────────────

function setupWebSockets() {
    connectStatusWS();
    connectPricesWS();
}

function connectStatusWS() {
    const ws = new WebSocket(`${WS_BASE}/status`);

    ws.onmessage = (event) => {
        const now = Date.now();
        dom.latency.textContent = `${now - wsLastPing}ms`;
        wsLastPing = now;

        const { data } = JSON.parse(event.data);
        updateStatusUI(data);
    };

    ws.onclose = () => {
        setStatusIndicator(false, 'DISCONNECTED');
        setTimeout(connectStatusWS, 5000);
    };

    ws.onerror = () => ws.close();
}

function connectPricesWS() {
    const ws = new WebSocket(`${WS_BASE}/prices`);

    ws.onmessage = (event) => {
        const { data } = JSON.parse(event.data);
        if (data && typeof data === 'object') {
            latestPrices = { ...latestPrices, ...data };
        }
    };

    ws.onclose = () => setTimeout(connectPricesWS, 5000);
    ws.onerror = () => ws.close();
}

// ─── UI Updaters ─────────────────────────────────────────────────

function updateStatusUI(data) {
    if (!data) return;

    latestStatusSnapshot = data;

    // Running state
    const isRunning = data.is_running !== undefined ? data.is_running : true;
    isBotRunning = isRunning;
    isTradingPaused = Boolean(data.trading_paused);
    const haltState = data.halt_state || 'none';
    const entriesBlockedReason = data.entries_blocked_reason || null;

    if (data.circuit_breaker_active) {
        setStatusIndicator(false, 'CIRCUIT BREAKER', 'critical');
    } else if (haltState !== 'none') {
        setStatusIndicator(false, 'ENTRIES BLOCKED', 'critical');
    } else if (!isRunning) {
        setStatusIndicator(false, 'BOT STOPPED', 'offline');
    } else if (isTradingPaused) {
        setStatusIndicator(true, 'ENTRIES PAUSED', 'warning');
    } else {
        setStatusIndicator(isRunning);
    }

    // Uptime
    if (data.uptime) {
        dom.uptime.textContent = data.uptime.split('.')[0];
    }

    // Trading mode badge
    if (data.trading_mode) {
        dom.tradingModeBadge.textContent = data.trading_mode.toUpperCase();
        dom.tradingModeBadge.classList.remove('hidden');
        if (data.trading_mode === 'paper') {
            dom.tradingModeBadge.classList.add('text-yellow-400', 'border-yellow-400/30');
        }
    }

    // Circuit breaker
    if (data.circuit_breaker_active) {
        dom.cbBanner.classList.remove('hidden');
        dom.cbReason.textContent = data.circuit_breaker_reason || 'Unknown reason';
    } else if (haltState !== 'none') {
        dom.cbBanner.classList.remove('hidden');
        dom.cbReason.textContent = entriesBlockedReason
            ? `Operational halt: ${entriesBlockedReason}`
            : `Operational halt active (${haltState})`;
    } else if (!data.circuit_breaker_active) {
        dom.cbBanner.classList.add('hidden');
    }

    // Update pause button text
    dom.pauseBtn.textContent = isTradingPaused ? 'RESUME ENTRIES' : 'PAUSE ENTRIES';
}

function setStatusIndicator(isOnline, textOverride = null, tone = 'critical') {
    if (textOverride) {
        dom.statusText.textContent = textOverride;
        if (tone === 'warning') {
            dom.statusText.className = 'text-yellow-400 font-mono tracking-wider';
            dom.statusDot.className = 'w-2 h-2 rounded-full bg-yellow-400 animate-pulse shadow-[0_0_8px_#FACC15]';
        } else if (tone === 'offline') {
            dom.statusText.className = 'text-matrix-dim font-mono tracking-wider';
            dom.statusDot.className = 'w-2 h-2 rounded-full bg-matrix-dim';
        } else {
            dom.statusText.className = 'text-red-500 font-mono tracking-wider';
            dom.statusDot.className = 'w-2 h-2 rounded-full bg-red-500 animate-pulse shadow-[0_0_8px_#EF4444]';
        }
        return;
    }

    if (isOnline) {
        dom.statusText.textContent = 'SYSTEM ONLINE';
        dom.statusText.className = 'text-matrix-neon font-mono tracking-wider';
        dom.statusDot.className = 'w-2 h-2 rounded-full bg-matrix-neon animate-pulse shadow-[0_0_8px_#00FF41]';
    } else {
        dom.statusText.textContent = 'PAUSED';
        dom.statusText.className = 'text-matrix-dim font-mono tracking-wider';
        dom.statusDot.className = 'w-2 h-2 rounded-full bg-matrix-dim';
    }
}

function formatTimestamp(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return date.toLocaleString();
}

function getTrainingStatusMeta(status) {
    switch (status) {
        case 'queued':
            return { icon: '\u{1F9E0}\u23F3', cls: 'text-yellow-400' };
        case 'running':
            return { icon: '\u{1F9E0}\u2699\uFE0F', cls: 'text-blue-400' };
        case 'succeeded':
            return { icon: '\u{1F9E0}\u2705', cls: 'text-matrix-neon' };
        case 'failed':
            return { icon: '\u{1F9E0}\u274C', cls: 'text-red-500' };
        case 'blocked':
            return { icon: '\u{1F9E0}\u26D4', cls: 'text-red-500' };
        default:
            return { icon: '\u{1F9E0}', cls: 'text-matrix-dim' };
    }
}

function renderTrainingSection(trainingData, modelData, statusData) {
    const currentJob = trainingData?.current_job || null;
    const lastJob = trainingData?.last_job || null;
    const activeJob = currentJob || lastJob;
    const activeStatus = activeJob?.status || 'idle';
    const statusMeta = getTrainingStatusMeta(activeStatus);
    const reloadStatus = safeText(
        modelData?.reload_status || activeJob?.reload_status,
        '—'
    );
    const startBlocker = safeText(trainingData?.start_blocker, '—');
    const tradingPaused = Boolean(statusData?.trading_paused);

    const summaryLines = [
        `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Status</span><span class="${statusMeta.cls}">${statusMeta.icon} ${safeText(String(activeStatus).toUpperCase(), 'IDLE')}</span></div>`,
        `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Trading Paused</span><span class="${tradingPaused ? 'text-yellow-400' : 'text-red-400'}">${tradingPaused ? 'yes' : 'no'}</span></div>`,
        `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Can Start</span><span class="${trainingData?.can_start_training ? 'text-matrix-neon' : 'text-red-400'}">${trainingData?.can_start_training ? 'yes' : 'no'}</span></div>`,
        `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Reload Status</span><span class="text-white">${reloadStatus}</span></div>`,
        `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Last Success</span><span class="text-white">${formatTimestamp(modelData?.last_success_at)}</span></div>`,
    ];

    if (activeJob) {
        summaryLines.push(
            `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Job ID</span><span class="text-white truncate max-w-[180px]">${safeText(activeJob.job_id, '—')}</span></div>`,
            `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Requested By</span><span class="text-white">${safeText(activeJob.requested_by, '—')}</span></div>`,
            `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Timeframe</span><span class="text-white">${safeText(activeJob.timeframe, '—')}</span></div>`,
            `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Candles</span><span class="text-white">${activeJob.candles ?? '—'}</span></div>`,
        );
        if (activeJob.progress != null) {
            summaryLines.push(
                `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Progress</span><span class="text-white">${toFiniteNumber(activeJob.progress).toFixed(0)}%</span></div>`
            );
        }
        if (activeJob.error) {
            summaryLines.push(
                `<div class="text-[11px] text-red-400 break-words">Error: ${safeText(activeJob.error, '')}</div>`
            );
        }
        if (activeJob.result_summary) {
            summaryLines.push(
                `<div class="text-[11px] text-matrix-dim break-words">Result: ${safeText(activeJob.result_summary, '')}</div>`
            );
        }
    } else {
        summaryLines.push(
            `<div class="text-[11px] text-matrix-dim">No training job has been queued from this server yet.</div>`
        );
    }

    if (!trainingData?.can_start_training && startBlocker && startBlocker !== '—') {
        summaryLines.push(
            `<div class="text-[11px] text-yellow-400 break-words">Start blocker: ${startBlocker}</div>`
        );
    }

    return `
        <div class="border border-matrix-border rounded-lg p-3 space-y-2">
            <div class="flex items-center justify-between">
                <span class="text-white text-xs font-bold tracking-wide">TRAINING CONTROL</span>
                <span class="text-[10px] text-matrix-dim">API / Telegram aligned</span>
            </div>
            ${summaryLines.join('')}
        </div>`;
}

function renderOperationalAlertsSection(statusData) {
    const alerts = Array.isArray(statusData?.operational_alerts)
        ? statusData.operational_alerts
        : [];
    const haltState = statusData?.halt_state || 'none';
    const entriesBlockedReason = safeText(statusData?.entries_blocked_reason, '—');
    const trainingJob = statusData?.training_job || null;
    const liveRolloutApproval = statusData?.live_rollout_approval || null;
    const latestLiveRolloutEvent = statusData?.latest_live_rollout_event || null;

    const alertLines = [
        `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Halt State</span><span class="${haltState === 'none' ? 'text-matrix-neon' : 'text-red-400'}">${safeText(haltState, 'none')}</span></div>`,
        `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Entries Blocked</span><span class="${entriesBlockedReason === '—' ? 'text-matrix-neon' : 'text-yellow-400'}">${entriesBlockedReason}</span></div>`,
        `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Critical Alerts</span><span class="${(statusData?.active_critical_alert_count || 0) > 0 ? 'text-red-400' : 'text-matrix-neon'}">${statusData?.active_critical_alert_count || 0}</span></div>`,
        `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Status Job Surface</span><span class="text-white">${safeText(trainingJob?.current_job?.status || trainingJob?.last_job?.status, 'idle')}</span></div>`,
    ];

    if (liveRolloutApproval) {
        alertLines.push(
            `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Live Approval Required</span><span class="${liveRolloutApproval.approval_required ? 'text-yellow-400' : 'text-matrix-neon'}">${liveRolloutApproval.approval_required ? 'yes' : 'no'}</span></div>`,
            `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Live Approval Status</span><span class="${liveRolloutApproval.current_approval ? 'text-matrix-neon' : 'text-red-400'}">${safeText(liveRolloutApproval.current_approval?.status, 'missing')}</span></div>`
        );
        if (liveRolloutApproval.current_approval?.actor) {
            alertLines.push(
                `<div class="flex justify-between text-[11px]"><span class="text-matrix-dim">Approved By</span><span class="text-white">${safeText(liveRolloutApproval.current_approval.actor, '')}</span></div>`
            );
        }
    }

    if (latestLiveRolloutEvent) {
        alertLines.push(
            `<div class="text-[11px] text-matrix-dim break-words">Latest rollout event: ${safeText(latestLiveRolloutEvent.event_type, 'unknown')} -&gt; ${safeText(latestLiveRolloutEvent.reason, 'inspect')}</div>`
        );
    }

    if (alerts.length === 0) {
        alertLines.push(
            `<div class="text-[11px] text-matrix-dim">No active operational alerts.</div>`
        );
    } else {
        alertLines.push(
            ...alerts.slice(0, 3).map(alert => {
                const severity = alert?.severity || 'info';
                const severityClass = severity === 'critical'
                    ? 'text-red-400'
                    : severity === 'warning'
                        ? 'text-yellow-400'
                        : 'text-matrix-dim';
                return `<div class="text-[11px] ${severityClass} break-words">${safeText(severity, 'info')}: ${safeText(alert.code, 'unknown')} -&gt; ${safeText(alert.recommended_action, 'inspect')}</div>`;
            })
        );
        if (alerts.length > 3) {
            alertLines.push(
                `<div class="text-[11px] text-matrix-dim">Plus ${alerts.length - 3} more alert(s).</div>`
            );
        }
    }

    return `
        <div class="border border-matrix-border rounded-lg p-3 space-y-2">
            <div class="flex items-center justify-between">
                <span class="text-white text-xs font-bold tracking-wide">OPERATIONS</span>
                <span class="text-[10px] text-matrix-dim">${alerts.length} active alert${alerts.length === 1 ? '' : 's'}</span>
            </div>
            ${alertLines.join('')}
        </div>`;
}

function renderModelCardsSection(modelData) {
    if (!modelData) {
        return `
            <div class="border border-matrix-border rounded-lg p-3">
                <div class="text-matrix-dim text-xs text-center py-4">Loading model status...</div>
            </div>`;
    }

    if (!modelData.freqai_active) {
        return `
            <div class="border border-matrix-border rounded-lg p-3">
                <div class="text-matrix-dim text-xs text-center py-4">${safeText(modelData.reason, 'FreqAI not active')}</div>
            </div>`;
    }

    const models = modelData.models || [];
    if (models.length === 0) {
        return `
            <div class="border border-matrix-border rounded-lg p-3">
                <div class="text-matrix-dim text-xs text-center py-4">No models loaded</div>
            </div>`;
    }

    return models.map(m => {
        const staleClass = m.stale ? 'model-card-stale' : '';
        const ageStr = m.age_hours != null ? `${toFiniteNumber(m.age_hours).toFixed(1)}h ago` : 'Unknown';

        let featuresHtml = '';
        if (m.top_features && m.top_features.length > 0) {
            const maxImp = Math.max(...m.top_features.map(f => toFiniteNumber(f.importance)));
            featuresHtml = `
                <div class="mt-2 space-y-1">
                    ${m.top_features.map(f => {
                        const importance = toFiniteNumber(f.importance);
                        const pct = maxImp > 0 ? (importance / maxImp * 100) : 0;
                        return `
                            <div class="flex items-center gap-2">
                                <span class="text-matrix-dim w-20 truncate text-[10px]">${safeText(f.name, '')}</span>
                                <div class="flex-grow bg-matrix-border rounded-full h-1">
                                    <div class="bg-matrix-neon h-1 rounded-full" style="width: ${pct}%"></div>
                                </div>
                                <span class="text-matrix-dim text-[10px] w-10 text-right">${importance.toFixed(3)}</span>
                            </div>`;
                    }).join('')}
                </div>`;
        }

        return `
            <div class="border border-matrix-border rounded-lg p-3 ${staleClass}">
                <div class="flex justify-between items-start mb-1">
                    <span class="text-white text-xs font-bold">${safeText(m.name, '')}</span>
                    <span class="text-[10px] ${m.stale ? 'text-red-400' : 'text-matrix-dim'}">${ageStr}</span>
                </div>
                <div class="flex gap-3 text-[10px] text-matrix-dim">
                    <span>W: ${m.weight != null ? toFiniteNumber(m.weight).toFixed(2) : '—'}</span>
                    <span>Feat: ${m.feature_count ?? '—'}</span>
                    <span>${m.trained ? 'Trained' : 'Untrained'}</span>
                </div>
                ${featuresHtml}
            </div>`;
    }).join('');
}

// ─── Helpers ─────────────────────────────────────────────────────

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function safeText(value, fallback = '—') {
    if (value === null || value === undefined || value === '') {
        return fallback;
    }
    return escapeHtml(String(value));
}

function toFiniteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function getExitReasonClass(reason) {
    const r = String(reason || '').toLowerCase();
    if (r.includes('take_profit') || r.includes('takeprofit') || r.includes('tp')) return 'exit-badge-tp';
    if (r.includes('stop_loss') || r.includes('stoploss') || r.includes('sl')) return 'exit-badge-sl';
    if (r.includes('trailing') || r.includes('trail')) return 'exit-badge-ts';
    return 'exit-badge-default';
}

function formatExitReason(reason) {
    return String(reason || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function getCorrClass(val) {
    const abs = Math.abs(val);
    if (abs >= 0.7) return 'corr-high';
    if (abs >= 0.4) return 'corr-medium';
    return 'corr-low';
}

// ─── Strategy Selector ──────────────────────────────────────────

function setupStrategySelector() {
    dom.strategySelect.addEventListener('change', async () => {
        const name = dom.strategySelect.value;
        if (!name) return;

        dom.strategySelect.disabled = true;
        try {
            const res = await fetch(`${API_BASE}/strategy/switch`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ strategy_name: name }),
            });
            const data = await res.json();

            if (res.ok) {
                dom.strategyLabel.textContent = `Strategy: ${data.active_strategy}`;
                // Refresh dependent panels
                fetchStrategyPerformance();
                fetchModelHealth();
                fetchStrategy();
            } else {
                alert(`Strategy switch failed: ${data.detail || 'Unknown error'}`);
                // Revert the dropdown
                fetchStrategy();
            }
        } catch (e) {
            alert('Failed to switch strategy');
            fetchStrategy();
        } finally {
            dom.strategySelect.disabled = false;
        }
    });
}

// ─── Pause / Resume Button ──────────────────────────────────────

function setupPauseButton() {
    dom.pauseBtn.addEventListener('click', async () => {
        const endpoint = isTradingPaused ? '/trading/resume' : '/trading/pause';
        try {
            const res = await fetch(`${API_BASE}${endpoint}`, { method: 'POST' });
            const data = await res.json();
            isBotRunning = data.is_running ?? isBotRunning;
            isTradingPaused = data.trading_paused ?? !isTradingPaused;
            if (isTradingPaused) {
                setStatusIndicator(true, 'ENTRIES PAUSED', 'warning');
            } else {
                setStatusIndicator(isBotRunning);
            }
            dom.pauseBtn.textContent = isTradingPaused ? 'RESUME ENTRIES' : 'PAUSE ENTRIES';
            await fetchStatus();
            await fetchTrainingStatus();
        } catch (e) {
            dom.pauseBtn.textContent = 'ERROR';
            setTimeout(() => {
                dom.pauseBtn.textContent = isTradingPaused ? 'RESUME ENTRIES' : 'PAUSE ENTRIES';
            }, 2000);
        }
    });
}

// ─── Boot ────────────────────────────────────────────────────────
init();
