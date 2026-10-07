#!/usr/bin/env python3
"""
Transform the SLCA003 Linux/Prometheus Grafana dashboard to:
  1. Make `nodename` and `node` (instance) template vars multi-select + All.
  2. Rewrite every panel query  instance="$node"  -> instance=~"$node"
     and  nodename="$nodename"  -> nodename=~"$nodename".
  3. Prefix selected netstat/sockstat legends with {{instance}} so multiple
     nodes are distinguishable when overlaid.
  4. Add a "TCP Flags (approx from node_exporter)" timeseries panel to the
     Network Netstat row.

Input : slca003-original.json   (the user's exported dashboard, verbatim)
Output: slca003-multinode.json  (import this into Grafana)
"""
import json
import re
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "slca003-original.json"
DST = sys.argv[2] if len(sys.argv) > 2 else "slca-network-ddos.json"

with open(SRC, "r", encoding="utf-8") as fh:
    raw = fh.read()

# --- 2. Regex-match the instance and nodename selectors everywhere ---
raw = raw.replace('instance=\\"$node\\"', 'instance=~\\"$node\\"')
raw = raw.replace('instance="$node"', 'instance=~"$node"')
raw = raw.replace('nodename=\\"$nodename\\"', 'nodename=~\\"$nodename\\"')
raw = raw.replace('nodename="$nodename"', 'nodename=~"$nodename"')

# The nodename variable is being removed (single Instance picker). Strip any
# leftover nodename filter from panel queries so they don't reference a dead var.
# Handles the common forms with a leading/trailing comma inside the selector.
raw = raw.replace(', nodename=~\\"$nodename\\"', '')
raw = raw.replace(',nodename=~\\"$nodename\\"', '')
raw = raw.replace('nodename=~\\"$nodename\\", ', '')
raw = raw.replace('nodename=~\\"$nodename\\",', '')

dash = json.loads(raw)

# --- 0. Keep ONLY networking rows/panels; drop CPU/mem/disk/process/etc. ---
# The network panels are top-level panels that follow their row header until the
# next row. We keep three network rows and every panel under them; collapsed
# non-network rows (and the leading Quick-stats gauges) are dropped entirely.
NETWORK_ROWS = {"Network Netstat", "Network Sockstat", "Network Traffic"}

kept = []
keeping = False  # are we currently inside a network row's panel span?
for p in dash["panels"]:
    if p.get("type") == "row":
        title = p.get("title", "")
        if title in NETWORK_ROWS:
            keeping = True
            # A collapsed network row would hide its children in `panels`; ours
            # are expanded with top-level panels after them, so just keep header.
            kept.append(p)
        else:
            keeping = False  # entering a non-network row -> stop keeping
        continue
    # Non-row panel: keep only if it belongs to a network row span.
    if keeping:
        kept.append(p)

dash["panels"] = kept

# --- 1. Single multi-select Instance picker (drop the nodename layer) ---
# Remove the nodename variable entirely and make `node` a standalone multi-select
# populated from all instances for the selected job. Panels already filter on
# instance=~"$node", so one dropdown now controls everything.
vlist = dash["templating"]["list"]
vlist = [v for v in vlist if v.get("name") != "nodename"]

for var in vlist:
    if var.get("name") == "node":
        var["label"] = "Instance"
        var["multi"] = True
        var["includeAll"] = True
        var["allValue"] = ".*"
        var["refresh"] = 2
        var["current"] = {"text": ["All"], "value": ["$__all"]}
        # Show the friendly nodename as the dropdown TEXT while using the instance
        # as the VALUE (what the panels filter on). Grafana supports this when the
        # Prometheus variable query returns full series and a regex names two
        # capture groups: (?<text>...) for the label shown and (?<value>...) for
        # the value used. We query node_uname_info (which carries both nodename
        # and instance) and map nodename->text, instance->value.
        q = 'node_uname_info{job="$job"}'
        var["definition"] = q
        var["query"] = {
            "qryType": 1,
            "query": q,
            "refId": "Prometheus-node-Variable-Query",
        }
        # Named capture groups: text = nodename, value = instance. Labels in the
        # series are serialized alphabetically (instance before nodename), so use
        # order-independent lookaheads rather than assuming a left-to-right order.
        var["regex"] = (
            '/(?=.*instance="(?<value>[^"]+)")'
            '(?=.*nodename="(?<text>[^"]+)")/'
        )
        var["regexApplyTo"] = "value"
        var["sort"] = 1

