/* L7 Proxy Test Suite — Dashboard JS */

const API = '';   // same origin — server.py serves this file
let es = null, rpsChart = null, statusChart = null;
let running = false, floodStart = null, floodDur = 30;
let lastTotal = 0, lastTick = null;
let runHistory = JSON.parse(localStorage.getItem('l7h') || '[]');

/* ── Init ─────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    renderHistory();
    checkProxy();
    setInterval(checkProxy, 8000);
});

/* ── Proxy status ─────────────────────────────────────────────── */
async function checkProxy() {
    const dot = document.getElementById('proxy-dot');
    const txt = document.getElementById('proxy-text');
    dot.className = 'status-dot checking';
    txt.textContent = 'Proxy: checking…';
    try {
        const r = await fetch('/api/proxy-status', { signal: AbortSignal.timeout(4000) });
        const d = await r.json();
        if (d.ok) {
            dot.className = 'status-dot online';
            txt.textContent = `Proxy: online (${d.ms}ms)`;
            document.getElementById('proxy-addr').textContent = `${d.host}:${d.port}`;
        } else {
            dot.className = 'status-dot offline';
            txt.textContent = 'Proxy: unreachable';
        }
    } catch {
        dot.className = 'status-dot offline';
        txt.textContent = 'Proxy: offline';
    }
}

/* ── Charts ───────────────────────────────────────────────────── */
function initCharts() {
    rpsChart = new Chart(document.getElementById('rpsChart').getContext('2d'), {
        type: 'line',
        data: { labels: [], datasets: [{ label: 'RPS', data: [],
            borderColor: '#ff6b35', backgroundColor: 'rgba(255,107,53,.1)',
            borderWidth: 2, pointRadius: 0, fill: true, tension: 0.4 }] },
        options: { responsive: true, animation: false,
            scales: { x: { display: false },
                      y: { beginAtZero: true, grid: { color: 'rgba(0,0,0,.05)' } } },
            plugins: { legend: { display: false } } }
    });

    statusChart = new Chart(document.getElementById('statusChart').getContext('2d'), {
        type: 'doughnut',
        data: { labels: [], datasets: [{ data: [], backgroundColor: [] }] },
        options: { responsive: true,
            plugins: { legend: { position: 'right', labels: { font: { size: 11 } } } } }
    });
}

const SC = { '2xx':'#4ade80','3xx':'#facc15','4xx':'#f87171','5xx':'#e879f9','0xx':'#94a3b8' };

function updateStatus(counts) {
    const g = {'2xx':0,'3xx':0,'4xx':0,'5xx':0,'0xx':0};
    for (const [c,n] of Object.entries(counts)) {
        const k = c==='0'?'0xx':(c[0]+'xx');
        if (g[k]!==undefined) g[k]+=n; else g['0xx']+=n;
    }
    const labels=[],data=[],colors=[];
    for (const [k,v] of Object.entries(g)) if(v>0){labels.push(k);data.push(v);colors.push(SC[k]);}
    statusChart.data.labels=labels;
    statusChart.data.datasets[0].data=data;
    statusChart.data.datasets[0].backgroundColor=colors;
    statusChart.update();

    const list = document.getElementById('status-list');
    list.innerHTML = '';
    for (const [c,n] of Object.entries(counts).sort()) {
        if (!n) continue;
        const cls = c==='0'?'status-0xx':`status-${c[0]}xx`;
        const chip = document.createElement('span');
        chip.className = `status-chip ${cls}`;
        chip.textContent = `HTTP ${c==='0'?'ERR':c}: ${n.toLocaleString()}`;
        list.appendChild(chip);
    }
}

function pushRps(rps) {
    const d = rpsChart.data;
    d.labels.push(new Date().toLocaleTimeString());
    d.datasets[0].data.push(+rps.toFixed(1));
    if (d.labels.length > 120) { d.labels.shift(); d.datasets[0].data.shift(); }
    rpsChart.update();
}

/* ── Presets ──────────────────────────────────────────────────── */
function applyPreset(w, r, d) {
    document.getElementById('workers').value  = w;
    document.getElementById('rps').value      = r;
    document.getElementById('duration').value = d;
}

/* ── Start flood ──────────────────────────────────────────────── */
async function startFlood(e) {
    e.preventDefault();
    if (running) return;

    const target   = document.getElementById('target').value.trim();
    const method   = document.getElementById('attack-type').value;
    const workers  = +document.getElementById('workers').value;
    const rps      = +document.getElementById('rps').value;
    const duration = +document.getElementById('duration').value;

    floodDur   = duration;
    floodStart = Date.now();
    lastTotal  = 0;
    lastTick   = Date.now();

    setRunning(true);
    clearLogEl();
    log(`▶ ${method} flood → ${target}`, 'section');
    log(`  Workers: ${workers}  RPS: ${rps}  Duration: ${duration}s`, 'info');

    try {
        const r = await fetch('/api/start', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ target, method, workers, rps, duration })
        });
        const d = await r.json();
        if (!d.ok) { log(`✗ ${d.error}`, 'err'); setRunning(false); return; }
    } catch(err) {
        log(`✗ Cannot reach server: ${err.message}`, 'err');
        setRunning(false); return;
    }

    if (es) es.close();
    es = new EventSource('/api/stream');
    es.onmessage = ev => onEvent(JSON.parse(ev.data));
    es.onerror   = ()  => { if(running){ log('⚠ Stream lost','warn'); setRunning(false); } };
}

