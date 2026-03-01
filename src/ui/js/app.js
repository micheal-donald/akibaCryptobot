/**
 * AkibaCryptoBot Dashboard Logic (Matrix Terminal Version)
 * Connects to FastAPI WebSockets and REST endpoints.
 */

const API_BASE = window.location.origin + '/api';
const WS_BASE = (window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host + '/ws';

// State
let isBotRunning = false;
let logIndex = 0;

// Elements
const el = {
    uptime: document.getElementById('uptime'),
    feed: document.getElementById('terminal-feed'),
    input: document.querySelector('input'),
    activePairs: document.querySelectorAll('.text-xl')[1], // Second large text block
    pnl: document.querySelectorAll('.text-xl')[2], // Third large text block
    latency: document.querySelectorAll('.text-xl')[3], // Fourth large text block
    statusText: document.querySelector('.text-matrix-neon'), // "STATUS: ONLINE"
    statusDot: document.querySelector('.w-3.h-3.bg-matrix-neon') // Pulse dot
};

// --- INITIALIZATION ---

async function init() {
    addLog("INITIALIZING CORE SYSTEMS...");
    addLog("CONNECTING TO EXCHANGE API...");
    await fetchBalance();
    setupWebSockets();
    setupInputHandler();
}

// --- REST API CALLS ---

async function fetchBalance() {
    try {
        const res = await fetch(`${API_BASE}/balance`);
        const data = await res.json();

        let total = 0;
        if (data.USDT) total += data.USDT;

        // Mock PnL logic for UI layout since true PnL isn't tracked yet
        const mockPnl = total * 0.005;

        el.pnl.textContent = `${mockPnl >= 0 ? '+' : ''} $${mockPnl.toFixed(2)}`;
        el.pnl.className = `text-xl ${mockPnl >= 0 ? 'text-green-400' : 'text-red-500'}`;
    } catch (e) {
        addLog("ERR: FAILED TO FETCH BALANCE.");
    }
}

async function handleCommand(cmd) {
    const args = cmd.split(' ');
    const action = args[0].toLowerCase();

    if (action === 'pause' || action === 'halt') {
        try {
            await fetch(`${API_BASE}/trading/pause`, { method: 'POST' });
            addLog("ENGINE HALTED. ALGORITHMS PAUSED.");
        } catch (e) {
            addLog("ERR: FAILED TO HALT ENGINE.");
        }
    } else if (action === 'resume' || action === 'start') {
        try {
            await fetch(`${API_BASE}/trading/resume`, { method: 'POST' });
            addLog("ENGINE RESUMED. ALGORITHMS ACTIVE.");
        } catch (e) {
            addLog("ERR: FAILED TO RESUME ENGINE.");
        }
    } else if (action === 'status') {
        addLog(`System is currently ${isBotRunning ? 'ONLINE' : 'PAUSED'}.`);
    } else if (action === 'clear' || action === 'cls') {
        el.feed.innerHTML = '';
    } else {
        addLog(`ERR: COMMAND NOT RECOGNIZED: ${cmd}`);
    }
}

// --- WEBSOCKETS ---

function setupWebSockets() {
    // 1. Status Stream (updates every 5s)
    const wsStatus = new WebSocket(`${WS_BASE}/status`);
    let lastPing = Date.now();

    wsStatus.onmessage = (event) => {
        // Calculate latency
        const currentPing = Date.now();
        const latency = currentPing - lastPing;
        el.latency.textContent = `${latency}ms`;
        lastPing = currentPing;

        const { data } = JSON.parse(event.data);
        updateStatusUI(data);
        fetchBalance();
    };

    wsStatus.onclose = () => {
        updateEngineStatusIndicator(false, "DISCONNECTED");
        addLog("WS CONNECTION LOST. RECONNECTING IN 5S...");
        setTimeout(() => setupWebSockets(), 5000);
    };

    // 2. Prices Stream (updates every 1s)
    const wsPrices = new WebSocket(`${WS_BASE}/prices`);

    wsPrices.onmessage = (event) => {
        const { data } = JSON.parse(event.data);
        if (Object.keys(data).length > 0) {
            const pairs = Object.keys(data).map(p => p.replace('/USDT', ''));
            el.activePairs.textContent = pairs.slice(0, 3).join(', ') + (pairs.length > 3 ? '...' : '');
        }
    };
}

// --- UI UPDATERS ---

function updateStatusUI(data) {
    updateEngineStatusIndicator(data.is_running);
    el.uptime.textContent = data.uptime.split('.')[0]; // Strip ms

    if (data.circuit_breaker_active) {
        addLog(`CRITICAL: CIRCUIT BREAKER ACTIVE - ${data.circuit_breaker_reason}`);
        el.statusText.className = 'text-red-500';
        el.statusDot.className = 'w-3 h-3 bg-red-500 rounded-full animate-pulse shadow-[0_0_8px_#EF4444]';
    }

    if (data.active_trades > 0) {
        // If a new trade opened, log it (simple check using a global var or just let the user see it on the dashboard)
        // A robust system would track previous state and log diffs.
    }
}

function updateEngineStatusIndicator(isRunning, textOverride = null) {
    isBotRunning = isRunning;

    if (textOverride) {
        el.statusText.textContent = `STATUS: ${textOverride}`;
        el.statusText.className = 'text-red-500';
        el.statusDot.className = 'w-3 h-3 bg-red-500 rounded-full animate-pulse shadow-[0_0_8px_#EF4444]';
        return;
    }

    if (isRunning) {
        el.statusText.textContent = 'STATUS: ONLINE';
        el.statusText.className = 'text-matrix-neon';
        el.statusDot.className = 'w-3 h-3 bg-matrix-neon rounded-full animate-pulse shadow-[0_0_8px_#00FF41]';
    } else {
        el.statusText.textContent = 'STATUS: PAUSED';
        el.statusText.className = 'text-matrix-dim';
        el.statusDot.className = 'w-3 h-3 bg-matrix-dim rounded-full shadow-none';
    }
}

function addLog(msg, isInput = false) {
    const row = document.createElement('div');
    const now = new Date();
    const timeStr = `[${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}]`;

    if (isInput) {
        row.innerHTML = `<span class="text-matrix-dim mr-2">${timeStr}</span> <span class="text-matrix-neon">> ${msg}</span>`;
    } else {
        row.innerHTML = `<span class="text-matrix-dim mr-2">${timeStr}</span> <span class="text-matrix-neon">${msg}</span>`;
    }

    el.feed.appendChild(row);

    // Keep only last 100 logs
    if (el.feed.children.length > 100) {
        el.feed.removeChild(el.feed.firstChild);
    }

    el.feed.scrollTop = el.feed.scrollHeight;
}

// --- INPUT HANDLER ---
function setupInputHandler() {
    el.input.addEventListener('keypress', function (e) {
        if (e.key === 'Enter' && this.value.trim() !== '') {
            const cmd = this.value.trim();
            addLog(cmd, true);

            // Process command after slight delay for visual effect
            setTimeout(() => {
                handleCommand(cmd);
            }, 300);

            this.value = '';
        }
    });

    // Auto-focus input when clicking terminal
    document.querySelector('main').addEventListener('click', () => {
        el.input.focus();
    });
}

// Boot
init();