dash["templating"]["list"] = vlist

# --- 3. Prefix legends on correlation-relevant panels with {{instance}} ---
# Panels where overlaying multiple hosts matters for scan correlation.
LEGEND_PREFIX_TITLES = {
    "TCP Errors", "TCP In / Out", "UDP In / Out", "ICMP In / Out",
    "ICMP Errors", "UDP Errors", "TCP SynCookie", "TCP Connections",
    "TCP Direct Transition", "TCP Stat", "Sockstat TCP", "Sockstat UDP",
    "Sockstat Used", "Netstat IP In / Out Octets", "Network Traffic by Packets",
}


def prefix_legends(panel):
    if panel.get("title") in LEGEND_PREFIX_TITLES:
        for tgt in panel.get("targets", []):
            lf = tgt.get("legendFormat")
            if lf and "{{instance}}" not in lf and "{{ instance }}" not in lf:
                tgt["legendFormat"] = "{{instance}} - " + lf
    for child in panel.get("panels", []):
        prefix_legends(child)


for panel in dash["panels"]:
    prefix_legends(panel)

# --- 3b. Descriptions (hover-info tooltips on the panel title) + series tooltips ---
# IR-focused descriptions. These only ADD/UPGRADE a description where the panel's
# existing one is missing or thin; they never blank an existing good description.
IR_DESCRIPTIONS = {
    "TCP Errors": (
        "TCP error/congestion events. For scan detection watch RST Sent (closed "
        "ports reply RST, so a sharp burst = someone probing many ports) and SYN "
        "Retransmits/Listen Drops (half-open handshakes from SYN scans). A spike "
        "here with little change in TCP Connections (established) is a classic "
        "port-scan fingerprint."
    ),
    "TCP Connections": (
        "Currently ESTABLISHED TCP connections vs the system max. During a scan "
        "this stays LOW even while connection attempts spike - scans rarely keep "
        "connections open. Established staying flat while RST/attempts surge is the "
        "tell that it is a scan, not real traffic."
    ),
    "TCP Direct Transition": (
        "New TCP connection initiations per second. Active Opens = this host dialed "
        "out (outbound SYN). Passive Opens = inbound connections accepted (inbound "
        "SYN received). A sudden spike in Passive Opens from a quiet baseline means "
        "a burst of inbound handshakes - the arrival stage of a scan or flood."
    ),
    "TCP Stat": (
        "TCP sockets by state (needs node_exporter --collector.tcpstat). SYN_RECV "
        "rising = half-open handshakes (SYN scan). TIME_WAIT rising while "
        "established stays low = many short-lived connect-style probes (-sT/-sV "
        "scan). Compare established vs the churn states to classify the activity."
    ),
    "Sockstat TCP": (
        "TCP socket usage overview. Watch TIME_WAIT: it climbs when many "
        "connections are opened and closed in quick succession. TIME_WAIT high "
        "while In-Use (established) stays low is the signature of a full-connect "
        "or version scan sweeping ports/services."
    ),
    "TCP SynCookie": (
        "SYN cookie activity - the kernel's SYN-flood defense. Cookies Sent rising "
        "sharply means the SYN backlog filled up, i.e. a flood of half-open "
        "handshakes (SYN flood or an aggressive SYN scan). Cookies Failed can "
        "indicate spoofed or malformed SYNs."
    ),
    "ICMP In / Out": (
        "ICMP messages per second. A short spike in ICMP In with matching ICMP Out "
        "(echo request/reply) from a quiet baseline is a ping sweep - usually the "
        "FIRST recon step before a port scan. If ICMP spikes and the TCP panels "
        "stay flat, someone is just checking which hosts are alive."
    ),
    "Netstat IP In / Out Octets": (
        "IP-layer throughput (bytes/sec in and out). Use alongside the packet-rate "
        "panel: a scan shows a sharp spike then silence. Correlate the timestamp of "
        "any spike with firewall/Suricata logs to attribute it to a source IP, "
        "which node_exporter itself cannot show."
    ),
    "Network Traffic by Packets": (
        "Packets/sec per interface. The easiest scan tell: a flat baseline that "
        "jumps sharply for a few seconds then drops back. Real traffic ramps up and "
        "down gently; a scan arrives as a spike. Overlay multiple nodes to see the "
        "same burst land on the attacker and target at once."
    ),
}