/* ── Stop ─────────────────────────────────────────────────────── */
async function stopFlood() {
    log('⚡ Stop requested…', 'warn');
    try { await fetch('/api/stop', { method: 'POST' }); } catch {}
}

/* ── SSE events ───────────────────────────────────────────────── */
function onEvent(d) {
    if (d.type === 'tick') {
        const now     = Date.now();
        const elapsed = (now - floodStart) / 1000;
        const pct     = Math.min(elapsed / floodDur * 100, 100);
        const delta   = (d.total||0) - lastTotal;
        const dt      = (now - (lastTick||now)) / 1000;
        const instRps = dt > 0 ? delta / dt : 0;
        lastTotal = d.total||0; lastTick = now;

        document.getElementById('stat-rps').textContent     = instRps.toFixed(0);
        document.getElementById('stat-total').textContent   = (d.total||0).toLocaleString();
        document.getElementById('stat-success').textContent = (d.success||0).toLocaleString();
        document.getElementById('stat-errors').textContent  = (d.errors||0).toLocaleString();
        document.getElementById('stat-elapsed').textContent = elapsed.toFixed(0)+'s';
        if (d.p50) document.getElementById('stat-p50').textContent = d.p50.toFixed(0);
        if (d.p99) document.getElementById('stat-p99').textContent = d.p99.toFixed(0);
        document.getElementById('progress-fill').style.width  = pct+'%';
        document.getElementById('progress-label').textContent = pct.toFixed(0)+'%';
        pushRps(instRps);
        if (d.status_counts) updateStatus(d.status_counts);
    }

    if (d.type === 'log') log(d.msg, d.level||'info');

    if (d.type === 'done') {
        running = false; setRunning(false);
        if (es) { es.close(); es = null; }
        log('─────────────────────────────────────', 'muted');
        log(`✔ ${d.stopped_early?'Stopped early':'Complete'} — ${(d.total||0).toLocaleString()} sent`, 'ok');
        log(`  RPS: ${(d.rps_actual||0).toFixed(1)}  p50: ${(d.p50||0).toFixed(0)}ms  p99: ${(d.p99||0).toFixed(0)}ms`, 'info');
        saveHistory({ time: new Date().toLocaleTimeString(), method: d.method||'–',
            target: d.target||'–', total: d.total||0,
            rps: (d.rps_actual||0).toFixed(1), p50: (d.p50||0).toFixed(0),
            p99: (d.p99||0).toFixed(0), success: d.success||0,
            errors: d.errors||0, duration: (d.wall||0).toFixed(1) });
    }
}

/* ── UI state ─────────────────────────────────────────────────── */
function setRunning(r) {
    running = r;
    const sb = document.getElementById('start-btn');
    const st = document.getElementById('stop-btn');
    const lb = document.getElementById('live-badge');
    const pw = document.getElementById('progress-wrap');
    sb.disabled     = r;
    sb.textContent  = r ? '⏳ Running…' : '🚀 Launch Flood';
    st.style.display = r ? 'inline-flex' : 'none';
    lb.style.display = r ? 'flex' : 'none';
    pw.style.display = r ? 'flex' : 'none';
    if (!r) {
        document.getElementById('progress-fill').style.width  = '100%';
        document.getElementById('progress-label').textContent = 'Done';
    }
}

/* ── Log ──────────────────────────────────────────────────────── */
function log(msg, cls='info') {
    const el = document.getElementById('log');
    const ph = el.querySelector('.muted');
    if (ph && ph.textContent.includes('Waiting')) ph.remove();
    const line = document.createElement('div');
    line.className = `log-line ${cls}`;
    line.textContent = msg;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
    while (el.children.length > 500) el.removeChild(el.firstChild);
}
function clearLogEl() { document.getElementById('log').innerHTML = ''; }
function clearLog() {
    clearLogEl();
    const l = document.createElement('div');
    l.className = 'log-line muted'; l.textContent = 'Log cleared.';
    document.getElementById('log').appendChild(l);
}

/* ── History ──────────────────────────────────────────────────── */
function saveHistory(e) {
    runHistory.unshift(e);
    if (runHistory.length > 50) runHistory.pop();
    localStorage.setItem('l7h', JSON.stringify(runHistory));
    renderHistory();
}
function renderHistory() {
    const tb = document.getElementById('history-body');
    if (!runHistory.length) {
        tb.innerHTML = '<tr><td colspan="10" class="empty-state">No runs yet</td></tr>';
        return;
    }
    tb.innerHTML = runHistory.map(h => `<tr>
        <td>${h.time}</td>
        <td><strong>${h.method}</strong></td>
        <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${h.target}</td>
        <td>${(+h.total).toLocaleString()}</td><td>${h.rps}</td>
        <td>${h.p50}ms</td><td>${h.p99}ms</td>
        <td style="color:#16a34a;font-weight:600">${(+h.success).toLocaleString()}</td>
        <td style="color:#dc2626;font-weight:600">${(+h.errors).toLocaleString()}</td>
        <td>${h.duration}s</td>
    </tr>`).join('');
}
function clearHistory() {
    runHistory = []; localStorage.removeItem('l7h'); renderHistory();
}
