'use strict';

// src/tiger_sdn/api/static/app.js
//
// Original: sdn-intent-framework's src/xai_pipeline/static/app.js (3574 lines).
//
// Stage 9 scope cuts applied (see docs/plan.md and index.html's header comment):
//   - RAG: no enableRag state, no rag_k/no_rag in the /api/run request body.
//   - Custom topology editor: dropped TOPOLOGY_PRESETS, the whole `editor`
//     state machine (enterEditMode/exitEditMode/renderEditorGraph/
//     renderPropsPanel/editorAddNode/editorPushHistory/editorUndo/lasso), and
//     applyTopology()/applyPreset()/updateExampleChips(). The topology panel
//     renders exactly one topology: GET /api/topology.
//   - Live traffic preset simulator: dropped all netPreset* functions/state,
//     /api/network-preset/* and /api/traffic-presets calls, and bandwidth-based
//     link coloring (bwLinkColor/bwLinkStroke/bwLabelsVisible) — the API's link
//     objects carry no bandwidth field, so links render as plain lines.
//   - Digital Twin packet-animation overlay: dropped onTwinInfo/onTwinBw/
//     highlightPath/clearPathHighlight/spawnPacket/startPacketLoop/
//     setTwinPhase/findTopoPath/getTwinLayer/renderTwinHighlights/stopTwinViz.
//     The ported twin only emits plain-text progress lines (no structured
//     twin_info/twin_bw SSE events), so twin progress is plain log lines in
//     the stage card via appendLogLine() — kept from the reference as-is.
//   - Flow-state cache / "Load State": dropped loadFlowState/clearLoadedState/
//     deleteFlowStateEntry/clearAllFlowState/renderLoadStateBtn/
//     renderSavedStateTab/activateFlowTab and all /api/flow-state/* calls.
//     FLOW RULES shows only the live ONOS table.
//
// The reference pipeline has 6 numbered stages (ev.stage: 1..6, name strings
// like "① Intent Parsing"). tiger_sdn's real pipeline has 5 gates keyed by
// string name (parse/grounding/compile/static_validation/twin,
// orchestrate/pipeline.py) plus a synthetic 6th ("deploy") added by the API
// layer (api/app.py's _run()). STAGE_DEFS/buildStageCards/renderStage/
// handleSSEEvent below are keyed by that string, not ev.stage as an integer.
// The repair loop (orchestrate/repair.py) re-runs "parse" up to
// MAX_REPAIR_ATTEMPTS times when grounding/static_validation reject; it shows
// up here as a `{"type":"repair","attempt":N,"reason":...}` event that tags
// the parse stage card with a "· Repair N" suffix (see handleSSEEvent).
//
// The backend does not send a per-stage `elapsed` field (unlike the
// reference), so stage-card timers are computed client-side from the
// running->terminal wall-clock gap instead of trusting the SSE payload.

// ── Constants ─────────────────────────────────────────────────────────────────

const STAGE_DEFS = [
  { key: 'parse',              name: '① Intent Parsing' },
  { key: 'grounding',          name: '② Grounding' },
  { key: 'compile',            name: '③ FlowRule Compile' },
  { key: 'static_validation',  name: '④ Static Validation' },
  { key: 'twin',                name: '⑤ Digital Twin' },
  { key: 'deploy',              name: '⑥ ONOS Deploy' },
];
const PP_LABELS = ['Parse', 'Ground', 'Compile', 'Validate', 'Twin', 'Deploy'];

// Stage statuses the backend actually sends: idle (client-only) | running |
// done | error | rejected | skipped. 'rejected' has no dedicated CSS variant
// — it's visually an error (red), but kept as a distinct string in state so
// detail renderers can tell "the gate refused this" from "the gate crashed".
const STATUS_ALIAS = { rejected: 'error' };
function cssStatus(status) { return STATUS_ALIAS[status] || status; }

// ── State ─────────────────────────────────────────────────────────────────────

// 스테이지 카드가 running 상태로 최소 이 시간(ms)은 보이도록 보장
const MIN_STAGE_VISIBLE_MS = 700;
const _stageRunningAt = {}; // stageKey → Date.now() when running was shown

const state = {
  intent: '',
  model: 'gemini-3.1-flash-lite',
  skipTwin: false,
  skipDeploy: false,
  running: false,
  stages: STAGE_DEFS.map(s => ({
    ...s,
    status: 'idle',   // idle | running | done | error | rejected | skipped
    elapsed: null,
    data: null,        // extra fields from the terminal "stage" event (program/flowrule/result/findings/error/...)
    expanded: false,
    progress_log: [],
    repairAttempt: null,
    repairReason: null,
  })),
  decision: null,
  decisionReport: null,
  deployFailed: false,
  confidenceBreakdown: {},
  history: [],
  twinActive: false, // true while the twin stage is running — Mininet's virtual
                      // switches connect to ONOS and disturb the live topology,
                      // so topology polling pauses until the twin stage ends.
};

function stageByKey(key) {
  return state.stages.find(s => s.key === key) || null;
}

// ── Pipeline ──────────────────────────────────────────────────────────────────

