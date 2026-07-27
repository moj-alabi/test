/* ── L7 Proxy Test Suite — Frontend ──────────────────────────── */

const API = 'http://localhost:5000';
let eventSource = null;
let rpsChart = null;
let statusChart = null;
let floodRunning = false;
let floodStart = null;
let floodDuration = 30;
let history = JSON.parse(localStorage.getItem('l7_history') || '[]');
let lastTotal = 0;
let lastTick = null;
let statusCounts = {};

/* ── Init ───────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    renderHistory();
    checkProxy();
    setInterval(checkProxy, 8000);
});

/* ── Proxy health check ─────────────────────────────────────── */
async function checkProxy() {
    const dot  = document.getElementById('proxy-dot');
    const text = document.getElementById('proxy-text');
    dot.className = 'status-dot checking';
    text.textContent = 'Proxy: checking…';
    try {
        const r = await fetch(`${API}/proxy-status`, { signal: AbortSignal.timeout(4000) });
        const d = await r.json();
        if (d.ok) {
            dot.className = 'status-dot online';
            text.textContent = `Proxy: online (${d.ms}ms)`;
        } else {
            dot.className = 'status-dot offline';
            text.textContent = 'Proxy: unreachable';
        }
    } catch {
        dot.className = 'status-dot offline';
        text.textContent = 'Proxy: unreachable';
    }
}

/* ── Charts ─────────────────────────────────────────────────── */
function initCharts() {
    const rpsCtx = document.getElementById('rpsChart').getContext('2d');
    rpsChart = new Chart(rpsCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'RPS',
                data: [],
                borderColor: '#ff6b35',
                backgroundColor: 'rgba(255,107,53,.1)',
                borderWidth: 2,
                pointRadius: 0,
                fill: true,
                tension: 0.4,
            }]
        },
        options: {
            responsive: true,
            animation: false,
            scales: {
                x: { display: false },
                y: { beginAtZero: true, grid: { color: 'rgba(0,0,0,.05)' } }
            },
            plugins: { legend: { display: false } }
        }
    });

    const sCtx = document.getElementById('statusChart').getContext('2d');
    statusChart = new Chart(sCtx, {
        type: 'doughnut',
        data: {
            labels: [],
            datasets: [{ data: [], backgroundColor: [] }]
        },
        options: {
            responsive: true,
            plugins: { legend: { position: 'right', labels: { font: { size: 11 } } } }
        }
    });
}

const STATUS_COLORS = {
    '2xx': '#4ade80', '3xx': '#facc15', '4xx': '#f87171',
    '5xx': '#e879f9', '0xx': '#94a3b8'
};

