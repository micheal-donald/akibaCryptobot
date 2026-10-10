'use strict';
const byId = id => document.getElementById(id);
const money = value => value == null || !Number.isFinite(Number(value)) ? '—' : `${Number(value).toFixed(2)} USDT`;
function rows(id, data) {
  const target = byId(id);
  target.replaceChildren(...data.map(values => {
    const row = document.createElement('tr');
    for (const value of values) {
      const cell = document.createElement('td');
      cell.textContent = value ?? '—';
      row.appendChild(cell);
    }
    return row;
  }));
}
function unavailable(message) {
  byId('status').className = 'error';
  byId('status').textContent = message;
  for (const id of ['equity', 'realized', 'unrealized', 'completed']) byId(id).textContent = '—';
  rows('positions', []); rows('history', []);
  byId('empty-positions').textContent = 'Current positions unavailable.';
  byId('empty-history').textContent = 'Trade history unavailable.';
  byId('rules').textContent = ''; byId('locks').textContent = '';
}
async function refresh() {
  try {
    const response = await fetch('/api/portfolio', {signal: AbortSignal.timeout(45000), cache: 'no-store'});
    const p = await response.json();
    if (!response.ok || !p.healthy || p.mode !== 'dry_run') throw new Error('unavailable');
    byId('status').className = '';
    byId('status').textContent = `Running · Dry-run · Updated ${new Date(p.updated_at * 1000).toLocaleTimeString()}`;
    byId('equity').textContent = p.total_pnl == null ? '—' : money(p.initial_balance + p.total_pnl);
    byId('realized').textContent = money(p.realized_pnl);
    byId('unrealized').textContent = p.total_pnl == null || p.realized_pnl == null ? '—' : money(p.total_pnl - p.realized_pnl);
    byId('completed').textContent = p.closed_trades;
    byId('rules').textContent = `${p.strategy} · ${p.timeframe} · ${p.pairs.join(' / ')} · ${money(p.stake_amount)} per position · Maximum ${p.max_open_trades} open · ${Math.abs(p.stoploss * 100).toFixed(0)}% simulated stop`;
    byId('locks').textContent = p.entry_locks.length ? `Entry cooldowns: ${p.entry_locks.map(l => `${l.pair}: ${l.reason}`).join('; ')}` : 'Entries require a strategy signal. No forced trades.';
    rows('positions', p.positions.map(t => [t.pair, money(t.stake_amount), money(t.open_rate), money(t.current_rate), money(t.profit_abs)]));
    rows('history', p.history.map(t => [t.close_date, t.pair, t.exit_reason, money(t.close_profit_abs)]));
    byId('empty-positions').textContent = p.positions.length ? '' : 'No open positions. Waiting for a qualifying signal.';
    byId('empty-history').textContent = p.history.length ? '' : 'No completed trades in this new experiment yet.';
  } catch (_) { unavailable('Engine data unavailable or stale. Results are temporarily hidden.'); }
  setTimeout(refresh, 15000);
}
refresh();