async function runPipeline() {
  if (state.running || !state.intent.trim()) return;

  state.running = true;
  state.decision = null;
  state.decisionReport = null;
  state.deployFailed = false;
  state.confidenceBreakdown = {};
  state.stages.forEach(s => {
    s.status = 'idle'; s.elapsed = null; s.data = null; s.expanded = false;
    s.progress_log = []; s.repairAttempt = null; s.repairReason = null;
  });
  Object.keys(_stageRunningAt).forEach(k => delete _stageRunningAt[k]);
  buildStageCards();   // 이전 실행 카드 DOM 전체 초기화
  renderDecision();
  setRunBtn(true);

  try {
    const resp = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        intent: state.intent,
        model: state.model,
        skip_twin: state.skipTwin,
        skip_deploy: state.skipDeploy,
      }),
    });

    if (!resp.ok) throw new Error(`Server error: ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const chunks = buf.split('\n\n');
      buf = chunks.pop(); // keep incomplete chunk
      for (const chunk of chunks) {
        const line = chunk.trim();
        if (line.startsWith('data: ')) {
          try {
            handleSSEEvent(JSON.parse(line.slice(6)));
          } catch { /* skip malformed */ }
        }
      }
    }
  } catch (err) {
    console.error('Pipeline error:', err);
  } finally {
    state.running = false;
    setRunBtn(false);
    loadHistory();
  }
}

function handleSSEEvent(ev) {
  if (ev.type === 'progress') {
    const s = stageByKey(ev.stage);
    if (!s) return;
    s.progress_log.push(ev.msg);
    appendLogLine(ev.stage, ev.msg);
  } else if (ev.type === 'repair') {
    // orchestrate/pipeline.py re-runs "parse" after grounding/static_validation
    // rejects. Tag the parse card so the retry is visible; the next
    // stage=parse/running event (attempt=ev.attempt) will follow shortly.
    const parseStage = stageByKey('parse');
    if (parseStage) {
      parseStage.repairAttempt = ev.attempt;
      parseStage.repairReason = ev.reason;
      renderStage('parse');
    }
  } else if (ev.type === 'stage') {
    const s = stageByKey(ev.stage);
    if (!s) return;

    if (ev.status === 'running') {
      s.status = 'running';
      ensureStageCard(ev.stage);
      _stageRunningAt[ev.stage] = Date.now();
      renderStage(ev.stage);
      renderPipelineProgress();
      if (ev.stage === 'twin') state.twinActive = true;
    } else {
      ensureStageCard(ev.stage); // skipped처럼 running 없이 끝나는 경우 대비

      const applyFinal = () => {
        s.status = ev.status;
        const startedAt = _stageRunningAt[ev.stage];
        s.elapsed = startedAt != null ? ((Date.now() - startedAt) / 1000).toFixed(1) : s.elapsed;

        const extra = { ...ev };
        delete extra.type; delete extra.stage; delete extra.status; delete extra.attempt;
        s.data = extra;

        if (ev.status === 'error' || ev.status === 'rejected') s.expanded = true;
        if (ev.stage === 'deploy' && ev.status === 'error') state.deployFailed = true;

        renderStage(ev.stage);
        renderPipelineProgress();
        if (ev.stage === 'deploy') renderDecision();

        if (ev.stage === 'twin') {
          state.twinActive = false;
          fetchTopology();
        }
      };

      const ranAt = _stageRunningAt[ev.stage];
      if (ranAt) {
        const waited = Date.now() - ranAt;
        const delay  = Math.max(0, MIN_STAGE_VISIBLE_MS - waited);
        if (delay > 0) setTimeout(applyFinal, delay);
        else applyFinal();
      } else {
        // running 이벤트 없이 바로 종료 (skip 등): 카드 진입 애니메이션만 기다림
        setTimeout(applyFinal, 120);
      }
    }
  } else if (ev.type === 'decision') {
    state.decision = ev.report.decision;
    state.decisionReport = ev.report;
    state.confidenceBreakdown = (ev.report && ev.report.confidence_breakdown) || {};
    // REJECT 시 실패한 단계 모두 자동 펼치기
    if (ev.report.decision === 'REJECT') {
      state.stages.forEach(s => {
        if (s.status === 'error' || s.status === 'rejected') {
          s.expanded = true;
          renderStage(s.key);
        }
      });
    }
    renderConfidenceBadges();
    renderDecision();
  } else if (ev.type === 'error') {
    // Pipeline-level error before any stage ran (empty/too-long intent,
    // topology load failure) — api/app.py's _run() emits this without a
    // "stage" wrapper, so surface it straight on the decision banner.
    state.decision = 'ERROR';
    state.decisionReport = { decision_reason: ev.error };
    renderDecision();
  } else if (ev.type === 'done') {
    state.decision = ev.decision;
    if (!state.decisionReport) {
      // Early-exit REJECT/ERROR paths (parse/compile failure, repair budget
      // exhausted) never emit a "decision" event — build_decision() only runs
      // once the twin stage completes. Synthesize a minimal report so the
      // banner still shows something.
      state.decisionReport = { decision_reason: ev.reason || '' };
    }
    renderDecision();
  }
}

// ── API Calls ─────────────────────────────────────────────────────────────────

let topoSnapshot = null; // JSON string of the last rendered topology, for cheap diffing

async function fetchTopology() {
  try {
    const resp = await fetch('/api/topology');
    const data = await resp.json();

    if (data.error) {
      if (!topoSnapshot) showTopoError(data.error);
      return;
    }

    // Digital Twin 실행 중: Mininet 가상 스위치가 ONOS에 연결되어
    // 토폴로지가 교란됨 → 표시 업데이트를 중단하고 이전 스냅샷 유지
    if (state.twinActive) return;

    const key = JSON.stringify({
      nodes: data.nodes,
      links: data.links,
      flow_table: data.flow_table,
      rule_count: data.rule_count,
    });
    if (key === topoSnapshot) return; // 변경 없으면 렌더 스킵
    topoSnapshot = key;

    updateTopology(data);
    updateMetrics(data);
    updateHostLegend(data);
    updateFlowTable(data);
  } catch {
    // 네트워크 오류 — 이전 데이터 유지
  }
}

async function loadHistory() {
  try {
    const resp = await fetch('/api/logs');
    state.history = await resp.json();
    renderHistory();
  } catch { /* silent */ }
}

// ── Rendering: Pipeline Progress ─────────────────────────────────────────────

function renderPipelineProgress() {
  const el        = document.getElementById('pipeline-progress');
  const track     = document.getElementById('pp-track');
  const steps     = document.getElementById('pp-steps');
  const nameEl    = document.getElementById('pp-stage-name');
  const counterEl = document.getElementById('pp-counter');
  const pctEl     = document.getElementById('pp-pct');

  const anyActive = state.stages.some(s => s.status !== 'idle');
  if (!anyActive) { el.style.display = 'none'; return; }
  el.style.display = 'block';

  const running   = state.stages.find(s => s.status === 'running');
  const doneCount = state.stages.filter(s => ['done', 'skipped', 'error', 'rejected'].includes(s.status)).length;
  const total     = state.stages.length;
  const pctVal    = Math.round(doneCount / total * 100);

  if (running) {
    nameEl.textContent = running.name.replace(/^[①②③④⑤⑥]\s*/, '');
    nameEl.style.color = '#60a5fa';
  } else {
    const last = [...state.stages].reverse().find(s => s.status !== 'idle');
    if (last) {
      if (last.status === 'error' || last.status === 'rejected') {
        nameEl.textContent = '✕ ' + last.name.replace(/^[①②③④⑤⑥]\s*/, '');
        nameEl.style.color = '#ef4444';
      } else if (doneCount === total) {
        nameEl.textContent = '✓ Pipeline Complete';
        nameEl.style.color = '#10b981';
      } else {
        nameEl.textContent = last.name.replace(/^[①②③④⑤⑥]\s*/, '');
        nameEl.style.color = '#9ca3af';
      }
    }
  }

  counterEl.textContent = `${doneCount} / ${total}`;
  pctEl.textContent = ` · ${pctVal}%`;

  const segs = track.querySelectorAll('.pp-seg');
  const stps = steps.querySelectorAll('.pp-step');
  state.stages.forEach((s, i) => {
    const vis = cssStatus(s.status);
    const segClass  = `pp-seg${vis !== 'idle' ? ' seg-' + vis : ''}`;
    const stepClass = `pp-step${vis !== 'idle' ? ' step-' + vis : ''}`;
    if (segs[i]) {
      segs[i].className = segClass;
    } else {
      const seg = document.createElement('div');
      seg.className = segClass;
      track.appendChild(seg);
    }
    if (stps[i]) {
      stps[i].className = stepClass;
    } else {
      const step = document.createElement('div');
      step.className = stepClass;
      step.textContent = PP_LABELS[i] || s.key;
      steps.appendChild(step);
    }
  });
}

function buildStageCards() {
  const section = document.getElementById('stages-section');
  const ppEl    = document.getElementById('pipeline-progress');
  const cards   = Array.from(section.querySelectorAll('.stage-card'));
  if (cards.length === 0) {
    section.style.display = 'none';
    ppEl.style.display = 'none';
    return;
  }
  ppEl.style.opacity = '0';
  ppEl.style.transition = 'opacity 0.3s ease';
  cards.forEach(card => card.classList.add('stage-card-exit'));
  setTimeout(() => {
    section.innerHTML = '';
    section.style.display = 'none';
    ppEl.style.display = 'none';
    ppEl.style.opacity = '';
    ppEl.style.transition = '';
    document.getElementById('pp-track').innerHTML = '';
    document.getElementById('pp-steps').innerHTML = '';
  }, 350);
}

function ensureStageCard(key) {
  const section = document.getElementById('stages-section');
  if (document.getElementById(`stage-${key}`)) return;

  const def = STAGE_DEFS.find(d => d.key === key);
  if (!def) return;
  const pos = STAGE_DEFS.indexOf(def) + 1;
  const hasConf = key === 'static_validation' || key === 'twin';

  const card = document.createElement('div');
  card.className = 'stage-card stage-card-enter';
  card.id = `stage-${key}`;
  card.innerHTML = `
    <div class="stage-header" data-key="${key}">
      <div class="stage-badge" id="badge-${key}">${pos}</div>
      <div class="stage-name" id="name-${key}">${def.name}</div>
      <div class="stage-time" id="time-${key}">—</div>
      ${hasConf ? `<div class="stage-conf" id="conf-${key}" style="display:none"></div>` : ''}
      <div class="stage-icon" id="icon-${key}">${iconDot()}</div>
    </div>
    <div class="stage-progress" id="progress-${key}">
      <div class="stage-progress-bar" id="progress-bar-${key}"></div>
    </div>
    <div class="live-log" id="live-${key}"></div>
    <div class="stage-detail" id="detail-${key}"></div>
  `;
  card.querySelector('.stage-header').addEventListener('click', () => {
    const s = stageByKey(key);
    s.expanded = !s.expanded;
    renderStage(key);
  });
  section.style.display = 'flex';
  section.appendChild(card);
  requestAnimationFrame(() => requestAnimationFrame(() => card.classList.remove('stage-card-enter')));
}

function renderStage(key) {
  const s = stageByKey(key);
  if (!s) return;
  const card = document.getElementById(`stage-${key}`);
  if (!card) return;

  const badge   = document.getElementById(`badge-${key}`);
  const nameEl  = document.getElementById(`name-${key}`);
  const timeEl  = document.getElementById(`time-${key}`);
  const iconEl  = document.getElementById(`icon-${key}`);
  const liveEl  = document.getElementById(`live-${key}`);
  const detail  = document.getElementById(`detail-${key}`);
  const progBar = document.getElementById(`progress-bar-${key}`);

  const vis = cssStatus(s.status);

  card.className = `stage-card${vis === 'running' ? ' border-running' : vis === 'error' ? ' border-error' : ''}`;

  badge.className = s.status === 'idle' ? 'stage-badge' : `stage-badge badge-${vis}`;

  if (nameEl) {
    const def = STAGE_DEFS.find(d => d.key === key);
    nameEl.textContent = s.repairAttempt ? `${def.name} · Repair ${s.repairAttempt}` : def.name;
  }

  timeEl.textContent = s.elapsed != null ? `${s.elapsed}s` : '—';

  iconEl.innerHTML = statusIcon(vis);

  if (progBar) {
    progBar.className = 'stage-progress-bar';
    if (vis === 'running')       progBar.classList.add('bar-running');
    else if (vis === 'done')     progBar.classList.add('bar-done');
    else if (vis === 'error')    progBar.classList.add('bar-error');
    else if (vis === 'skipped')  progBar.classList.add('bar-skipped');
  }

  if (liveEl) {
    if (s.status === 'running') {
      liveEl.style.display = 'block';
    } else {
      liveEl.style.display = 'none';
      liveEl.querySelectorAll('.log-line.current').forEach(el => {
        el.classList.remove('current');
        el.classList.add('done');
      });
    }
  }

  if (s.expanded && s.status !== 'idle' && s.status !== 'running') {
    detail.className = 'stage-detail open';
    detail.innerHTML = renderStageDetail(s);
  } else {
    detail.className = 'stage-detail';
    detail.innerHTML = '';
  }
}

// ── Stage Detail Renderers ────────────────────────────────────────────────────
//
// Each stage's `s.data` holds whatever extra fields the terminal SSE event
// carried, keyed exactly as orchestrate/pipeline.py and api/app.py emit them:
//   parse:              done → {program: IntentPrediction dump}; rejected/error → {reason} / {error}
//   grounding:          rejected → {findings: ValidationFinding[]}; done → {}
//   compile:            done → {flowrule: OnosFlowSet dump}; error → {error}
//   static_validation:  done/rejected → {result: StaticResult dict}
//   twin:               done/skipped/error → {result: TwinResult dict}
//   deploy:              done/error → {result: {success, flow_ids, error}}

function renderStageDetail(s) {
  const d = s.data || {};
  try {
    switch (s.key) {
      case 'parse':             return renderParseDetail(s.status, d);
      case 'grounding':         return renderGroundingDetail(s.status, d);
      case 'compile':           return renderCompileDetail(s.status, d);
      case 'static_validation': return renderStaticDetail(d);
      case 'twin':               return renderTwinDetail(d);
      case 'deploy':             return renderDeployDetail(d);
      default: return `<pre style="font-size:11px;color:#9ca3af;white-space:pre-wrap">${escHtml(JSON.stringify(d, null, 2))}</pre>`;
    }
  } catch (e) {
    return `<pre style="font-size:11px;color:#9ca3af;white-space:pre-wrap">${escHtml(JSON.stringify(d, null, 2))}</pre>`;
  }
}

/* parse — Intent IR (tiger_sdn.ir.model.IntentRule field names) */
function irTable(rule) {
  const sel = rule.selector || {};
  const enf = rule.enforcement || {};
  const qos = rule.qos || {};
  const routing = rule.routing || {};
  const rows = [
    ['Action', rule.action],
    ['Intent Type', rule.intent_type],
    ['Device', enf.device],
    ['Source', sel.source && (sel.source.host || sel.source.ip)],
    ['Destination', sel.destination && (sel.destination.host || sel.destination.ip)],
    ['Eth Type', sel.eth_type],
    ['Protocol', sel.protocol],
    ['Src Port', sel.src_port],
    ['Dst Port', sel.dst_port],
    ['In Port', sel.in_port],
    ['Egress Port', enf.egress_port],
    ['Alt Egress Port', enf.alt_egress_port],
    ['VLAN', enf.set_vlan_id],
    ['QoS Queue', qos.queue],
    ['Min BW (Mbps)', qos.min_bandwidth_mbps],
    ['Max Latency (ms)', qos.max_latency_ms],
    ['Waypoints', Array.isArray(routing.waypoints) ? routing.waypoints.join(' → ') : null],
    ['Via Device', routing.via_device],
    ['Avoid Device', routing.avoid_device],
    ['Priority', rule.priority],
    ['SFC Role', rule.sfc_role],
  ].filter(([, v]) => v != null && v !== '');
  return `<table class="sd-table">${rows.map(([k, v]) =>
    `<tr><td class="sd-key">${k}</td><td class="sd-val">${escHtml(String(v))}</td></tr>`).join('')}</table>`;
}

function renderParseDetail(status, d) {
  if (status === 'rejected' || status === 'error') {
    const msg = d.reason || d.error || '';
    return `<div class="sd-section"><span class="sd-badge sd-badge-error">${status === 'rejected' ? 'Rejected' : 'Error'}</span>
      <div class="sd-reason">${escHtml(msg)}</div></div>`;
  }
  const program = d.program && d.program.program; // IntentPrediction dump: {status, program, rejection}
  if (!program) return `<div class="sd-section"><span class="sd-badge sd-badge-ok">Accepted</span></div>`;
  const rules = program.rules || [];
  if (rules.length > 1) {
    return `<div class="sd-section"><span class="sd-badge sd-badge-info">Compound Intent — ${rules.length} rules</span>${program.description ? `<div class="sd-desc">${escHtml(program.description)}</div>` : ''}</div>` +
      rules.map((rule, i) => `<div class="sd-section"><div class="sd-label">Rule ${i + 1} — ${(rule.action || '').toUpperCase()}</div>${irTable(rule)}</div>`).join('');
  }
  const rule = rules[0];
  const cls = rule.action === 'block' ? 'sd-badge-block' : rule.action === 'forward' ? 'sd-badge-forward' : 'sd-badge-info';
  return `<div class="sd-section"><span class="sd-badge ${cls}">${(rule.action || 'INTENT').toUpperCase()}</span></div>
    <div class="sd-section">${irTable(rule)}</div>`;
}

/* grounding — verify.grounding.ValidationFinding[] */
function renderGroundingDetail(status, d) {
  if (status === 'rejected') {
    const findings = d.findings || [];
    return `<div class="sd-section"><span class="sd-badge sd-badge-error">Rejected</span></div>` +
      (findings.length
        ? `<div class="sd-section"><div class="sd-label">Findings</div>${findings.map(f =>
            `<div class="sd-error-item">[${escHtml(f.category || '')}/${escHtml(f.code || '')}] ${escHtml(f.message || '')}</div>`).join('')}</div>`
        : '');
  }
  return `<div class="sd-section"><div class="sd-ok-msg">✓ All references resolved — no unknown hosts/devices, no shadowing conflicts.</div></div>`;
}

/* compile — compile.onos.OnosFlowSet dump */
function flowCard(f, idx) {
  const criteria = (f.selector && f.selector.criteria) || [];
  const treatment = f.treatment;
  const instructions = (treatment && treatment.instructions) || [];
  const isBlock = !treatment || instructions.length === 0;

  const actionStr = isBlock
    ? '<span class="sd-action-drop">⛔ DROP</span>'
    : instructions.map(inst => {
        if (inst.type === 'OUTPUT') return `<span class="sd-action-forward">→ Port ${escHtml(String(inst.port))}</span>`;
        if (inst.type === 'QUEUE') return `<span>Queue ${inst.queueId ?? ''}</span>`;
        if (inst.type === 'L2MODIFICATION') return `<span>VLAN ${inst.vlanId ?? ''}</span>`;
        return `<span>${escHtml(inst.type)}</span>`;
      }).join(' ');

  const matchRows = criteria.map(c => {
    const v = c.ip ?? c.ethType ?? c.protocol ?? c.tcpPort ?? c.udpPort ?? c.port ?? '';
    return `<tr><td class="sd-key">${escHtml(String(c.type).replace(/_/g, ' '))}</td><td class="sd-val">${escHtml(String(v))}</td></tr>`;
  }).join('');

  const devShort = (f.deviceId || '').replace('of:000000000000', 's');
  return `<div class="sd-flow-card">
    <div class="sd-flow-header">
      <span class="sd-flow-title">Rule ${idx + 1}</span>
      <span class="sd-flow-device">${escHtml(devShort)} · Pri ${f.priority ?? '—'}</span>
    </div>
    <div class="sd-flow-body">
      <div class="sd-match-col">
        <div class="sd-sublabel">Match</div>
        <table class="sd-table">${matchRows || '<tr><td class="sd-key" colspan="2" style="color:#4b5563">any</td></tr>'}</table>
      </div>
      <div class="sd-action-col">
        <div class="sd-sublabel">Action</div>
        <div class="sd-action-str">${actionStr}</div>
      </div>
    </div>
  </div>`;
}

function renderCompileDetail(status, d) {
  if (status === 'error') {
    return `<div class="sd-section"><span class="sd-badge sd-badge-error">Error</span><div class="sd-reason">${escHtml(d.error || '')}</div></div>`;
  }
  const flows = (d.flowrule && d.flowrule.flows) || [];
  return `<div class="sd-section"><span class="sd-meta">${flows.length} flow rule${flows.length !== 1 ? 's' : ''} compiled</span></div>` +
    flows.map((f, i) => flowCard(f, i)).join('');
}

/* static_validation — verify.static.StaticResult */
function renderStaticDetail(d) {
  const r = d.result || {};
  const passed = r.passed;
  const errors = r.schema_errors || [];
  const conflicts = r.conflicts || [];
  const warnings = r.warnings || [];

  let html = `<div class="sd-section"><span class="sd-badge ${passed ? 'sd-badge-ok' : 'sd-badge-error'}">${passed ? '✓ Passed' : '✗ Failed'}</span></div>`;
  if (errors.length) html += `<div class="sd-section"><div class="sd-label">Schema Errors</div>${errors.map(e => `<div class="sd-error-item">${escHtml(e)}</div>`).join('')}</div>`;
  if (conflicts.length) html += `<div class="sd-section"><div class="sd-label">Conflicts</div>
    <table class="sd-table"><tr><th>Type</th><th>Reason</th></tr>${conflicts.map(c =>
      `<tr><td class="sd-key">${escHtml(c.conflict_type || '')}</td><td class="sd-val">${escHtml(c.reason || '')}</td></tr>`).join('')}</table></div>`;
  if (warnings.length) html += `<div class="sd-section"><div class="sd-label">Warnings</div>${warnings.map(w => `<div class="sd-warn-item">⚠ ${escHtml(w)}</div>`).join('')}</div>`;
  if (!errors.length && !conflicts.length && !warnings.length) html += `<div class="sd-section"><div class="sd-ok-msg">✓ No schema errors, conflicts, or warnings found.</div></div>`;
  return html;
}

/* twin — twin.twin_verifier.TwinResult */
function renderTwinDetail(d) {
  const r = d.result || {};
  const stCls = r.status === 'passed' ? 'sd-badge-ok' : r.status === 'skipped' ? 'sd-badge-skip' : 'sd-badge-error';
  const checks = r.checks || {};
  const evidence = r.evidence || {};

  const checkRows = Object.entries(checks).map(([name, ok]) => {
    const label = name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    return `<div class="sd-check-row">
      <span class="sd-check-icon" style="color:${ok ? '#10b981' : '#ef4444'}">${ok ? '✓' : '✗'}</span>
      <span class="sd-check-name">${escHtml(label)}</span>
      <span class="sd-check-status ${ok ? 'check-pass' : 'check-fail'}">${ok ? 'PASS' : 'FAIL'}</span>
    </div>`;
  }).join('');

  const evidRows = Object.entries(evidence).filter(([, v]) => v != null).map(([k, v]) =>
    `<tr><td class="sd-key">${escHtml(k.replace(/_/g, ' '))}</td><td class="sd-val">${escHtml(String(v))}</td></tr>`).join('');

  return `<div class="sd-section"><span class="sd-badge ${stCls}">${(r.status || '—').toUpperCase()}</span>${r.reason ? `<div class="sd-reason">${escHtml(r.reason)}</div>` : ''}</div>` +
    (checkRows ? `<div class="sd-section"><div class="sd-label">Verification Checks</div><div class="sd-checks">${checkRows}</div></div>` : '') +
    (evidRows  ? `<div class="sd-section"><div class="sd-label">Evidence</div><table class="sd-table">${evidRows}</table></div>` : '');
}

/* deploy — deploy.Deployer result */
function renderDeployDetail(d) {
  const r = d.result || {};
  const ok = r.success;
  const flowIds = r.flow_ids || [];

  return `<div class="sd-section"><span class="sd-badge ${ok ? 'sd-badge-ok' : 'sd-badge-error'}">${ok ? '✓ Deployed' : '✗ Failed'}</span></div>` +
    (r.error ? `<div class="sd-section"><div class="sd-error-item">${escHtml(r.error)}</div></div>` : '') +
    (flowIds.length ? `<div class="sd-section"><div class="sd-label">${flowIds.length} Flow Rule${flowIds.length > 1 ? 's' : ''} Installed</div>${flowIds.map(id => `<div class="sd-flow-id">${escHtml(String(id))}</div>`).join('')}</div>` : '') +
    (!flowIds.length && ok ? `<div class="sd-section"><div class="sd-ok-msg">✓ Rules applied successfully.</div></div>` : '');
}

function renderConfidenceBadges() {
  const map = { static_validation: 'static', twin: 'twin' };
  Object.entries(map).forEach(([key, breakdownKey]) => {
    const el = document.getElementById(`conf-${key}`);
    if (!el) return;
    const score = state.confidenceBreakdown[breakdownKey];
    if (score == null) { el.style.display = 'none'; return; }
    const pct = Math.round(score * 100);
    const color = pct >= 80 ? '#10b981' : pct >= 50 ? '#f59e0b' : '#ef4444';
    el.style.display = 'flex';
    el.style.color = color;
    el.style.borderColor = color + '40';
    el.textContent = `${pct}%`;
  });
}

// ── Decision Banner ───────────────────────────────────────────────────────────
//
// explain.decision.DecisionReport has no separate "explanation" pipeline
// stage in this backend (unlike the reference's numbered stage 5) — the
// report arrives via a standalone {"type":"decision"} SSE event. So its
// confidence/evidence content is folded into the decision banner itself
// instead of inventing a stage card that the event stream never produces.

function renderDecision() {
  const el = document.getElementById('decision-banner');
  if (!state.decision) { el.style.display = 'none'; return; }

  const deployFailed = !!state.deployFailed;
  const approve = String(state.decision).includes('APPROVE') && !deployFailed;
  const color   = approve ? '#10b981' : '#ef4444';
  const bgColor = approve ? '#0f1c16' : '#1c0f0f';
  const icon    = approve
    ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0a0e1a" stroke-width="3"><path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round"/></svg>`
    : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0a0e1a" stroke-width="3"><path d="M18 6L6 18M6 6l12 12" stroke-linecap="round"/></svg>`;

  const report = state.decisionReport || {};
  const baseReason = report.decision_reason || '';
  const reason = deployFailed
    ? `⚠ ONOS deploy failed — verification passed but the rule was not actually installed. ${baseReason}`.trim()
    : baseReason;
  // "done"'s decision already reads DEPLOY_FAILED once the run has actually
  // finished; the suffix only adds information in the brief window between
  // the deploy stage's error event and that final "done" arriving.
  const title = (deployFailed && !String(state.decision).includes('DEPLOY_FAILED'))
    ? `${state.decision} (Deploy Failed)`
    : state.decision;

  const conf = report.confidence != null ? Math.round(report.confidence * 100) : null;
  const confColor = conf >= 80 ? '#10b981' : conf >= 50 ? '#f59e0b' : '#ef4444';
  const breakdown = report.confidence_breakdown || {};
  const bdRows = Object.entries(breakdown).map(([stg, score]) => {
    const pct = Math.round(score * 100);
    const col = pct >= 80 ? '#10b981' : pct >= 50 ? '#f59e0b' : '#ef4444';
    return `<div class="sd-conf-row">
      <span class="sd-conf-label">${escHtml(stg)}</span>
      <div class="sd-conf-bar-wrap"><div class="sd-conf-bar-fill" style="width:${pct}%;background:${col}"></div></div>
      <span class="sd-conf-pct" style="color:${col}">${pct}%</span>
    </div>`;
  }).join('');

  const evidence = report.evidence || [];
  const evItems = evidence.map(e => `<div class="sd-evidence-item">
    <span class="sd-ev-stage">${escHtml(e.stage || '')}</span>
    <span class="sd-ev-finding">${escHtml(e.finding || '')}</span>
  </div>`).join('');

  el.style.display = 'flex';
  el.style.background = bgColor;
  el.style.borderColor = color;
  el.innerHTML = `
    <div class="decision-icon" style="background:${color}">${icon}</div>
    <div style="flex:1;min-width:0">
      <div class="decision-title" style="color:${color}">${escHtml(String(title))}</div>
      <div class="decision-reason">${escHtml(reason)}</div>
      ${conf != null ? `<div class="sd-section" style="margin-top:10px"><div class="sd-label">Overall Confidence</div>
        <div class="sd-conf-overall"><div class="sd-conf-bar-wrap large"><div class="sd-conf-bar-fill" style="width:${conf}%;background:${confColor}"></div></div>
        <span class="sd-conf-pct large" style="color:${confColor}">${conf}%</span></div></div>` : ''}
      ${bdRows ? `<div class="sd-section"><div class="sd-label">Stage Breakdown</div>${bdRows}</div>` : ''}
      ${evItems ? `<div class="sd-section"><div class="sd-label">Evidence</div>${evItems}</div>` : ''}
    </div>
  `;
}