# Panel titles that are timeseries and benefit from a full multi-series tooltip.
TOOLTIP_MULTI_TITLES = set(IR_DESCRIPTIONS) | set(LEGEND_PREFIX_TITLES)


IR_MARKER = "Scan tip:"


def enrich(panel):
    title = panel.get("title", "")
    # Append IR guidance to the existing description (keep the vendor text, add
    # a "Scan tip:" line). Idempotent: don't append twice on re-runs.
    new_desc = IR_DESCRIPTIONS.get(title)
    if new_desc:
        cur = (panel.get("description") or "").strip()
        if IR_MARKER not in cur:
            panel["description"] = (cur + "\n\n" if cur else "") + IR_MARKER + " " + new_desc
    # Richer series tooltip on timeseries panels used for correlation.
    if panel.get("type") == "timeseries" and title in TOOLTIP_MULTI_TITLES:
        opts = panel.setdefault("options", {})
        tt = opts.setdefault("tooltip", {})
        tt["mode"] = "multi"
        tt["sort"] = "desc"
        tt.setdefault("hideZeros", False)
    for child in panel.get("panels", []):
        enrich(child)


for panel in dash["panels"]:
    enrich(panel)

# --- 4. Build the TCP Flags approximation panel ---
flags_panel = {
    "datasource": {"type": "prometheus", "uid": "${ds_prometheus}"},
    "description": (
        "TCP handshake/flag activity APPROXIMATED from node_exporter counters. "
        "node_exporter does NOT expose true per-flag (SYN/ACK/SYN-ACK) counts - "
        "those come only from /proc/net/snmp aggregates. Mapping: inbound SYN "
        "approx PassiveOpens (new inbound handshakes), outbound SYN approx "
        "ActiveOpens, RST approx OutRsts, failed/half-open approx AttemptFails, "
        "SYN retransmits = TcpExt_TCPSynRetrans. A burst of PassiveOpens + OutRsts "
        "with few CurrEstab is the port-scan fingerprint. For true SYN/ACK/SYN-ACK "
        "flag decode, feed Suricata or an nstat/conntrack textfile collector."
    ),
    "fieldConfig": {
        "defaults": {
            "color": {"mode": "palette-classic"},
            "custom": {
                "axisBorderShow": False, "axisCenteredZero": False,
                "axisColorMode": "text", "axisLabel": "", "axisPlacement": "auto",
                "barAlignment": 0, "barWidthFactor": 0.6, "drawStyle": "line",
                "fillOpacity": 20, "gradientMode": "none",
                "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                "insertNulls": False, "lineInterpolation": "linear", "lineWidth": 1,
                "pointSize": 5, "scaleDistribution": {"type": "linear"},
                "showPoints": "never", "spanNulls": False,
                "stacking": {"group": "A", "mode": "none"},
                "thresholdsStyle": {"mode": "off"},
            },
            "links": [], "mappings": [], "min": 0,
            "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": 0}]},
            "unit": "pps",
        },
        "overrides": [
            {
                "matcher": {"id": "byRegexp", "options": "/.*RST.*/"},
                "properties": [{"id": "color", "value": {"fixedColor": "dark-red", "mode": "fixed"}}],
            }
        ],
    },
    "gridPos": {"h": 10, "w": 24, "x": 0, "y": 7},
    "id": 900,
    "options": {
        "legend": {"calcs": ["min", "mean", "max"], "displayMode": "table",
                   "placement": "bottom", "showLegend": True},
        "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
    },
    "pluginVersion": "12.4.0",
    "targets": [
        {
            "editorMode": "code",
            "expr": 'irate(node_netstat_Tcp_PassiveOpens{instance=~"$node",job="$job"}[$__rate_interval])',
            "legendFormat": "{{instance}} - inbound SYN (PassiveOpens)",
            "range": True, "refId": "A",
        },
        {
            "editorMode": "code",
            "expr": 'irate(node_netstat_Tcp_ActiveOpens{instance=~"$node",job="$job"}[$__rate_interval])',
            "legendFormat": "{{instance}} - outbound SYN (ActiveOpens)",
            "range": True, "refId": "B",
        },
        {
            "editorMode": "code",
            "expr": 'irate(node_netstat_Tcp_OutRsts{instance=~"$node",job="$job"}[$__rate_interval])',
            "legendFormat": "{{instance}} - RST sent (OutRsts)",
            "range": True, "refId": "C",
        },
        {
            "editorMode": "code",
            "expr": 'irate(node_netstat_Tcp_AttemptFails{instance=~"$node",job="$job"}[$__rate_interval])',
            "legendFormat": "{{instance}} - failed/half-open (AttemptFails)",
            "range": True, "refId": "D",
        },
        {
            "editorMode": "code",
            "expr": 'irate(node_netstat_TcpExt_TCPSynRetrans{instance=~"$node",job="$job"}[$__rate_interval])',
            "legendFormat": "{{instance}} - SYN retransmits",
            "range": True, "refId": "E",
        },
        {
            "editorMode": "code",
            "expr": 'node_netstat_Tcp_CurrEstab{instance=~"$node",job="$job"}',
            "legendFormat": "{{instance}} - established (gauge)",
            "range": True, "refId": "F",
        },
    ],
    "title": "TCP Flags / Handshake Activity (approx from node_exporter)",
    "type": "timeseries",
}

