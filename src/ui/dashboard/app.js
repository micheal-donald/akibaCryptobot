document.addEventListener("DOMContentLoaded", () => {

    // Live Clock Uptime Simulation
    const uptimeEl = document.getElementById('uptime');
    let seconds = 0;
    setInterval(() => {
        seconds++;
        const h = String(Math.floor(seconds / 3600)).padStart(2, '0');
        const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
        const s = String(seconds % 60).padStart(2, '0');
        uptimeEl.textContent = `${h}:${m}:${s}`;
    }, 1000);

    // Terminal Feed Simulation
    const feed = document.getElementById('terminal-feed');
    const logs = [
        "INITIALIZING CORE SYSTEMS...",
        "LOADING CORRELATION RISK MATRIX...",
        "CONNECTING TO EXCHANGE API...",
        "STATUS: OK. LATENCY: 42ms.",
        "MONITORING PAIRS: BTC/USDT, ETH/USDT",
        "---",
        "EVALUATING SPREAD OPPORTUNITIES...",
        "NO ARBITRAGE DETECTED.",
        "RISK MATRIX RECALCULATED.",
        "WAITING FOR SIGNAL...",
    ];

    let logIndex = 0;

    function addLog() {
        if (logIndex < logs.length) {
            const row = document.createElement('div');

            // Format time stamp
            const now = new Date();
            const timeStr = `[${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}]`;

            row.innerHTML = `<span class="text-matrix-dim mr-2">${timeStr}</span> <span class="text-matrix-neon">${logs[logIndex]}</span>`;
            feed.appendChild(row);

            // Auto scroll to bottom
            feed.scrollTop = feed.scrollHeight;

            logIndex++;
            setTimeout(addLog, Math.random() * 1500 + 500); // Random delay between 500ms and 2000ms
        } else {
            // Loop logs for simulation purposes
            logIndex = 5;
            setTimeout(addLog, 3000);
        }
    }

    // Start initial log feed
    setTimeout(addLog, 1000);

    // Basic Input Command Handling
    const inputField = document.querySelector('input');
    inputField.addEventListener('keypress', function (e) {
        if (e.key === 'Enter' && this.value.trim() !== '') {
            const row = document.createElement('div');
            const now = new Date();
            const timeStr = `[${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}]`;

            row.innerHTML = `<span class="text-matrix-dim mr-2">${timeStr}</span> <span class="text-matrix-neon">> ${this.value}</span>`;
            feed.appendChild(row);

            // Echo response
            setTimeout(() => {
                const response = document.createElement('div');
                response.innerHTML = `<span class="text-matrix-dim mr-2">${timeStr}</span> <span class="text-matrix-neon">ERR: COMMAND NOT RECOGNIZED.</span>`;
                feed.appendChild(response);
                feed.scrollTop = feed.scrollHeight;
            }, 500);

            this.value = '';
            feed.scrollTop = feed.scrollHeight;
        }
    });

});