// ── History ───────────────────────────────────────────────────────────────────

function renderHistory() {
  const el = document.getElementById('history-list');
  if (!el) return;
  if (state.history.length === 0) {
    el.innerHTML = '<div style="font-size:11px;color:#4b5563;font-family:\'JetBrains Mono\',monospace">No runs yet</div>';
    return;
  }
  el.innerHTML = state.history.map(h => {
    const badge = h.decision
      ? `<span style="font-size:10px;color:${h.decision.includes('APPROVE') ? '#10b981' : '#ef4444'}">${h.decision.includes('APPROVE') ? '✓' : '✗'}</span> `
      : '';
    return `<div class="history-item" title="${escHtml(h.intent)}" data-intent="${escHtml(h.intent)}">${badge}${escHtml(h.intent)}</div>`;
  }).join('');
  el.querySelectorAll('.history-item').forEach(item => {
    item.addEventListener('click', () => fillIntent(item.dataset.intent));
  });
}

// ── Topology Panel: Metrics / Host Legend / Flow Table ───────────────────────

function updateMetrics(data) {
  const nodes = data.nodes || [];
  const links = data.links || [];
  const switches = nodes.filter(n => n.type === 'switch').length;
  const hosts    = nodes.filter(n => n.type === 'host').length;
  const swLinks  = links.filter(l => {
    const s = typeof l.source === 'object' ? l.source.id : l.source;
    const t = typeof l.target === 'object' ? l.target.id : l.target;
    return s && t && s.startsWith('of:') && t.startsWith('of:');
  }).length;

  document.getElementById('metric-switches').textContent = switches || '—';
  document.getElementById('metric-hosts').textContent    = hosts    || '—';
  document.getElementById('metric-links').textContent    = swLinks  || '—';
  document.getElementById('metric-rules').textContent    = data.rule_count ?? '—';
}

