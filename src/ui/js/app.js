/**
 * AkibaCryptoBot Dashboard Logic
 * Connects to FastAPI WebSockets and REST endpoints.
 */

const API_BASE = window.location.origin + '/api';
const WS_BASE = (window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host + '/ws';

// State
let isBotRunning = false;
let pricesCache = {};

// Elements
const el = {
    ping: document.getElementById('status-ping'),
    dot: document.getElementById('status-dot'),
    text: document.getElementById('engine-status-text'),
    uptime: document.getElementById('uptime-text'),
    badge: document.getElementById('nav-mode-badge'),
    btnPause: document.getElementById('btn-pause'),
    btnResume: document.getElementById('btn-resume'),
    balance: document.getElementById('total-balance'),
    pnl: document.getElementById('daily-pnl'),
    strategy: document.getElementById('current-strategy'),
    strategyComponents: document.getElementById('strategy-components'),
    tickerList: document.getElementById('market-ticker-list'),
    circuitBreaker: document.getElementById('circuit-breaker-alert'),
    circuitReason: document.getElementById('circuit-breaker-reason'),
    activeTradesBody: document.getElementById('active-trades-body'),
    activeTradesCount: document.getElementById('active-trades-count'),
    modal: document.getElementById('strategy-modal'),
    modalContent: document.getElementById('strategy-modal-content'),
    strategyOptions: document.getElementById('strategy-options-list'),
    btnSubmitStrategy: document.getElementById('btn-submit-strategy')
};

// --- INITIALIZATION ---

async function init() {
    await fetchConfig();
    await fetchMetrics();
    await fetchTrades();
    setupWebSockets();
}

// --- REST API CALLS ---

async function fetchConfig() {
    try {
        const res = await fetch(`${API_BASE}/config`);
        const data = await res.json();

        el.badge.textContent = `${data.trading_mode.toUpperCase()} MODE`;
        if (data.trading_mode === 'live') {
            el.badge.classList.add('text-rose-400');
            el.badge.classList.remove('text-slate-400');
        }

        document.getElementById('config-max-pos').textContent = `${(data.max_position_size * 100).toFixed(1)}%`;
        document.getElementById('config-sl').textContent = `${(data.stop_loss_percent * 100).toFixed(1)}%`;
    } catch (e) {
        console.error("Config fetch failed", e);
    }
}

async function fetchMetrics() {
    try {
        const res = await fetch(`${API_BASE}/metrics`);
        const data = await res.json();

        if (data.win_rate !== undefined) {
            document.getElementById('metric-winrate').textContent = `${(data.win_rate * 100).toFixed(1)}%`;
            document.getElementById('metric-pf').textContent = (data.profit_factor || 0).toFixed(2);
            document.getElementById('metric-sharpe').textContent = (data.sharpe_ratio_daily || 0).toFixed(2);
            document.getElementById('metric-trades').textContent = data.total_signals || 0;
            renderPlaceholderBars(data.total_pnl || 0);
        }
    } catch (e) {
        console.error("Metrics fetch failed", e);
    }
}

async function fetchTrades() {
    try {
        const res = await fetch(`${API_BASE}/trades`);
        const data = await res.json();
        renderTrades(data.trades);
    } catch (e) {
        console.error("Trades fetch failed", e);
    }
}

async function fetchBalance() {
    try {
        const res = await fetch(`${API_BASE}/balance`);
        const data = await res.json();

        // Sum roughly if multiple assets, or just show USDT
        let total = 0;
        if (data.USDT) total += data.USDT;
        // Mock PnL logic for UI layout
        const mockPnl = total * 0.005; // Just for visual placeholder

        el.balance.textContent = `$${total.toFixed(2)}`;
        el.pnl.textContent = `+$${mockPnl.toFixed(2)} Today`;
    } catch (e) {
        console.error("Balance fetch failed", e);
    }
}

// --- ENGINE CONTROLS ---

async function toggleEngine(action) {
    try {
        const res = await fetch(`${API_BASE}/trading/${action}`, { method: 'POST' });
        const data = await res.json();
        showToast(`Engine ${action}d successfully.`, 'success');
        updateEngineStatusIndicator(data.is_running);
    } catch (e) {
        showToast(`Failed to ${action} engine.`, 'error');
    }
}

// --- STRATEGY MODAL ---

let selectedStrategy = null;

async function openStrategyModal() {
    el.modal.classList.remove('hidden');
    // slight delay for transition
    setTimeout(() => {
        el.modal.classList.remove('opacity-0');
        el.modalContent.classList.remove('scale-95');
    }, 10);

    el.btnSubmitStrategy.disabled = true;

    try {
        const res = await fetch(`${API_BASE}/strategy`);
        const data = await res.json();

        el.strategyOptions.innerHTML = '';
        data.available_strategies.forEach(s => {
            const isActive = s === data.active_strategy;
            const div = document.createElement('div');
            div.className = `p-3 border rounded-lg cursor-pointer transition-colors flex items-center justify-between
                ${isActive ? 'border-brand bg-brand/10' : 'border-dark-border bg-dark-card hover:border-slate-500'}`;

            div.onclick = () => selectStrategyOption(div, s, isActive);

            div.innerHTML = `
                <div>
                    <h4 class="text-white font-bold capitalize">${s}</h4>
                    <p class="text-xs text-slate-400 mt-1">${isActive ? 'Currently Active' : 'Click to select'}</p>
                </div>
                ${isActive ? '<i class="fa-solid fa-circle-check text-brand text-xl"></i>' : ''}
            `;
            el.strategyOptions.appendChild(div);
        });
    } catch (e) {
        el.strategyOptions.innerHTML = '<p class="text-red-400 text-sm">Failed to load strategies</p>';
    }
}

function selectStrategyOption(element, strategyName, isActive) {
    if (isActive) return;

    // Reset borders
    Array.from(el.strategyOptions.children).forEach(child => {
        if (!child.classList.contains('border-brand')) {
            child.classList.replace('border-slate-400', 'border-dark-border');
        }
    });

    // Highlight selected
    element.classList.replace('border-dark-border', 'border-slate-400');
    selectedStrategy = strategyName;
    el.btnSubmitStrategy.disabled = false;
}

function closeStrategyModal() {
    el.modal.classList.add('opacity-0');
    el.modalContent.classList.add('scale-95');
    setTimeout(() => {
        el.modal.classList.add('hidden');
        selectedStrategy = null;
    }, 300);
}

async function submitStrategySwitch() {
    if (!selectedStrategy) return;

    const btn = el.btnSubmitStrategy;
    btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Switching...';
    btn.disabled = true;

    try {
        const res = await fetch(`${API_BASE}/strategy/switch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ strategy_name: selectedStrategy })
        });

        if (res.ok) {
            showToast(`Switched to ${selectedStrategy}`, 'success');
            closeStrategyModal();
            // Status websocket will update UI naturally within 5s
        } else {
            const err = await res.json();
            throw new Error(err.detail);
        }
    } catch (e) {
        showToast(e.message || 'Failed to switch strategy', 'error');
        btn.innerHTML = 'Confirm Switch';
        btn.disabled = false;
    }
}

// --- WEBSOCKETS ---

function setupWebSockets() {
    // 1. Status Stream (updates every 5s)
    const wsStatus = new WebSocket(`${WS_BASE}/status`);

    wsStatus.onmessage = (event) => {
        const { data } = JSON.parse(event.data);
        updateStatusUI(data);
        fetchBalance(); // Refresh balance alongside status
    };

    wsStatus.onclose = () => {
        updateEngineStatusIndicator(false, "DISCONNECTED");
        setTimeout(() => setupWebSockets(), 5000); // Reconnect
    };

    // 2. Prices Stream (updates every 1s)
    const wsPrices = new WebSocket(`${WS_BASE}/prices`);

    wsPrices.onmessage = (event) => {
        const { data } = JSON.parse(event.data);
        updatePricesUI(data);
    };
}

// --- UI UPDATERS ---

function updateStatusUI(data) {
    updateEngineStatusIndicator(data.is_running);
    el.uptime.textContent = `Uptime: ${data.uptime.split('.')[0]}`; // Strip milliseconds

    el.strategy.textContent = data.strategy;

    // Circuit Breaker
    if (data.circuit_breaker_active) {
        el.circuitBreaker.classList.remove('hidden');
        el.circuitReason.textContent = data.circuit_breaker_reason || 'Unknown volatility event';
    } else {
        el.circuitBreaker.classList.add('hidden');
    }

    // Refresh trades count
    el.activeTradesCount.textContent = data.active_trades;
    if (data.active_trades > 0 && el.activeTradesBody.children.length <= 1) {
        fetchTrades(); // refresh table if count mismatch
    } else if (data.active_trades === 0) {
        renderTrades({});
    }
}

function updateEngineStatusIndicator(isRunning, textOverride = null) {
    isBotRunning = isRunning;

    if (textOverride) {
        el.text.textContent = textOverride;
        el.text.className = 'text-sm font-semibold text-red-500 uppercase tracking-wider';
        el.dot.className = 'relative inline-flex rounded-full h-3 w-3 bg-red-500';
        el.ping.classList.add('hidden');
        el.btnPause.disabled = true;
        el.btnResume.disabled = true;
        return;
    }

    if (isRunning) {
        el.text.textContent = 'ONLINE';
        el.text.className = 'text-sm font-semibold text-emerald-400 uppercase tracking-wider';
        el.dot.className = 'relative inline-flex rounded-full h-3 w-3 bg-emerald-500';
        el.ping.classList.remove('hidden');
        el.ping.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75';

        el.btnPause.disabled = false;
        el.btnResume.disabled = true;
    } else {
        el.text.textContent = 'PAUSED';
        el.text.className = 'text-sm font-semibold text-amber-500 uppercase tracking-wider';
        el.dot.className = 'relative inline-flex rounded-full h-3 w-3 bg-amber-500';
        el.ping.classList.add('hidden');

        el.btnPause.disabled = true;
        el.btnResume.disabled = false;
    }
}

function updatePricesUI(prices) {
    if (Object.keys(prices).length === 0) return;

    // Initial render
    if (el.tickerList.children.length <= 1 && el.tickerList.innerHTML.includes('Waiting')) {
        el.tickerList.innerHTML = '';
    }

    Object.entries(prices).forEach(([symbol, price]) => {
        let tickerEl = document.getElementById(`ticker-${symbol}`);
        const oldPrice = pricesCache[symbol] || price;
        const colorClass = price > oldPrice ? 'text-emerald-400' : (price < oldPrice ? 'text-red-400' : 'text-slate-300');
        const bgAnim = price > oldPrice ? 'price-up' : (price < oldPrice ? 'price-down' : '');

        if (!tickerEl) {
            tickerEl = document.createElement('div');
            tickerEl.id = `ticker-${symbol}`;
            tickerEl.className = 'flex justify-between items-center px-4 py-2 hover:bg-slate-800/50 rounded transition-colors';
            tickerEl.innerHTML = `
                <span class="font-bold text-white">${symbol.replace('/USDT', '')} <span class="text-slate-500 text-xs font-normal">/USDT</span></span>
                <span class="font-mono ${colorClass} transition-colors" id="price-${symbol}">${price.toFixed(4)}</span>
            `;
            el.tickerList.appendChild(tickerEl);
        } else {
            const priceSpan = document.getElementById(`price-${symbol}`);
            priceSpan.textContent = price.toFixed(4);
            priceSpan.className = `font-mono ${colorClass} transition-colors ${bgAnim}`;
            setTimeout(() => {
                priceSpan.classList.remove('price-up', 'price-down');
            }, 1000);
        }

        pricesCache[symbol] = price;
    });
}

function renderTrades(tradesDict) {
    el.activeTradesBody.innerHTML = '';
    const trades = Object.entries(tradesDict);

    if (trades.length === 0) {
        el.activeTradesBody.innerHTML = `<tr><td colspan="6" class="px-6 py-8 text-center text-slate-500 italic">No active positions</td></tr>`;
        return;
    }

    trades.forEach(([symbol, t]) => {
        const pnl = ((t.current_price - t.entry_price) / t.entry_price) * (t.side === 'buy' ? 1 : -1) * 100;
        const pnlColor = pnl >= 0 ? 'text-emerald-400' : 'text-red-400';
        const sideColor = t.side === 'buy' ? 'text-emerald-400 bg-emerald-500/10' : 'text-rose-400 bg-rose-500/10';

        const tr = document.createElement('tr');
        tr.className = 'hover:bg-slate-800/30 transition-colors';
        tr.innerHTML = `
            <td class="px-6 py-4 font-bold text-white">${symbol}</td>
            <td class="px-6 py-4"><span class="px-2 py-1 rounded text-xs font-bold uppercase tracking-wider ${sideColor}">${t.side}</span></td>
            <td class="px-6 py-4 text-right font-mono text-slate-300">${t.entry_price.toFixed(4)}</td>
            <td class="px-6 py-4 text-right font-mono text-slate-300">${t.amount.toFixed(4)}</td>
            <td class="px-6 py-4 text-right font-mono font-bold ${pnlColor}">${pnl > 0 ? '+' : ''}${pnl.toFixed(2)}%</td>
            <td class="px-6 py-4 text-right text-slate-500 text-xs">--</td>
        `;
        el.activeTradesBody.appendChild(tr);
    });
}

function renderPlaceholderBars(pnl) {
    // Generate CSS bars for aesthetic
    const container = document.getElementById('equity-bars');
    container.innerHTML = '';
    let val = 40;
    for (let i = 0; i < 30; i++) {
        val = val + (Math.random() * 20 - 9);
        if (val < 10) val = 10;
        if (val > 100) val = 100;

        const color = val > 50 ? 'bg-emerald-500/80 hover:bg-emerald-400' : 'bg-rose-500/80 hover:bg-rose-400';

        container.innerHTML += `<div class="w-full rounded-t-sm transition-all duration-300 ${color}" style="height: ${val}%;"></div>`;
    }
}

// --- UTILS ---

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');

    const colors = {
        success: 'bg-emerald-500 border-emerald-400 text-white',
        error: 'bg-rose-500 border-rose-400 text-white',
        info: 'bg-slate-800 border-slate-600 text-slate-200'
    };

    const icons = {
        success: 'fa-check-circle',
        error: 'fa-triangle-exclamation',
        info: 'fa-circle-info'
    };

    toast.className = `flex items-center px-4 py-3 rounded shadow-lg border ${colors[type]} toast-enter`;
    toast.innerHTML = `<i class="fa-solid ${icons[type]} mr-2"></i> <span class="text-sm font-medium">${message}</span>`;

    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => {
        toast.classList.replace('toast-enter', 'toast-enter-active');
    });

    // Remove after 3s
    setTimeout(() => {
        toast.classList.replace('toast-enter-active', 'toast-leave');
        requestAnimationFrame(() => toast.classList.replace('toast-leave', 'toast-leave-active'));
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Boot
init();
