/**
 * AkibaCryptoBot — Modern Matrix Dashboard
 * Wires every widget to the real FastAPI backend.
 *
 * REST endpoints used:
 *   GET  /api/status          -> header status, uptime
 *   GET  /api/balance         -> balance card
 *   GET  /api/trades          -> active positions table + count card
 *   GET  /api/metrics         -> PnL card
 *   GET  /api/equity-curve    -> Chart.js line chart
 *   GET  /api/trades/history  -> recent activity feed
 *   GET  /api/strategy        -> strategy label
 *   POST /api/trading/pause   -> pause button
 *   POST /api/trading/resume  -> pause button
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

    // Cards
    pnlValue: document.getElementById('pnl-value'),
    pnlPercent: document.getElementById('pnl-percent'),
    activePositions: document.getElementById('active-positions'),
    balanceValue: document.getElementById('balance-value'),
    uptime: document.getElementById('uptime'),
    latency: document.getElementById('latency'),

    // Chart
    pnlChart: document.getElementById('pnlChart'),
    chartEmpty: document.getElementById('chart-empty'),
    strategyLabel: document.getElementById('strategy-label'),

    // Activity Feed
    activityFeed: document.getElementById('activity-feed'),

    // Positions Table
    positionsTbody: document.getElementById('positions-tbody'),

    // Circuit Breaker
    cbBanner: document.getElementById('circuit-breaker-banner'),
    cbReason: document.getElementById('circuit-breaker-reason'),
};

// ─── State ───────────────────────────────────────────────────────

let isBotRunning = false;
let chartInstance = null;
let latestPrices = {};  // from /ws/prices
let wsLastPing = Date.now();

// ─── Initialization ──────────────────────────────────────────────

async function init() {
    setupPauseButton();
    setupWebSockets();

    // Initial REST data fetch (parallel)
    await Promise.allSettled([
        fetchStatus(),
        fetchBalance(),
        fetchTrades(),
        fetchMetrics(),
        fetchEquityCurve(),
        fetchTradeHistory(),
        fetchStrategy(),
    ]);

    // Periodic polling for data that isn't covered by WebSockets
    setInterval(fetchBalance, 15000);
    setInterval(fetchTrades, 10000);
    setInterval(fetchMetrics, 30000);
    setInterval(fetchTradeHistory, 20000);
}

// ─── REST Fetchers ───────────────────────────────────────────────

async function fetchStatus() {
    try {
        const res = await fetch(`${API_BASE}/status`);
        const data = await res.json();
        updateStatusUI(data);
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
            // Sum all values, or just show USDT
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
        const data = await res.json();
        const count = data.count || 0;
        const trades = data.trades || [];

        // Update card
        dom.activePositions.innerHTML = `${count} <span class="text-sm text-gray-500 font-sans font-normal">Pair${count !== 1 ? 's' : ''}</span>`;

        // Update table
        if (trades.length === 0) {
            dom.positionsTbody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-matrix-dim">No open positions</td></tr>`;
            return;
        }

        dom.positionsTbody.innerHTML = trades.map(t => {
            const entryPrice = parseFloat(t.entry_price || 0);
            const currentPrice = latestPrices[t.symbol] || entryPrice;
            const pnlPct = entryPrice > 0 ? ((currentPrice - entryPrice) / entryPrice * 100) : 0;
            const isLong = (t.side || 'long').toLowerCase() === 'long' || (t.side || 'buy').toLowerCase() === 'buy';
            const effectivePnl = isLong ? pnlPct : -pnlPct;

            return `
                <tr class="border-b border-matrix-border/50 hover:bg-matrix-border/30 transition-colors">
                    <td class="py-3 text-white font-bold">${t.symbol || '—'}</td>
                    <td class="py-3 ${isLong ? 'text-matrix-neon' : 'text-red-500'}">${isLong ? 'LONG' : 'SHORT'}</td>
                    <td class="py-3 text-gray-400">$${entryPrice.toLocaleString()}</td>
                    <td class="py-3 text-white">$${currentPrice.toLocaleString()}</td>
                    <td class="py-3 ${effectivePnl >= 0 ? 'text-matrix-neon' : 'text-red-500'}">${effectivePnl >= 0 ? '+' : ''}${effectivePnl.toFixed(2)}%</td>
                </tr>`;
        }).join('');
    } catch (e) {
        dom.positionsTbody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-matrix-dim">Failed to load positions</td></tr>`;
    }
}

async function fetchMetrics() {
    try {
        const res = await fetch(`${API_BASE}/metrics`);
        const data = await res.json();

        // Try to find total PnL in the response
        const pnl = data.total_pnl ?? data.net_pnl ?? data.total_profit ?? null;
        const winRate = data.win_rate ?? null;

        if (pnl !== null) {
            dom.pnlValue.textContent = `${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
            dom.pnlValue.className = `text-3xl font-bold font-mono neon-glow ${pnl >= 0 ? 'text-matrix-neon' : 'text-red-500'}`;
        }

        if (winRate !== null) {
            dom.pnlPercent.textContent = `WR: ${(winRate * 100).toFixed(1)}%`;
        }
    } catch (e) {
        // Metrics may not be available yet — that's fine
    }
}

async function fetchEquityCurve() {
    try {
        const res = await fetch(`${API_BASE}/equity-curve`);
        const data = await res.json();
        const points = data.data || [];

        if (points.length === 0) {
            dom.pnlChart.classList.add('hidden');
            dom.chartEmpty.classList.remove('hidden');
            return;
        }

        dom.pnlChart.classList.remove('hidden');
        dom.chartEmpty.classList.add('hidden');

        const labels = points.map(p => {
            if (p.timestamp) {
                const d = new Date(p.timestamp);
                return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
            }
            return String(p.index ?? '');
        });
        const values = points.map(p => p.balance);

        renderChart(labels, values);
    } catch (e) {
        dom.pnlChart.classList.add('hidden');
        dom.chartEmpty.classList.remove('hidden');
    }
}

async function fetchTradeHistory() {
    try {
        const res = await fetch(`${API_BASE}/trades/history?limit=15`);
        const data = await res.json();
        const trades = data.trades || [];

        if (trades.length === 0) {
            dom.activityFeed.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">No completed trades yet</div>`;
            return;
        }

        dom.activityFeed.innerHTML = trades.map(t => {
            const isBuy = (t.side || '').toLowerCase() === 'buy';
            const pnl = t.pnl || 0;
            const typeClass = isBuy
                ? 'text-matrix-neon bg-matrix-neon/10 border-matrix-neon/20'
                : 'text-red-500 bg-red-500/10 border-red-500/20';

            const time = t.exit_time ? new Date(t.exit_time).toLocaleTimeString() : '';

            return `
                <div class="flex items-start space-x-3 p-2 rounded hover:bg-matrix-border/30 transition-colors border border-transparent hover:border-matrix-border/50">
                    <div class="${typeClass} px-2 py-0.5 rounded text-xs font-bold border">${isBuy ? 'BUY' : 'SELL'}</div>
                    <div class="flex flex-col min-w-0">
                        <span class="text-white text-xs truncate">${t.symbol} @ $${parseFloat(t.exit_price || 0).toLocaleString()} → <span class="${pnl >= 0 ? 'text-matrix-neon' : 'text-red-500'}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</span></span>
                        <span class="text-matrix-dim text-[10px]">${t.strategy || ''} · ${time}</span>
                    </div>
                </div>`;
        }).join('');
    } catch (e) {
        dom.activityFeed.innerHTML = `<div class="text-matrix-dim text-xs text-center py-4">Failed to load history</div>`;
    }
}

async function fetchStrategy() {
    try {
        const res = await fetch(`${API_BASE}/strategy`);
        const data = await res.json();
        if (data.active_strategy) {
            dom.strategyLabel.textContent = `Strategy: ${data.active_strategy}`;
        }
    } catch (e) { /* silent */ }
}