function updateHostLegend(data) {
  const body = document.getElementById('host-legend-body');
  if (!body) return;
  const hosts = (data.nodes || []).filter(n => n.type === 'host' && n.ip);
  if (hosts.length === 0) {
    body.innerHTML = '<div style="color:#4b5563;font-size:11px;padding:4px 8px">No host info</div>';
    return;
  }
  body.innerHTML = hosts.map(h => {
    const label = h.label || h.id;
    return `<div class="host-legend-row">
      <span class="host-legend-dot"></span>
      <span class="host-legend-label">${escHtml(String(label))}</span>
      <span class="host-legend-ip">${escHtml(String(h.ip))}</span>
      <span></span>
    </div>`;
  }).join('');
}

function updateFlowTable(data) {
  const body = document.getElementById('flow-table-body');
  const rows = data.flow_table || [];
  if (rows.length === 0) {
    body.innerHTML = `<div style="padding:12px 10px;font-size:11px;color:#4b5563;font-family:'JetBrains Mono',monospace">No flow rules</div>`;
    return;
  }
  body.innerHTML = rows.map(r => {
    const actionColor = r.action === 'FORWARD' ? '#10b981' : r.action === 'DROP' ? '#f59e0b' : '#9ca3af';
    return `
      <div class="flow-table-row">
        <div class="flow-cell-device">${escHtml(String(r.device))}</div>
        <div class="flow-cell-pri">${r.priority}</div>
        <div class="flow-cell-match">${escHtml(String(r.match))}</div>
        <div class="flow-cell-action" style="color:${actionColor}">
          <span class="flow-action-dot" style="background:${actionColor}"></span>
          ${escHtml(String(r.action))}
        </div>
      </div>`;
  }).join('');
}