# Insert the flags panel right after the "Network Netstat" row header.
panels = dash["panels"]
insert_at = None
for i, p in enumerate(panels):
    if p.get("type") == "row" and p.get("title") == "Network Netstat":
        insert_at = i + 1
        break
if insert_at is None:
    # Fallback: append near the end before the last row.
    insert_at = len(panels)
panels.insert(insert_at, flags_panel)


# --- 5. DDoS detection row ---------------------------------------------------
_PANEL_ID = 950


def ts_panel(title, unit, targets, desc, overrides=None, stacking="none"):
    """Compact helper to build a timeseries panel."""
    global _PANEL_ID
    _PANEL_ID += 1
    return {
        "datasource": {"type": "prometheus", "uid": "${ds_prometheus}"},
        "description": desc,
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "axisBorderShow": False, "axisCenteredZero": False,
                    "axisColorMode": "text", "axisLabel": "", "axisPlacement": "auto",
                    "barAlignment": 0, "barWidthFactor": 0.6, "drawStyle": "line",
                    "fillOpacity": 20, "gradientMode": "none",
                    "hideFrom": {"legend": False, "tooltip": False, "viz": False},
                    "insertNulls": False, "lineInterpolation": "linear", "lineWidth": 1,
                    "pointSize": 5, "scaleDistribution": {"type": "linear"},
                    "showPoints": "never", "spanNulls": False,
                    "stacking": {"group": "A", "mode": stacking},
                    "thresholdsStyle": {"mode": "off"},
                },
                "links": [], "mappings": [], "min": 0,
                "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": 0}]},
                "unit": unit,
            },
            "overrides": overrides or [],
        },
        "gridPos": {"h": 9, "w": 12, "x": 0, "y": 0},
        "id": _PANEL_ID,
        "options": {
            "legend": {"calcs": ["mean", "max"], "displayMode": "table",
                       "placement": "bottom", "showLegend": True},
            "tooltip": {"hideZeros": False, "mode": "multi", "sort": "desc"},
        },
        "pluginVersion": "12.4.0",
        "targets": [
            {"editorMode": "code", "expr": e, "legendFormat": lf, "range": True,
             "refId": chr(65 + i)}
            for i, (e, lf) in enumerate(targets)
        ],
        "title": title,
        "type": "timeseries",
    }


I = 'instance=~"$node",job="$job"'  # shared selector
RI = "[$__rate_interval]"

ddos_row = {"collapsed": False, "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
            "id": 949, "panels": [], "title": "DDoS / Flood Indicators", "type": "row"}