function updateStatusChart(counts) {
    const groups = { '2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0, '0xx': 0 };
    for (const [code, cnt] of Object.entries(counts)) {
        const g = code === '0' ? '0xx' : (code[0] + 'xx');
        if (groups[g] !== undefined) groups[g] += cnt; else groups['0xx'] += cnt;
    }
    const labels = [], data = [], colors = [];
    for (const [g, v] of Object.entries(groups)) {
        if (v > 0) { labels.push(g); data.push(v); colors.push(STATUS_COLORS[g]); }
    }
    statusChart.data.labels = labels;
    statusChart.data.datasets[0].data = data;
    statusChart.data.datasets[0].backgroundColor = colors;
    statusChart.update();

    // Status chips
    const list = document.getElementById('status-list');
    list.innerHTML = '';
    for (const [code, cnt] of Object.entries(counts).sort()) {
        if (cnt === 0) continue;
        const cls = code === '0' ? 'status-0xx' : `status-${code[0]}xx`;
        const chip = document.createElement('span');
        chip.className = `status-chip ${cls}`;
        chip.textContent = `HTTP ${code === '0' ? 'ERR' : code}: ${cnt.toLocaleString()}`;
        list.appendChild(chip);
    }
}

function pushRpsPoint(rps) {
    const d = rpsChart.data;
    const now = new Date().toLocaleTimeString();
    d.labels.push(now);
    d.datasets[0].data.push(+rps.toFixed(1));
    if (d.labels.length > 120) { d.labels.shift(); d.datasets[0].data.shift(); }
    rpsChart.update();
}

/* ── Preset helper ──────────────────────────────────────────── */
function applyPreset(workers, rps, duration) {
    document.getElementById('workers').value  = workers;
    document.getElementById('rps').value      = rps;
    document.getElementById('duration').value = duration;
}

/* ── Start flood ────────────────────────────────────────────── */
async function startFlood(e) {
    e.preventDefault();
    if (floodRunning) return;

    const target   = document.getElementById('target').value.trim();
    const method   = document.getElementById('attack-type').value;
    const workers  = parseInt(document.getElementById('workers').value);
    const rps      = parseFloat(document.getElementById('rps').value);
    const duration = parseFloat(document.getElementById('duration').value);

    floodDuration = duration;
    floodStart    = Date.now();
    floodRunning  = true;
    lastTotal     = 0;
    lastTick      = Date.now();
    statusCounts  = {};

    setRunning(true);
    clearLogEl();
    addLog(`▶ Starting ${method} flood → ${target}`, 'section');
    addLog(`  Workers: ${workers}  RPS: ${rps}  Duration: ${duration}s`, 'info');

    try {
        const resp = await fetch(`${API}/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target, method, workers, rps, duration })
        });
        const d = await resp.json();
        if (!d.ok) {
            addLog(`✗ Failed to start: ${d.error}`, 'err');
            setRunning(false);
            return;
        }
    } catch (err) {
        addLog(`✗ Cannot reach backend: ${err.message}`, 'err');
        setRunning(false);
        return;
    }

    // Open SSE stream for live metrics
    if (eventSource) eventSource.close();
    eventSource = new EventSource(`${API}/stream`);

    eventSource.onmessage = (ev) => {
        const d = JSON.parse(ev.data);
        handleMetric(d);
    };

    eventSource.onerror = () => {
        if (floodRunning) {
            addLog('⚠ Stream disconnected', 'warn');
            floodRunning = false;
            setRunning(false);
        }
    };
}

/* ── Stop flood ─────────────────────────────────────────────── */
async function stopFlood() {
    addLog('⚡ Stop requested…', 'warn');
    try {
        await fetch(`${API}/stop`, { method: 'POST' });
    } catch {}
}

/* ── Handle metric event ────────────────────────────────────── */
function handleMetric(d) {
    if (d.type === 'tick') {
        const now     = Date.now();
        const elapsed = (now - floodStart) / 1000;
        const pct     = Math.min(elapsed / floodDuration * 100, 100);

        // Compute instantaneous RPS from delta
        const deltaReqs = (d.total || 0) - lastTotal;
        const deltaSec  = (now - (lastTick || now)) / 1000;
        const instRps   = deltaSec > 0 ? deltaReqs / deltaSec : 0;
        lastTotal = d.total || 0;
        lastTick  = now;

        document.getElementById('stat-rps').textContent     = instRps.toFixed(0);
        document.getElementById('stat-total').textContent   = (d.total || 0).toLocaleString();
        document.getElementById('stat-success').textContent = (d.success || 0).toLocaleString();
        document.getElementById('stat-errors').textContent  = (d.errors || 0).toLocaleString();
        document.getElementById('stat-elapsed').textContent = elapsed.toFixed(0) + 's';
        if (d.p50) document.getElementById('stat-p50').textContent = d.p50.toFixed(0);
        if (d.p99) document.getElementById('stat-p99').textContent = d.p99.toFixed(0);

        document.getElementById('progress-fill').style.width = pct + '%';
        document.getElementById('progress-label').textContent = pct.toFixed(0) + '%';

        pushRpsPoint(instRps);

        if (d.status_counts) {
            statusCounts = d.status_counts;
            updateStatusChart(statusCounts);
        }
    }

    if (d.type === 'log') {
        const cls = d.level || 'info';
        addLog(d.msg, cls);
    }

    if (d.type === 'done') {
        floodRunning = false;
        setRunning(false);
        if (eventSource) { eventSource.close(); eventSource = null; }

        addLog('─────────────────────────────────────────', 'muted');
        addLog(`✔ Flood complete`, 'ok');
        addLog(`  Total: ${(d.total||0).toLocaleString()}  Success: ${(d.success||0).toLocaleString()}  Errors: ${(d.errors||0).toLocaleString()}`, 'ok');
        addLog(`  RPS: ${(d.rps_actual||0).toFixed(1)}  p50: ${(d.p50||0).toFixed(0)}ms  p99: ${(d.p99||0).toFixed(0)}ms`, 'info');

        saveHistory({
            time:     new Date().toLocaleTimeString(),
            method:   d.method || '–',
            target:   d.target || '–',
            total:    d.total  || 0,
            rps:      (d.rps_actual || 0).toFixed(1),
            p50:      (d.p50 || 0).toFixed(0),
            p99:      (d.p99 || 0).toFixed(0),
            success:  d.success || 0,
            errors:   d.errors  || 0,
            duration: (d.wall   || 0).toFixed(1),
        });
    }
}

/* ── UI helpers ─────────────────────────────────────────────── */
function setRunning(running) {
    floodRunning = running;
    const startBtn = document.getElementById('start-btn');
    const stopBtn  = document.getElementById('stop-btn');
    const badge    = document.getElementById('live-badge');
    const prog     = document.getElementById('progress-wrap');

    startBtn.disabled = running;
    startBtn.textContent = running ? '⏳ Running…' : '🚀 Launch Flood';
    stopBtn.style.display  = running ? 'inline-flex' : 'none';
    badge.style.display    = running ? 'flex' : 'none';
    prog.style.display     = running ? 'flex' : 'none';

    if (!running) {
        document.getElementById('progress-fill').style.width = '100%';
        document.getElementById('progress-label').textContent = 'Done';
    }
}

/* ── Log helpers ────────────────────────────────────────────── */
function addLog(msg, cls = 'info') {
    const log = document.getElementById('log');
    // Remove placeholder
    const placeholder = log.querySelector('.muted');
    if (placeholder && placeholder.textContent.includes('Waiting')) placeholder.remove();

    const line = document.createElement('div');
    line.className = `log-line ${cls}`;
    line.textContent = msg;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;

    // Keep max 500 lines
    while (log.children.length > 500) log.removeChild(log.firstChild);
}

function clearLogEl() {
    const log = document.getElementById('log');
    log.innerHTML = '';
}

function clearLog() {
    clearLogEl();
    const log = document.getElementById('log');
    const p = document.createElement('div');
    p.className = 'log-line muted';
    p.textContent = 'Log cleared.';
    log.appendChild(p);
}

/* ── History ────────────────────────────────────────────────── */
function saveHistory(entry) {
    history.unshift(entry);
    if (history.length > 50) history.pop();
    localStorage.setItem('l7_history', JSON.stringify(history));
    renderHistory();
}

function renderHistory() {
    const tbody = document.getElementById('history-body');
    if (!history.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No runs yet</td></tr>';
        return;
    }
    tbody.innerHTML = history.map(h => `
        <tr>
            <td>${h.time}</td>
            <td><strong>${h.method}</strong></td>
            <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${h.target}</td>
            <td>${(+h.total).toLocaleString()}</td>
            <td>${h.rps}</td>
            <td>${h.p50}ms</td>
            <td>${h.p99}ms</td>
            <td style="color:#16a34a;font-weight:600;">${(+h.success).toLocaleString()}</td>
            <td style="color:#dc2626;font-weight:600;">${(+h.errors).toLocaleString()}</td>
            <td>${h.duration}s</td>
        </tr>
    `).join('');
}

function clearHistory() {
    history = [];
    localStorage.removeItem('l7_history');
    renderHistory();
}