function showTopoError(msg) {
  const el = document.getElementById('topology-placeholder');
  if (el) {
    el.style.display = 'flex';
    el.textContent = `ONOS offline — ${msg}`;
  }
}

// ── D3 Topology ───────────────────────────────────────────────────────────────

let topoSvg = null;
let topoZoom = null;
let topoZoomLayer = null;
let simulation = null;
let topoFittedKey = null; // node-set signature the camera was last fitted to
const nodePositions = new Map(); // persist positions across refreshes

function initTopology() {
  const container = document.getElementById('topology-graph');
  const w = container.clientWidth  || 342;
  const h = container.clientHeight || 280;

  topoSvg = d3.select('#topology-graph')
    .append('svg')
    .attr('width',  '100%')
    .attr('height', '100%')
    .attr('viewBox', `0 0 ${w} ${h}`);

  topoZoomLayer = topoSvg.append('g').attr('class', 'zoom-layer');
  topoZoomLayer.append('g').attr('class', 'links');
  topoZoomLayer.append('g').attr('class', 'nodes');

  topoZoom = d3.zoom()
    .scaleExtent([0.2, 4])
    .on('zoom', ev => topoZoomLayer.attr('transform', ev.transform));

  topoSvg.call(topoZoom);

  topoSvg.on('dblclick.zoom', () => {
    if (simulation) fitTopoToView(simulation.nodes());
    else topoSvg.transition().duration(300).call(topoZoom.transform, d3.zoomIdentity);
  });
}