ddos_panels = [
    ts_panel(
        "DDoS: Inbound Packet & Connection Rate",
        "pps",
        [
            (f'sum(rate(node_network_receive_packets_total{{{I}}}{RI})) by (instance)',
             "{{instance}} - rx packets/s"),
            (f'irate(node_netstat_Tcp_PassiveOpens{{{I}}}{RI})',
             "{{instance}} - new inbound conns/s (PassiveOpens)"),
        ],
        "Volumetric baseline. A sustained, order-of-magnitude jump in received "
        "packets/sec and new inbound connections/sec that does NOT return to "
        "baseline is the core volumetric-DDoS signal. A scan spikes then stops; a "
        "flood stays high. Overlay all exposed hosts to see a coordinated hit.",
    ),
    ts_panel(
        "DDoS: SYN Flood Indicators",
        "pps",
        [
            (f'irate(node_netstat_TcpExt_SyncookiesSent{{{I}}}{RI})',
             "{{instance}} - SYN cookies sent/s"),
            (f'irate(node_netstat_TcpExt_SyncookiesFailed{{{I}}}{RI})',
             "{{instance}} - SYN cookies FAILED/s"),
            (f'irate(node_netstat_Tcp_PassiveOpens{{{I}}}{RI})',
             "{{instance}} - PassiveOpens/s"),
            (f'node_netstat_Tcp_CurrEstab{{{I}}}',
             "{{instance}} - established (gauge)"),
        ],
        "SYN flood = a storm of half-open handshakes. When the SYN backlog fills, "
        "the kernel emits SYN cookies, so SyncookiesSent rising sharply is the "
        "classic SYN-flood tell. PassiveOpens climbing while CurrEstab stays flat "
        "means connections start but never complete (spoofed/abandoned SYNs). "
        "SyncookiesFailed suggests spoofed or malformed SYNs.",
        overrides=[{"matcher": {"id": "byRegexp", "options": "/.*FAILED.*/"},
                    "properties": [{"id": "color", "value": {"fixedColor": "dark-red", "mode": "fixed"}}]}],
    ),
    ts_panel(
        "DDoS: Listen Backlog Overflows & Half-Open Churn",
        "pps",
        [
            (f'irate(node_netstat_TcpExt_ListenOverflows{{{I}}}{RI})',
             "{{instance}} - listen overflows/s"),
            (f'irate(node_netstat_TcpExt_ListenDrops{{{I}}}{RI})',
             "{{instance}} - listen drops/s"),
            (f'irate(node_netstat_Tcp_AttemptFails{{{I}}}{RI})',
             "{{instance}} - attempt fails/s"),
            (f'irate(node_netstat_Tcp_OutRsts{{{I}}}{RI})',
             "{{instance}} - RST sent/s"),
        ],
        "When a service's accept queue is overwhelmed, the kernel drops new "
        "connections: ListenOverflows/ListenDrops climb. Combined with high "
        "AttemptFails (handshakes that never finished) this points at a connection "
        "flood exhausting the listener, legit clients get refused during this.",
    ),
    ts_panel(
        "DDoS: UDP Flood & Reflection",
        "pps",
        [
            (f'irate(node_netstat_Udp_InDatagrams{{{I}}}{RI})',
             "{{instance}} - UDP in/s"),
            (f'irate(node_netstat_Udp_OutDatagrams{{{I}}}{RI})',
             "{{instance}} - UDP out/s"),
            (f'irate(node_netstat_Udp_NoPorts{{{I}}}{RI})',
             "{{instance}} - UDP to closed port/s (NoPorts)"),
            (f'irate(node_netstat_Udp_InErrors{{{I}}}{RI})',
             "{{instance}} - UDP in errors/s"),
        ],
        "UDP floods and reflection/amplification abuse. A spike in UDP in/s, "
        "especially with NoPorts rising (datagrams hitting closed ports), is "
        "garbage/reflected traffic rather than real sessions. If UDP out/s spikes "
        "too, this host may be an unwitting reflector/amplifier.",
        overrides=[{"matcher": {"id": "byRegexp", "options": "/.*closed port.*/"},
                    "properties": [{"id": "color", "value": {"fixedColor": "orange", "mode": "fixed"}}]}],
    ),
    ts_panel(
        "DDoS: ICMP Flood",
        "pps",
        [
            (f'irate(node_netstat_Icmp_InMsgs{{{I}}}{RI})',
             "{{instance}} - ICMP in/s"),
            (f'irate(node_netstat_Icmp_OutMsgs{{{I}}}{RI})',
             "{{instance}} - ICMP out/s"),
            (f'irate(node_netstat_Icmp_InErrors{{{I}}}{RI})',
             "{{instance}} - ICMP in errors/s"),
        ],
        "ICMP flood (ping flood / smurf). A large sustained ICMP in/s rate is the "
        "signal. If ICMP out/s mirrors it, the host is busy replying to a ping "
        "flood (wasting CPU/bandwidth). Short low blips are just ping sweeps, not "
        "a flood.",
    ),
    ts_panel(
        "DDoS: Kernel Packet Drops Under Load",
        "pps",
        [
            (f'sum(rate(node_network_receive_drop_total{{{I}}}{RI})) by (instance)',
             "{{instance}} - NIC rx drops/s"),
            (f'sum(rate(node_softnet_dropped_total{{{I}}}{RI})) by (instance)',
             "{{instance}} - softnet drops/s"),
            (f'sum(irate(node_softnet_times_squeezed_total{{{I}}}{RI})) by (instance)',
             "{{instance}} - softnet squeezed/s"),
        ],
        "When traffic exceeds what the host can process, packets are dropped at "
        "the NIC/kernel: rx drops and softnet drops climb, and 'times squeezed' "
        "(CPU ran out of budget draining the queue) rises. Non-zero drops during a "
        "traffic spike means the box is saturated - a successful volumetric DDoS.",
        overrides=[{"matcher": {"id": "byRegexp", "options": "/.*drops.*/"},
                    "properties": [{"id": "color", "value": {"fixedColor": "dark-red", "mode": "fixed"}}]}],
    ),
    ts_panel(
        "DDoS: Conntrack Table Exhaustion",
        "short",
        [
            (f'node_nf_conntrack_entries{{{I}}}',
             "{{instance}} - conntrack entries"),
            (f'node_nf_conntrack_entries_limit{{{I}}}',
             "{{instance}} - conntrack LIMIT"),
            (f'100 * node_nf_conntrack_entries{{{I}}} / node_nf_conntrack_entries_limit{{{I}}}',
             "{{instance}} - conntrack % used"),
        ],
        "Connection-tracking table exhaustion. A flood of connections fills "
        "nf_conntrack; when entries approach the limit, the firewall starts "
        "dropping NEW connections for everyone (effective outage). Watch the % "
        "used line approach 100 - that is table-exhaustion DoS.",
        overrides=[{"matcher": {"id": "byRegexp", "options": "/.*LIMIT.*/"},
                    "properties": [
                        {"id": "color", "value": {"fixedColor": "dark-red", "mode": "fixed"}},
                        {"id": "custom.lineStyle", "value": {"dash": [10, 10], "fill": "dash"}},
                        {"id": "custom.fillOpacity", "value": 0},
                    ]}],
    ),
    ts_panel(
        "DDoS: Bandwidth Saturation",
        "bps",
        [
            (f'sum(rate(node_network_receive_bytes_total{{{I}}}{RI})*8) by (instance)',
             "{{instance}} - rx bits/s"),
            (f'sum(rate(node_network_transmit_bytes_total{{{I}}}{RI})*8) by (instance)',
             "{{instance}} - tx bits/s"),
        ],
        "Raw ingress/egress bandwidth. Compare rx bits/s against the interface "
        "Speed (see Network Traffic row). A volumetric DDoS pins rx near link "
        "capacity and holds it there; that sustained ceiling, not a brief spike, "
        "is the saturation signature.",
    ),
]

# Lay the DDoS panels out in a 2-column grid under the row header.
for idx, p in enumerate(ddos_panels):
    p["gridPos"]["x"] = 0 if idx % 2 == 0 else 12
    p["gridPos"]["y"] = 2 + (idx // 2) * 9

# Put the DDoS row first (it's the headline for incident response).
dash["panels"] = [ddos_row] + ddos_panels + dash["panels"]

# --- Final metadata: this is a standalone NETWORKING dashboard --------------
dash["version"] = 1
dash["title"] = "SLCA - NETWORK & DDoS MONITOR [PROMETHEUS]"
dash["uid"] = "slca-net-ddos"
dash["tags"] = ["linux", "Prometheus", "network", "ddos", "security"]

with open(DST, "w", encoding="utf-8") as fh:
    json.dump(dash, fh, indent=2)

print(f"OK wrote {DST}")
print(f"panels (top-level): {len(dash['panels'])}")
print(f"ddos panels added : {len(ddos_panels)}")