// ─── Chart.js ────────────────────────────────────────────────────

function renderChart(labels, values) {
    const ctx = dom.pnlChart.getContext('2d');

    const gradient = ctx.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, 'rgba(0, 255, 65, 0.15)');
    gradient.addColorStop(1, 'rgba(0, 255, 65, 0)');

    if (chartInstance) {
        chartInstance.data.labels = labels;
        chartInstance.data.datasets[0].data = values;
        chartInstance.update();
        return;
    }

    chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Portfolio Value',
                data: values,
                borderColor: '#00FF41',
                backgroundColor: gradient,
                borderWidth: 2,
                pointBackgroundColor: '#000000',
                pointBorderColor: '#00FF41',
                pointBorderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 5,
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#06120E',
                    titleColor: '#008F11',
                    bodyColor: '#00FF41',
                    borderColor: '#0A291A',
                    borderWidth: 1,
                    displayColors: false
                }
            },
            scales: {
                x: {
                    grid: { color: '#0A291A', drawBorder: false },
                    ticks: { color: '#008F11', font: { family: 'Space Grotesk', size: 11 }, maxTicksLimit: 8 }
                },
                y: {
                    grid: { color: '#0A291A', drawBorder: false },
                    ticks: { color: '#008F11', font: { family: 'VT323', size: 13 } }
                }
            }
        }
    });
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

    // Running state
    const isRunning = data.is_running !== undefined ? data.is_running : true;
    setStatusIndicator(isRunning);
    isBotRunning = isRunning;

    // Uptime
    if (data.uptime) {
        dom.uptime.textContent = data.uptime.split('.')[0]; // Strip ms
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
        setStatusIndicator(false, 'CIRCUIT BREAKER');
    } else {
        dom.cbBanner.classList.add('hidden');
    }

    // Update pause button text
    dom.pauseBtn.textContent = isBotRunning ? 'PAUSE BOT' : 'RESUME BOT';
}

function setStatusIndicator(isOnline, textOverride = null) {
    if (textOverride) {
        dom.statusText.textContent = textOverride;
        dom.statusText.className = 'text-red-500 font-mono tracking-wider';
        dom.statusDot.className = 'w-2 h-2 rounded-full bg-red-500 animate-pulse shadow-[0_0_8px_#EF4444]';
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

// ─── Pause / Resume Button ───────────────────────────────────────

function setupPauseButton() {
    dom.pauseBtn.addEventListener('click', async () => {
        const endpoint = isBotRunning ? '/trading/pause' : '/trading/resume';
        try {
            const res = await fetch(`${API_BASE}${endpoint}`, { method: 'POST' });
            const data = await res.json();
            isBotRunning = data.is_running ?? !isBotRunning;
            setStatusIndicator(isBotRunning);
            dom.pauseBtn.textContent = isBotRunning ? 'PAUSE BOT' : 'RESUME BOT';
        } catch (e) {
            // Temporary visual error feedback
            dom.pauseBtn.textContent = 'ERROR';
            setTimeout(() => {
                dom.pauseBtn.textContent = isBotRunning ? 'PAUSE BOT' : 'RESUME BOT';
            }, 2000);
        }
    });
}

// ─── Boot ────────────────────────────────────────────────────────
init();