function updateTopology(data) {
  if (!topoSvg) initTopology();

  const placeholder = document.getElementById('topology-placeholder');
  if (placeholder) placeholder.style.display = 'none';

  const { nodes, links } = data;
  if (!nodes || nodes.length === 0) {
    showTopoError('No devices found');
    return;
  }

  const container = document.getElementById('topology-graph');
  const w = container.clientWidth  || 342;
  const h = container.clientHeight || 280;

  topoSvg.attr('viewBox', `0 0 ${w} ${h}`);

  // Seed positions: prefer stored positions, then backend-provided x/y
  nodes.forEach(n => {
    const prev = nodePositions.get(n.id);
    if (prev) { n.x = prev.x; n.y = prev.y; }
    else if (n.x != null && n.y != null) {
      nodePositions.set(n.id, { x: n.x, y: n.y });
    }
  });

  if (simulation) simulation.stop();

  simulation = d3.forceSimulation(nodes)
    .force('link',      d3.forceLink(links).id(d => d.id).distance(70))
    .force('charge',    d3.forceManyBody().strength(-180))
    .force('center',    d3.forceCenter(w / 2, h / 2))
    .force('collision', d3.forceCollide(24));

  // Links — no bandwidth field in the API's link data, so links are plain lines.
  const link = topoZoomLayer.select('.links')
    .selectAll('line')
    .data(links, d => `${d.source?.id ?? d.source}-${d.target?.id ?? d.target}`)
    .join('line')
    .attr('stroke', '#374151')
    .attr('stroke-width', 1.5);

  // Nodes
  const drag = d3.drag()
    .on('start', (ev, d) => {
      if (!ev.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
    .on('end',  (ev, d) => {
      if (!ev.active) simulation.alphaTarget(0);
      d.fx = null; d.fy = null;
    });

  const nodeG = topoZoomLayer.select('.nodes')
    .selectAll('g.live-node')
    .data(nodes, d => d.id)
    .join(enter => enter.append('g').attr('class', 'live-node'))
    .call(drag);

  nodeG.selectAll('*').remove();

  nodeG.each(function(d) {
    const g = d3.select(this);
    const fill = nodeColor(d);

    if (d.type === 'switch') {
      g.append('polygon')
        .attr('points', '0,-15 13,-7.5 13,7.5 0,15 -13,7.5 -13,-7.5')
        .attr('fill', fill)
        .attr('stroke', '#0a0e1a')
        .attr('stroke-width', 1.5);
    } else {
      g.append('circle')
        .attr('r', 10)
        .attr('fill', '#111827')
        .attr('stroke', '#3b82f6')
        .attr('stroke-width', 2);
    }

    g.append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .attr('font-size', 9)
      .attr('font-family', 'JetBrains Mono, monospace')
      .attr('fill', d.type === 'switch' ? '#0a0e1a' : '#f9fafb')
      .attr('font-weight', '700')
      .attr('pointer-events', 'none')
      .text(d.label);
  });

  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);
    nodeG.attr('transform', d => {
      nodePositions.set(d.id, { x: d.x, y: d.y });
      return `translate(${d.x},${d.y})`;
    });
  });

  simulation.on('end', () => {
    nodes.forEach(n => nodePositions.set(n.id, { x: n.x, y: n.y }));
    const key = nodes.map(n => n.id).sort().join('|');
    if (key !== topoFittedKey) {
      topoFittedKey = key;
      fitTopoToView(nodes);
    }
  });
}

/**
 * Zoom/pan the graph so every node is visible.
 */
function fitTopoToView(nodes, animate = true) {
  if (!topoSvg || !topoZoom || !nodes || !nodes.length) return;
  const el = document.getElementById('topology-graph');
  if (!el) return;
  const w = el.clientWidth || 342;
  const h = el.clientHeight || 280;
  const pad = 34;

  const xs = nodes.map(n => n.x).filter(Number.isFinite);
  const ys = nodes.map(n => n.y).filter(Number.isFinite);
  if (!xs.length || !ys.length) return;

  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const k = Math.min((w - pad * 2) / Math.max(maxX - minX, 1),
                     (h - pad * 2) / Math.max(maxY - minY, 1),
                     1.5);
  const transform = d3.zoomIdentity
    .translate(w / 2 - k * (minX + maxX) / 2, h / 2 - k * (minY + maxY) / 2)
    .scale(k);

  (animate ? topoSvg.transition().duration(400) : topoSvg)
    .call(topoZoom.transform, transform);
}

function nodeColor(d) {
  if (d.type === 'host') return '#3b82f6';
  return { forward: '#10b981', drop: '#f59e0b', offline: '#ef4444', idle: '#6b7280' }[d.state] || '#6b7280';
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function statusIcon(status) {
  if (status === 'done')
    return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="3"><path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  if (status === 'running')
    return `<svg width="16" height="16" viewBox="0 0 24 24" style="animation:spin 1s linear infinite"><circle cx="12" cy="12" r="9" fill="none" stroke="#1f2937" stroke-width="3"/><path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="#3b82f6" stroke-width="3" stroke-linecap="round"/></svg>`;
  if (status === 'error')
    return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="3"><path d="M18 6L6 18M6 6l12 12" stroke-linecap="round"/></svg>`;
  if (status === 'skipped')
    return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6b7280" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>`;
  return iconDot();
}

function iconDot() {
  return `<div style="width:8px;height:8px;border-radius:50%;background:#374151;margin:auto"></div>`;
}

function appendLogLine(stageKey, msg) {
  const el = document.getElementById(`live-${stageKey}`);
  if (!el) return;

  const prev = el.querySelector('.log-line.current');
  if (prev) {
    prev.classList.remove('current');
    prev.classList.add('done');
  }

  const line = document.createElement('div');
  line.className = 'log-line current';
  if (msg.startsWith('✓')) line.classList.add('log-ok');
  else if (msg.startsWith('✗')) line.classList.add('log-fail');
  else if (msg.startsWith('⚠')) line.classList.add('log-warn');
  line.textContent = msg;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function fillIntent(text) {
  state.intent = text;
  document.getElementById('intent-input').value = text;
}

function setRunBtn(running) {
  const btn = document.getElementById('run-btn');
  btn.disabled = running;
  btn.innerHTML = running
    ? `<svg width="14" height="14" viewBox="0 0 24 24" style="animation:spin 1s linear infinite"><circle cx="12" cy="12" r="9" fill="none" stroke="rgba(255,255,255,0.3)" stroke-width="3"/><path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="white" stroke-width="3" stroke-linecap="round"/></svg> Running...`
    : `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Run Pipeline`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Topology Refresh Loop ─────────────────────────────────────────────────────

let topoFetching = false;

function startRefreshLoop() {
  async function tick() {
    if (!topoFetching) {
      topoFetching = true;
      await fetchTopology();
      topoFetching = false;
    }
    setTimeout(tick, 1000);
  }
  tick();
}

// ── Init ──────────────────────────────────────────────────────────────────────

function init() {
  buildStageCards();

  const intentInput = document.getElementById('intent-input');
  intentInput.addEventListener('input', e => {
    state.intent = e.target.value;
  });

  document.getElementById('run-btn').addEventListener('click', runPipeline);

  initIntentPresets();

  document.getElementById('model-select').addEventListener('change', e => { state.model = e.target.value; });
  document.getElementById('toggle-twin').addEventListener('change', e => { state.skipTwin  = e.target.checked; });
  document.getElementById('toggle-deploy').addEventListener('change', e => { state.skipDeploy = e.target.checked; });

  if (localStorage.getItem('layout-swapped') === 'true') {
    document.getElementById('app').classList.add('layout-swapped');
  }
  document.getElementById('layout-swap-btn').addEventListener('click', toggleLayoutSwap);

  document.getElementById('clear-history-btn').addEventListener('click', async () => {
    if (!confirm('Clear all run history?')) return;
    await fetch('/api/logs', { method: 'DELETE' });
    state.history = [];
    renderHistory();
  });

  loadHistory();
  startRefreshLoop();

  initSidebarToggle();
  initPanelResizer();

  const topoPanel = document.getElementById('topology-panel');
  if (topoPanel && !localStorage.getItem('topo-panel-width')) {
    topoPanel.style.width = Math.floor(window.innerWidth * 0.5) + 'px';
  } else if (topoPanel && localStorage.getItem('topo-panel-width')) {
    topoPanel.style.width = localStorage.getItem('topo-panel-width');
  }

  document.querySelectorAll('.topo-info-section').forEach(section => {
    const body = section.querySelector('.topo-info-body');
    if (!body) return;
    section.classList.add('collapsed');
    body.style.maxHeight = '0';
  });
}

// ── Panel Resizer ─────────────────────────────────────────────────────────────

function initPanelResizer() {
  const resizer = document.getElementById('panel-resizer');
  const panel   = document.getElementById('topology-panel');
  if (!resizer || !panel) return;

  let startX, startWidth;

  resizer.addEventListener('mousedown', e => {
    startX     = e.clientX;
    startWidth = panel.offsetWidth;
    resizer.classList.add('dragging');
    document.body.style.userSelect = 'none';
    document.body.style.cursor     = 'col-resize';
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup',   onMouseUp);
    e.preventDefault();
  });

  function onMouseMove(e) {
    const swapped  = document.getElementById('app').classList.contains('layout-swapped');
    const delta    = swapped ? e.clientX - startX : startX - e.clientX;
    const minWidth = 220;
    const maxWidth = Math.floor(window.innerWidth * 0.6);
    const newWidth = Math.min(Math.max(startWidth + delta, minWidth), maxWidth);
    panel.style.width = newWidth + 'px';
    syncTopoSize();
  }

  function onMouseUp() {
    resizer.classList.remove('dragging');
    document.body.style.userSelect = '';
    document.body.style.cursor     = '';
    localStorage.setItem('topo-panel-width', panel.style.width);
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup',   onMouseUp);
  }
}

// ── Topology Size Sync ────────────────────────────────────────────────────────

function syncTopoSize() {
  if (!topoSvg) return;
  const container = document.getElementById('topology-graph');
  const w = container.clientWidth  || 342;
  const h = container.clientHeight || 280;
  topoSvg.attr('viewBox', `0 0 ${w} ${h}`);
  if (simulation) {
    simulation.force('center', d3.forceCenter(w / 2, h / 2));
    simulation.alpha(0.3).restart();
  }
}

// ── Collapsible Info Sections ─────────────────────────────────────────────────

function toggleInfoSection(id) {
  const section = document.getElementById(id);
  if (!section) return;
  const body      = section.querySelector('.topo-info-body');
  const collapsed = section.classList.toggle('collapsed');
  body.style.maxHeight = collapsed ? '0' : body.scrollHeight + 'px';
  setTimeout(syncTopoSize, 260);
}

function initIntentPresets() {
  const btn  = document.getElementById('intent-preset-btn');
  const menu = document.getElementById('intent-preset-menu');

  btn.addEventListener('click', e => {
    e.stopPropagation();
    const isOpen = menu.classList.contains('open');
    if (!isOpen) {
      menu.style.visibility = 'hidden';
      menu.style.display = 'block';
      const menuH = menu.offsetHeight;
      menu.style.display = '';
      menu.style.visibility = '';

      const r = btn.getBoundingClientRect();
      const spaceBelow = window.innerHeight - r.bottom - 8;
      if (spaceBelow >= menuH || spaceBelow >= 200) {
        menu.style.top    = (r.bottom + 5) + 'px';
        menu.style.bottom = '';
      } else {
        menu.style.top    = '';
        menu.style.bottom = (window.innerHeight - r.top + 5) + 'px';
      }
      menu.style.left = r.left + 'px';
    }
    menu.classList.toggle('open');
  });

  document.addEventListener('click', () => menu.classList.remove('open'));

  menu.querySelectorAll('.preset-item').forEach(item => {
    item.addEventListener('click', e => {
      e.stopPropagation();
      menu.classList.remove('open');
      fillIntent(item.dataset.intent);
    });
  });
}

function toggleTopoPanel() {
  const panel = document.getElementById('topology-panel');
  const collapsed = panel.classList.toggle('topo-collapsed');
  setTimeout(syncTopoSize, 310);
  if (collapsed) {
    panel._savedWidth = panel.style.width;
    panel.style.width = '';
  } else {
    if (panel._savedWidth) panel.style.width = panel._savedWidth;
    setTimeout(syncTopoSize, 310);
  }
}

// ── Sidebar Toggle ────────────────────────────────────────────────────────────

function initSidebarToggle() {
  const btn     = document.getElementById('sidebar-toggle-btn');
  const sidebar = document.getElementById('sidebar');
  const stored  = localStorage.getItem('sidebar-collapsed') === 'true';

  if (stored) {
    sidebar.classList.add('collapsed');
    btn.textContent = '▶';
    btn.title = 'Expand sidebar';
  }

  btn.addEventListener('click', () => {
    const collapsed = sidebar.classList.toggle('collapsed');
    btn.textContent = collapsed ? '▶' : '◀';
    btn.title       = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    localStorage.setItem('sidebar-collapsed', collapsed);
  });
}

function toggleLayoutSwap() {
  const app     = document.getElementById('app');
  const swapped = app.classList.toggle('layout-swapped');
  localStorage.setItem('layout-swapped', swapped);
  const btn = document.getElementById('layout-swap-btn');
  if (btn) btn.title = swapped ? 'Swap back to default layout' : 'Swap left/right layout';
}

document.addEventListener('DOMContentLoaded', init);
window.addEventListener('resize', syncTopoSize);
