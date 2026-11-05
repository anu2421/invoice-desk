/* Invoice Desk UI: no build step, no framework. */
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inrFmt = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2, minimumFractionDigits: 2 });
const inr = (n) => (n === null || n === undefined || n === "" ? "—" : inrFmt.format(n));
const inrShort = (n) => {
  if (n == null) return "—";
  if (n >= 1e7) return "₹" + (n / 1e7).toFixed(2) + " Cr";
  if (n >= 1e5) return "₹" + (n / 1e5).toFixed(2) + " L";
  return inrFmt.format(n).replace(".00", "");
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fdate = (iso) => { if (!iso) return "—"; const [y, m, d] = iso.slice(0, 10).split("-"); return `${+d} ${MONTHS[+m - 1]} ${y}`; };
const ftime = (iso) => { if (!iso) return ""; const d = new Date(iso); return d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };
const DECISION = { approved: "Approved", review: "On hold", rejected: "Rejected", running: "Processing" };
const STAMP = { approved: ["APPROVED", "for payment"], review: ["ON HOLD", "needs review"], rejected: ["RETURNED", "to vendor"] };
const FIELD_LABELS = {
  document_title: "Document type", vendor_name: "Vendor", vendor_gstin: "Vendor GSTIN", buyer_gstin: "Billed-to GSTIN",
  invoice_number: "Invoice number", invoice_date: "Invoice date", po_number: "PO number", subtotal: "Taxable value",
  total: "Total payable", bank_account: "Bank account", ifsc: "IFSC",
};
const EFFECT_ICON = { pass: "✓", info: "i", review: "!", reject: "×" };

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; t.setAttribute("role", "status");
  document.body.appendChild(t); setTimeout(() => t.remove(), 2600);
}
let timers = [];
function clearTimers() { timers.forEach(clearTimeout); timers = []; }

/* ------------------------------------------------------------------ router */
async function route() {
  clearTimers();
  const [, page, id] = (location.hash || "#/new").split("/");
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === (page === "run" ? "runs" : page)));
  try {
    if (page === "run" && id) await renderRun(id);
    else if (page === "runs") await renderRuns();
    else if (page === "ledger") await renderLedger();
    else if (page === "rules") await renderRules();
    else await renderNew();
  } catch (e) {
    view.innerHTML = `<div class="panel empty"><h2>Couldn't load this page</h2><p>${esc(e.message)}</p><a class="btn" href="#/new">Back to processing</a></div>`;
  }
}
window.addEventListener("hashchange", route);

async function loadReader() {
  try {
    const h = await api("/api/health");
    const el = $("#reader");
    el.innerHTML = h.extractor === "rules"
      ? `<span class="dot off"></span>Reading with <b>rules parser</b> (no LLM key set)`
      : `<span class="dot"></span>Reading with <b>${esc(h.model)}</b>, cross-checked by parser`;
  } catch { /* header chip is optional */ }
}

/* ------------------------------------------------------------------ process invoice */
async function renderNew() {
  const samples = await api("/api/samples");
  view.innerHTML = `
    <div class="page-head">
      <div><h1>Process an invoice</h1>
      <p class="muted">Drop in a vendor invoice. It's read, checked against the vendor master, the PO and the ledger, then approved, held for review or returned to the vendor, with the reasoning shown at every step.</p></div>
    </div>
    <div class="intake">
      <label class="drop" id="drop" tabindex="0">
        <span class="sheet-icon" aria-hidden="true"></span>
        <h2>Drop an invoice here</h2>
        <p class="muted small">PDF, scanned PDF, or a photo (PNG/JPG). Up to 15 MB.</p>
        <span class="btn primary">Choose file</span>
        <input type="file" id="file" accept=".pdf,.png,.jpg,.jpeg" hidden>
      </label>
      <section class="tray" aria-labelledby="tray-h">
        <div class="tray-head">
          <div><h2 id="tray-h">Test invoices</h2><p class="muted small">Eleven realistic invoices from five vendors, each built to test one situation.</p></div>
          <button class="btn" id="batch">Run all ${samples.length}</button>
        </div>
        <ul class="tray-list">
          ${samples.map((s, i) => `<li><button class="sample" data-file="${esc(s.file)}">
              <span class="n">${String(i + 1).padStart(2, "0")}</span>
              <span><span class="t">${esc(s.scenario)}</span><br><span class="s">${esc(s.story)}</span></span>
              <span class="go">Run</span></button></li>`).join("")}
        </ul>
      </section>
    </div>`;
  const drop = $("#drop"), input = $("#file");
  const send = async (file) => {
    const fd = new FormData(); fd.append("file", file);
    try { const r = await api("/api/runs", { method: "POST", body: fd }); location.hash = `#/run/${r.id}`; }
    catch (e) { toast(e.message); }
  };
  input.addEventListener("change", () => input.files[0] && send(input.files[0]));
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && send(e.dataTransfer.files[0]));
  view.querySelectorAll(".sample").forEach((b) => b.addEventListener("click", async () => {
    try {
      const r = await api("/api/runs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sample: b.dataset.file }) });
      location.hash = `#/run/${r.id}`;
    } catch (e) { toast(e.message); }
  }));
  $("#batch").addEventListener("click", async () => {
    try { await api("/api/runs/batch", { method: "POST" }); toast("Running all test invoices in order"); location.hash = "#/runs"; }
    catch (e) { toast(e.message); }
  });
}

/* ------------------------------------------------------------------ run view */
async function renderRun(id) {
  let run = await api(`/api/runs/${id}`);
  let pinned = null, landed = run.status !== "running", lastKey = "";
  view.innerHTML = `
    <div class="run-head"><div id="rh"></div><div id="stamp"></div></div>
    <div class="run-grid">
      <ol class="rail" id="rail" aria-label="Stages"></ol>
      <div id="center" aria-live="polite"></div>
      <aside class="doc panel"><div class="panel-head"><h3>The invoice</h3><a class="small" href="/api/runs/${esc(id)}/file" target="_blank" rel="noopener">Open PDF</a></div>
        <div class="page-img" id="pageimg"><img alt="Page 1 of the uploaded invoice" src="/api/runs/${esc(id)}/page/0.png" onerror="this.parentNode.innerHTML='<p class=&quot;muted small&quot; style=&quot;padding:20px&quot;>Preview not available for this file.</p>'"></div></aside>
    </div>`;

  const draw = () => {
    const stages = run.stages || [];
    const active = stages.find((s) => s.status === "running");
    const done = run.status === "done";
    const lastDone = [...stages].reverse().find((s) => !["pending", "skipped"].includes(s.status));
    const sel = pinned || (done ? null : (active || lastDone || stages[0]).key);
    const idx = stages.findIndex((s) => s.status === "running");

    $("#rh").innerHTML = `
      <h1>${esc(run.vendor_name || run.filename)}</h1>
      <div class="meta">
        <span>${esc(run.filename)}</span><span>Run ${esc(run.id)}</span><span>${ftime(run.created_at)}</span>
        ${run.invoice_number ? `<span>Invoice ${esc(run.invoice_number)}</span>` : ""}
        ${run.total ? `<span>${inr(run.total)}</span>` : ""}
        ${run.extractor ? `<span>Read by ${esc(run.extractor)}</span>` : ""}
        ${done && run.duration_ms != null ? `<span>${run.duration_ms >= 1000 ? (run.duration_ms / 1000).toFixed(1) + " s" : run.duration_ms + " ms"} processing time</span>` : ""}
      </div>`;

    const st = $("#stamp");
    if (!done) {
      st.innerHTML = `<div class="waiting"><span class="spinner"></span>Stage ${idx + 1 || 1} of ${stages.length}: ${esc(active ? active.name : "starting")}</div>`;
    } else if (!st.querySelector(".stamp") || st.dataset.d !== run.final_decision) {
      const [a, b] = STAMP[run.final_decision] || ["DONE", ""];
      const overridden = run.review ? `<small>by reviewer</small>` : `<small>${b}</small>`;
      st.innerHTML = `<div class="stamp ${run.final_decision} ${landed ? "" : "land"}" role="img" aria-label="${DECISION[run.final_decision]}">${a}${overridden}</div>`;
      st.dataset.d = run.final_decision; landed = true;
    }

    $("#rail").innerHTML = stages.map((s, i) => `
      <li class="${s.status} ${s.key === sel ? "selected" : ""}">
        <button class="step" data-k="${s.key}" aria-current="${s.key === sel}">
          <span class="step-dot">${s.status === "flag" ? "!" : s.status === "block" || s.status === "error" ? "×" : i + 1}</span>
          <span><span class="step-name">${esc(s.name)}<span class="step-time">${s.duration_ms != null ? (s.compute_ms >= 1000 ? (s.compute_ms / 1000).toFixed(1) + " s" : Math.round(s.compute_ms) + " ms") : ""}</span></span>
          <span class="step-sum">${esc(s.summary || (s.status === "pending" ? s.purpose : ""))}</span></span>
        </button></li>`).join("");
    $("#rail").querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { pinned = b.dataset.k; draw(); }));

    const key = `${run.status}|${run.final_decision}|${sel}|${stages.map((s) => s.status).join("")}`;
    if (key !== lastKey) {
      lastKey = key;
      const s = stages.find((x) => x.key === sel);
      $("#center").innerHTML = (done ? decisionPanel(run) : "") + (s ? stagePanel(s, run) : "");
      bindDecision(run, (r) => { run = r; draw(); });
      $("#center").querySelectorAll("[data-goto]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); pinned = a.dataset.goto; draw(); }));
    }
  };
  draw();
  const poll = async () => {
    if (!location.hash.includes(id)) return;
    try { run = await api(`/api/runs/${id}`); draw(); } catch { /* transient */ }
    if (run.status === "running") timers.push(setTimeout(poll, 300));
  };
  if (run.status === "running") timers.push(setTimeout(poll, 250));
}

function findingsList(list) {
  if (!list || !list.length) return "";
  return `<ul class="findings">${list.map((x) => `
    <li class="finding ${x.effect}"><span class="ic" aria-label="${x.effect}">${EFFECT_ICON[x.effect]}</span>
      <div><div class="ft">${esc(x.title)}</div>${x.detail ? `<div class="fd">${esc(x.detail)}</div>` : ""}
      ${x.owner && x.effect !== "pass" ? `<div class="fo">Owner: ${esc(x.owner)}${x.priority === "high" ? " · high priority" : ""}</div>` : ""}</div></li>`).join("")}</ul>`;
}

function conf(v) {
  if (v == null) return "";
  const low = v < 0.8;
  return `<span class="conf ${low ? "low" : ""}"><i><b style="width:${Math.round(v * 100)}%"></b></i>${Math.round(v * 100)}%</span>`;
}

function stagePanel(s, run) {
  const d = s.details || {};
  let body = "";
  if (s.key === "receive") {
    body = `<dl class="kv"><dt>File</dt><dd>${esc(d.filename)}</dd><dt>Size</dt><dd>${esc(d.size_kb)} KB</dd><dt>Fingerprint</dt><dd>SHA-256 ${esc(d.sha256)}</dd>
      ${d.converted ? `<dt>Converted</dt><dd>${esc(d.converted)}</dd>` : ""}</dl>
      <p class="small faint" style="margin-top:12px">The fingerprint catches the exact same file being sent twice, before anything is read.</p>`;
  } else if (s.key === "read") {
    body = `<dl class="kv"><dt>Method</dt><dd>${esc(d.method)}</dd><dt>Pages</dt><dd>${esc(d.pages)}</dd><dt>Characters</dt><dd>${esc(d.characters)}</dd>
      ${d.ocr_confidence != null ? `<dt>OCR confidence</dt><dd>${Math.round(d.ocr_confidence * 100)}% mean word confidence</dd>` : ""}</dl>
      ${d.text_preview ? `<details style="margin-top:12px"><summary class="small">Show the raw text it read</summary><pre class="text">${esc(d.text_preview)}</pre></details>` : ""}`;
  } else if (s.key === "extract" && d.fields) {
    const c = d.confidence || {}, rep = d.repaired || [];
    body = `<div class="wrap-x"><table class="t"><thead><tr><th>Field</th><th>Value read</th><th>Confidence</th></tr></thead><tbody>
      ${Object.entries(FIELD_LABELS).map(([k, label]) => {
        const v = d.fields[k];
        const shown = v == null || v === "" ? `<span class="missing">Not found</span>` : esc(k === "subtotal" || k === "total" ? inr(v) : k === "invoice_date" ? fdate(v) : v);
        return `<tr><td class="muted">${label}</td><td>${shown}${rep.includes(k) ? `<span class="fixed">OCR fixed</span><div class="small faint">OCR read “${esc((d.repairs || {})[k] || "")}”, corrected using the GSTIN format and check digit</div>` : ""}</td><td>${v == null || v === "" ? "" : conf(c[k])}</td></tr>`;
      }).join("")}</tbody></table></div>
      <h3 style="margin:18px 0 6px">Line items</h3>
      <div class="wrap-x"><table class="t"><thead><tr><th>Description</th><th>HSN</th><th class="num">Qty</th><th class="num">Rate</th><th class="num">Amount</th></tr></thead><tbody>
      ${(d.line_items || []).map((l) => `<tr><td>${esc(l.description)}</td><td>${esc(l.hsn || "")}</td><td class="num">${esc(l.qty)}</td><td class="num">${inr(l.unit_price)}</td><td class="num">${inr(l.amount)}</td></tr>`).join("") || `<tr><td colspan="5" class="missing">No line items found</td></tr>`}
      ${(d.taxes || []).map((t) => `<tr><td colspan="4" class="muted">${esc(t.type)} @ ${esc(t.rate)}%</td><td class="num">${inr(t.amount)}</td></tr>`).join("")}
      </tbody></table></div>
      ${d.cross_check ? `<p class="small" style="margin-top:12px"><b>Cross-check:</b> ${typeof d.cross_check === "string" ? esc(d.cross_check) : "disagreements found (see below)"}</p>` : ""}`;
  } else if (s.key === "vendor" && d.vendor) {
    const v = d.vendor;
    body = `<dl class="kv"><dt>Vendor master</dt><dd>${esc(v.vendor_id)} · ${esc(v.name)}</dd><dt>GSTIN on file</dt><dd>${esc(v.gstin)}</dd>
      <dt>Status</dt><dd>${esc(v.status)}</dd><dt>Verified bank</dt><dd>${esc(v.bank_name)} …${esc(v.bank_account.slice(-4))} · ${esc(v.ifsc)}</dd><dt>Payment terms</dt><dd>Net ${esc(v.payment_terms_days)} days</dd></dl>`;
  } else if (s.key === "po" && d.po) {
    body = poDetail(d, s.findings);
  } else if (s.key === "po") {
    const rank = (s.findings.find((x) => x.evidence && x.evidence.ranking) || {}).evidence;
    if (rank) body = rankingTable(rank.ranking);
  }
  const ms = s.compute_ms != null ? (s.compute_ms >= 1000 ? `${(s.compute_ms / 1000).toFixed(1)} s` : `${Math.round(s.compute_ms)} ms`) : "";
  return `<section class="panel">
    <div class="panel-head"><div><h2>${esc(s.name)}</h2><p class="purpose">${esc(s.purpose)}</p></div><span class="faint small">${ms}</span></div>
    <div class="panel-body">
      ${s.status === "pending" ? `<p class="muted">Waiting for earlier stages.</p>` : s.status === "running" ? `<p class="waiting"><span class="spinner"></span>Working on it…</p>` : `<p style="margin-bottom:${body || (s.findings || []).length ? 14 : 0}px">${esc(s.summary)}</p>`}
      ${body}
      ${(s.findings || []).length ? `<div style="margin-top:${body ? 16 : 0}px">${findingsList(s.findings)}</div>` : ""}
    </div></section>`;
}

function rankingTable(ranking) {
  return `<h3 style="margin:4px 0 6px">Open POs compared</h3><div class="wrap-x"><table class="t"><thead><tr><th>PO</th><th class="num">Line match</th><th class="num">Amount fit</th><th class="num">Score</th></tr></thead><tbody>
    ${ranking.map((r, i) => `<tr><td>${i === 0 ? "<b>" : ""}${esc(r.po)}${i === 0 ? "</b>" : ""}</td><td class="num">${Math.round((r.description_match || 0) * 100)}%</td><td class="num">${Math.round((r.amount_fit || 0) * 100)}%</td><td class="num">${Math.round(r.score * 100)}%</td></tr>`).join("")}
    </tbody></table></div>`;
}

function poDetail(d, findings) {
  const p = d.po, c = d.consumption;
  const max = Math.max(c.after, c.po_value);
  const pct = (x) => (x / max) * 100;
  const over = Math.max(0, c.after - c.po_value);
  const nowInside = c.this_invoice - over;
  const inferred = (findings.find((x) => x.code === "PO_INFERRED") || {}).evidence;
  return `
    ${inferred ? rankingTable(inferred.ranking) + `<div style="height:16px"></div>` : ""}
    <dl class="kv"><dt>Purchase order</dt><dd>${esc(p.po_number)}${p.inferred ? " (inferred)" : ""} · ${esc(p.description)}</dd><dt>Raised</dt><dd>${fdate(p.po_date)} · ${esc(p.status)}</dd><dt>PO value</dt><dd>${inr(p.po_value ?? p.total_value)}</dd></dl>
    <h3 style="margin:18px 0 0">How much of the PO is used</h3>
    <div class="bar-wrap"><div class="bar" role="img" aria-label="${c.pct_after}% of PO used after this invoice">
      <span class="before" style="width:${pct(c.billed_before)}%"></span><span class="now" style="width:${pct(Math.max(nowInside, 0))}%"></span><span class="over" style="width:${pct(over)}%"></span></div>
      ${over ? `<span class="bar-limit" style="left:${pct(c.po_value)}%"></span>` : ""}</div>
    <div class="legend"><span><i style="background:var(--ink-3)"></i>Billed before ${inr(c.billed_before)}</span><span><i style="background:var(--pen)"></i>This invoice ${inr(c.this_invoice)}</span>
      ${over ? `<span><i style="background:var(--stop)"></i>Over the PO by ${inr(over)}</span>` : `<span><i style="background:var(--rule-soft)"></i>Remaining ${inr(c.po_value - c.after)}</span>`}</div>
    ${c.prior_invoices.length ? `<p class="small muted" style="margin-top:8px">Earlier invoices on this PO: ${c.prior_invoices.map((x) => `${esc(x.invoice_number)} (${inrShort(x.subtotal)})`).join(", ")}</p>` : ""}
    <h3 style="margin:18px 0 6px">Line by line</h3>
    <div class="wrap-x"><table class="t"><thead><tr><th>Invoice line</th><th>PO line</th><th class="num">Qty inv / PO</th><th class="num">Rate inv / PO</th><th class="num">Price vs PO</th></tr></thead><tbody>
    ${d.lines.map((r) => `<tr><td>${esc(r.invoice.description)}</td><td class="muted">${r.po ? esc(r.po.description) : `<span class="missing">Not on PO</span>`}</td>
      <td class="num">${esc(r.invoice.qty)} / ${r.po ? esc(r.po.qty) : "—"}</td><td class="num">${inr(r.invoice.unit_price)} / ${r.po ? inr(r.po.unit_price) : "—"}</td>
      <td class="num" style="color:${r.price_var_pct > 2 ? "var(--stop)" : r.price_var_pct ? "var(--hold)" : "inherit"};font-weight:${r.price_var_pct > 2 ? 700 : 400}">${r.po ? (r.price_var_pct > 0 ? "+" : "") + r.price_var_pct.toFixed(1) + "%" : ""}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function emailBlock(e, label) {
  const mailto = `mailto:${encodeURIComponent(e.to.includes("@") ? e.to : "")}?subject=${encodeURIComponent(e.subject)}&body=${encodeURIComponent(e.body)}`;
  return `<div class="email"><div class="email-head"><span class="faint small">${esc(label)}</span><span><b>To</b>${esc(e.to)}</span><span><b>Subject</b>${esc(e.subject)}</span></div>
    <div class="email-body">${esc(e.body)}</div>
    <div class="email-foot"><button class="btn ghost" data-copy="${esc(e.subject + "\n\n" + e.body)}">Copy email</button><a class="btn" href="${mailto}">Open in mail app</a></div></div>`;
}

function decisionPanel(run) {
  const o = run.output || {};
  const reasons = (run.findings || []).filter((x) => x.effect === "review" || x.effect === "reject");
  const order = { reject: 0, review: 1 }, pr = { high: 0, low: 2 };
  reasons.sort((a, b) => order[a.effect] - order[b.effect] || (pr[a.priority] ?? 1) - (pr[b.priority] ?? 1));
  const stageName = (k) => (run.stages.find((s) => s.key === k) || {}).name || k;
  let out = "";
  if (o.kind === "posting") {
    const p = o.posting;
    out = `<h3 style="margin-top:16px">Posting record</h3><dl class="kv" style="margin-top:8px">
      <dt>Vendor</dt><dd>${esc(p.vendor)}</dd><dt>Invoice</dt><dd>${esc(p.invoice_number)} · ${fdate(p.invoice_date)}</dd><dt>PO</dt><dd>${esc(p.po_number)}</dd>
      <dt>Taxable value</dt><dd>${inr(p.taxable_value)}</dd><dt>GST (input credit)</dt><dd>${inr(p.gst)}</dd><dt>Total payable</dt><dd><b>${inr(p.total_payable)}</b></dd>
      <dt>Due</dt><dd>${fdate(p.due_date)} (${esc(p.payment_terms)})</dd><dt>Pay to</dt><dd>${esc(p.pay_to)}</dd></dl>
      ${o.notes && o.notes.length ? `<p class="small muted" style="margin-top:12px">Noted, no action needed: ${o.notes.map(esc).join("; ")}</p>` : ""}`;
  } else if (o.kind === "review") {
    out = `${o.warning ? `<div class="warn">${esc(o.warning)}</div>` : ""}
      ${o.vendor_email ? emailBlock(o.vendor_email, "Draft query to vendor, send after review") : ""}`;
  } else if (o.kind === "returned" && o.vendor_email) {
    out = emailBlock(o.vendor_email, "Ready to send");
  }
  let reviewUi = "";
  if (run.review) {
    reviewUi = `<div class="reviewed"><b>${run.review.action === "approve" ? "Approved" : "Rejected"} by ${esc(run.review.by)}</b> · ${ftime(run.review.at)}${run.review.note ? `<br>${esc(run.review.note)}` : ""}</div>`;
  } else if (run.final_decision === "review") {
    reviewUi = `<div class="review-box"><label for="note"><h3>Your decision</h3></label>
      <p class="small muted" style="margin:4px 0 8px">Approving posts it to the ledger and schedules payment. Your note is kept in the audit trail.</p>
      <textarea id="note" placeholder="What did you check? e.g. Called vendor on file number, bank change confirmed"></textarea>
      <div class="row"><button class="btn ok" data-act="approve">Approve and post</button><button class="btn stop" data-act="reject">Reject</button></div></div>`;
  }
  return `<section class="panel decision ${run.final_decision}">
    <div class="panel-body">
      <p class="headline">${esc(o.headline || DECISION[run.final_decision])}</p>
      <p class="next">${esc(o.next || "")}${o.queue ? ` Priority: ${esc(o.priority || "normal")}.` : ""}</p>
      ${reasons.length ? `<ol class="reason-list">${reasons.map((x) => `<li><b>${esc(x.title)}</b> <a href="#" data-goto="${x.stage}" class="small">${esc(stageName(x.stage))}</a>${x.detail ? `<div class="small muted">${esc(x.detail)}</div>` : ""}</li>`).join("")}</ol>` : ""}
      ${out}${reviewUi}
    </div></section>`;
}

function bindDecision(run, onUpdate) {
  $("#center").querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Email copied"); } catch { toast("Couldn't copy; select the text instead"); }
  }));
  $("#center").querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
    const note = ($("#note") || {}).value || "";
    try {
      const r = await api(`/api/runs/${run.id}/review`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: b.dataset.act, note }) });
      toast(b.dataset.act === "approve" ? "Approved and posted" : "Rejected"); onUpdate(r);
    } catch (e) { toast(e.message); }
  }));
}

/* ------------------------------------------------------------------ runs dashboard */
async function renderRuns() {
  let filter = "all", confirmReset = false;
  const draw = async () => {
    const [runs, st] = await Promise.all([api("/api/runs"), api("/api/stats")]);
    const anyRunning = runs.some((r) => r.status === "running");
    if (!runs.length) {
      view.innerHTML = `<div class="page-head"><div><h1>Runs</h1></div></div>
        <div class="panel empty"><h2>No invoices processed yet</h2><p>Process one invoice, or run the full test set to fill this page.</p>
        <a class="btn primary" href="#/new">Process an invoice</a> <button class="btn" id="batch">Run all test invoices</button></div>`;
      $("#batch").addEventListener("click", async () => { try { await api("/api/runs/batch", { method: "POST" }); draw(); } catch (e) { toast(e.message); } });
      return;
    }
    const rows = runs.filter((r) => filter === "all" || (r.status === "running" ? filter === "running" : r.final_decision === filter));
    const maxExc = Math.max(1, ...st.exceptions.map((e) => e[1]));
    const fin = st.final || {};
    view.innerHTML = `
      <div class="page-head"><div><h1>Runs</h1><p class="muted">Every invoice processed, what was decided and why. Select a row to open its full trace.</p></div>
        <div style="display:flex;gap:8px"><button class="btn" id="batch">Run all test invoices</button><button class="btn ghost" id="reset">Reset demo data</button></div></div>
      <div class="figures">
        <div class="fig"><div class="v">${st.runs}</div><div class="l">Invoices processed</div><div class="sub">${inrShort(st.value_processed)} total value</div></div>
        <div class="fig"><div class="v">${st.touchless_rate == null ? "—" : Math.round(st.touchless_rate * 100) + "%"}</div><div class="l">Approved with no one touching it</div><div class="sub">${st.auto_approved} of ${st.runs}</div></div>
        <div class="fig"><div class="v" style="color:var(--hold)">${st.in_queue}</div><div class="l">Waiting for review</div><div class="sub">${inrShort(st.value_held)} on hold</div></div>
        <div class="fig"><div class="v" style="color:var(--stop)">${fin.rejected || 0}</div><div class="l">Rejected or returned</div><div class="sub">${fin.approved || 0} approved in total</div></div>
        <div class="fig"><div class="v">${st.avg_seconds ?? "—"} s</div><div class="l">Average processing time</div><div class="sub">per invoice, upload to decision</div></div>
        <div class="fig"><div class="v">${(st.minutes_saved / 60).toFixed(1)} h</div><div class="l">AP time saved, estimated</div><div class="sub">vs about 12 min by hand each</div></div>
      </div>
      <div class="dash">
        <section class="panel">
          <div class="panel-head"><div class="filters" role="group" aria-label="Filter runs">
            ${[["all", "All"], ["approved", "Approved"], ["review", "On hold"], ["rejected", "Rejected"]].concat(anyRunning ? [["running", "Processing"]] : [])
              .map(([k, l]) => `<button data-f="${k}" class="${filter === k ? "on" : ""}">${l}</button>`).join("")}</div>
            <span class="faint small">${rows.length} shown</span></div>
          <div class="wrap-x"><table class="t"><thead><tr><th>Run</th><th>Received</th><th>Vendor</th><th>Invoice</th><th class="num">Amount</th><th>Decision</th><th>Main reason</th><th class="num">Time</th></tr></thead><tbody>
          ${rows.map((r) => {
            const d = r.status === "running" ? "running" : r.final_decision;
            return `<tr class="click" data-id="${esc(r.id)}" tabindex="0"><td class="nw"><b>${esc(r.id)}</b></td><td class="muted nw">${ftime(r.created_at)}</td>
              <td>${esc(r.vendor_name || r.filename)}</td><td>${esc(r.invoice_number || "—")}</td><td class="num">${inr(r.total)}</td>
              <td><span class="tag ${d}">${DECISION[d]}</span>${r.review ? `<span class="overridden">by reviewer, was ${DECISION[r.decision].toLowerCase()}</span>` : ""}</td>
              <td class="exc">${esc(r.exceptions[0] || (r.status === "running" ? "" : "All checks passed"))}${r.exceptions.length > 1 ? ` <span class="faint">+${r.exceptions.length - 1}</span>` : ""}</td>
              <td class="num muted nw">${r.duration_ms != null ? (r.duration_ms >= 1000 ? (r.duration_ms / 1000).toFixed(1) + " s" : r.duration_ms + " ms") : ""}</td></tr>`;
          }).join("") || `<tr><td colspan="8" class="muted">No runs match this filter.</td></tr>`}
          </tbody></table></div>
        </section>
        <aside>
          <section class="panel"><div class="panel-head"><h2>Why invoices stopped</h2></div><div class="panel-body">
            ${st.exceptions.length ? `<ul class="hbars">${st.exceptions.map(([k, n]) => `<li><div class="lbl"><span>${esc(k)}</span><b>${n}</b></div><div class="track"><b style="width:${(n / maxExc) * 100}%"></b></div></li>`).join("")}</ul>` : `<p class="muted">Nothing has been stopped yet.</p>`}
          </div></section>
          <section class="panel"><div class="panel-body">
            <div class="fig" style="padding:0"><div class="v">${inrShort(st.value_stopped)}</div><div class="l">Stopped before payment</div><div class="sub">invoice value that was held or returned instead of paid automatically</div></div>
          </div></section>
        </aside>
      </div>`;
    view.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => { filter = b.dataset.f; draw(); }));
    view.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => (location.hash = `#/run/${tr.dataset.id}`));
      tr.addEventListener("keydown", (e) => e.key === "Enter" && (location.hash = `#/run/${tr.dataset.id}`));
    });
    $("#batch").addEventListener("click", async () => { try { await api("/api/runs/batch", { method: "POST" }); toast("Running all test invoices"); draw(); } catch (e) { toast(e.message); } });
    const rb = $("#reset");
    if (confirmReset) { rb.textContent = "Click again to wipe runs and ledger"; rb.classList.add("stop"); }
    rb.addEventListener("click", async () => {
      if (!confirmReset) { confirmReset = true; draw(); timers.push(setTimeout(() => { confirmReset = false; }, 4000)); return; }
      confirmReset = false; await api("/api/reset", { method: "POST" }); toast("Demo data reset"); draw();
    });
    if (anyRunning && location.hash.startsWith("#/runs")) timers.push(setTimeout(draw, 1200));
  };
  await draw();
}

/* ------------------------------------------------------------------ POs and ledger */
async function renderLedger() {
  const d = await api("/api/reference");
  const vname = Object.fromEntries(d.vendors.map((v) => [v.vendor_id, v.name]));
  view.innerHTML = `
    <div class="page-head"><div><h1>POs and ledger</h1><p class="muted">The data each invoice is checked against. Approved invoices are added to the ledger; held ones sit there as on hold until someone decides.</p></div></div>
    <section class="panel"><div class="panel-head"><h2>Purchase orders</h2><span class="faint small">Billed amount counts posted invoices only</span></div>
      <div class="wrap-x"><table class="t"><thead><tr><th>PO</th><th>Vendor</th><th>Description</th><th>Raised</th><th class="num">Value</th><th style="width:220px">Billed</th></tr></thead><tbody>
      ${d.pos.map((p) => { const pct = Math.min(100, (p.billed / p.total_value) * 100); const over = p.billed > p.total_value * 1.02;
        return `<tr><td><b>${esc(p.po_number)}</b></td><td>${esc(vname[p.vendor_id] || p.vendor_id)}</td><td class="muted">${esc(p.description)}<div class="small faint">${p.lines.map((l) => `${esc(l.description)}: ${l.qty} × ${inr(l.unit_price)}`).join(" · ")}</div></td>
        <td class="muted">${fdate(p.po_date)}</td><td class="num">${inr(p.total_value)}</td>
        <td><div class="bar" style="margin:4px 0 2px;height:8px"><span class="${over ? "over" : "before"}" style="width:${pct}%"></span></div><span class="small muted">${inrShort(p.billed)} · ${Math.round((p.billed / p.total_value) * 100)}%</span></td></tr>`; }).join("")}
      </tbody></table></div></section>
    <section class="panel"><div class="panel-head"><h2>Vendor master</h2></div>
      <div class="wrap-x"><table class="t"><thead><tr><th>ID</th><th>Vendor</th><th>GSTIN</th><th>State</th><th>Status</th><th>Verified bank account</th><th>Terms</th></tr></thead><tbody>
      ${d.vendors.map((v) => `<tr><td><b>${esc(v.vendor_id)}</b></td><td>${esc(v.name)}</td><td>${esc(v.gstin)}</td><td>${esc(v.state)}</td>
        <td><span class="tag ${v.status === "approved" ? "approved" : "rejected"}">${esc(v.status)}</span></td><td>${esc(v.bank_name)} …${esc(v.bank_account.slice(-4))}</td><td>Net ${v.payment_terms_days}</td></tr>`).join("")}
      </tbody></table></div></section>
    <section class="panel"><div class="panel-head"><h2>AP ledger</h2><span class="faint small">${d.ledger.length} entries</span></div>
      <div class="wrap-x"><table class="t"><thead><tr><th>Invoice</th><th>Vendor</th><th>Date</th><th>PO</th><th class="num">Taxable</th><th class="num">Total</th><th>Status</th><th>Source</th></tr></thead><tbody>
      ${d.ledger.map((l) => `<tr><td><b>${esc(l.invoice_number)}</b></td><td>${esc(vname[l.vendor_id] || l.vendor_id || "—")}</td><td class="muted">${fdate(l.invoice_date)}</td><td>${esc(l.po_number || "—")}</td>
        <td class="num">${inr(l.subtotal)}</td><td class="num">${inr(l.total)}</td>
        <td><span class="tag ${l.status === "posted" ? "approved" : l.status === "held" ? "review" : "rejected"}">${l.status === "held" ? "on hold" : esc(l.status)}</span></td>
        <td>${l.run_id ? `<a href="#/run/${esc(l.run_id)}">${esc(l.run_id)}</a>` : `<span class="faint">history</span>`}</td></tr>`).join("")}
      </tbody></table></div></section>`;
}

/* ------------------------------------------------------------------ rules */
async function renderRules() {
  const r = await api("/api/rules");
  const R = r.rules;
  view.innerHTML = `
    <div class="page-head"><div><h1>How it decides</h1><p class="muted">The model reads the invoice. Rules written in code make the decision, so every outcome can be traced to a specific check and the same invoice always gets the same answer.</p></div></div>
    <section class="panel"><div class="panel-head"><h2>The process</h2></div>
      <ol class="map">${r.stages.map((s) => `<li><h3>${esc(s.name)}</h3><p>${esc(s.purpose)}</p></li>`).join("")}</ol></section>
    <div class="two">
      <section class="panel"><div class="panel-head"><h2>Decision rule</h2></div><div class="panel-body policy">
        <div><span class="tag rejected">Rejected</span><span>Any check that means the invoice can't be booked as-is: proforma instead of tax invoice, missing number or date, invalid GSTIN, wrong GST type, unknown or blocked vendor, exact duplicate. A correction email is drafted for the vendor.</span></div>
        <div><span class="tag review">On hold</span><span>Anything a person should look at before money moves: price or quantity variance, PO over-billed, likely duplicate, bank details changed, PO inferred rather than quoted, low-confidence reading, the two extractors disagreeing.</span></div>
        <div><span class="tag approved">Approved</span><span>Every check passes. Small variances inside tolerance are noted but don't stop it. Posted to the ledger with a due date from the vendor's terms.</span></div>
        <p class="small muted">If any stage errors, the invoice is held, never approved. Failing safe is the default.</p>
      </div></section>
      <section class="panel"><div class="panel-head"><h2>Thresholds</h2></div><div class="panel-body">
        <table class="t"><tbody>
          <tr><td>Cumulative billing over PO value</td><td class="num"><b>${R.amount_tolerance_pct}%</b></td></tr>
          <tr><td>Unit price over PO rate</td><td class="num"><b>${R.price_tolerance_pct}%</b></td></tr>
          <tr><td>Rounding slack on arithmetic</td><td class="num"><b>${inr(R.arithmetic_tolerance_inr)}</b></td></tr>
          <tr><td>Duplicate look-back window</td><td class="num"><b>${R.duplicate_window_days} days</b></td></tr>
          <tr><td>Field confidence needed</td><td class="num"><b>${Math.round(R.min_field_confidence * 100)}%</b></td></tr>
          <tr><td>Score needed to suggest a PO</td><td class="num"><b>${Math.round(R.po_inference_min_score * 100)}%</b></td></tr>
          <tr><td>Invoice age before review</td><td class="num"><b>${R.max_invoice_age_days} days</b></td></tr>
        </tbody></table></div></section>
    </div>
    <div class="two">
      <section class="panel"><div class="panel-head"><h2>Assumptions</h2></div><div class="panel-body"><ul class="prose">
        <li>The buyer is one Indian entity (Halden Home Retail, Haryana). GST rules follow: same state means CGST + SGST, different states means IGST.</li>
        <li>Matching is two-way: invoice against PO. There's no goods receipt data, so quantity is checked against the PO, not against what arrived.</li>
        <li>Only posted invoices count toward a PO's used value. Held invoices are shown as exposure but don't block others.</li>
        <li>Recurring bills (monthly retainers) legitimately share amount and PO, so those alone only count as a duplicate signal when the dates are close.</li>
        <li>A changed bank account is never accepted from an invoice. It's verified out of band and updated in the vendor master first.</li>
        <li>On a scan, a field the OCR couldn't read isn't treated as missing from the invoice. It's held for a person to key in, not bounced back to the vendor.</li>
        <li>Time saved assumes about ${R.manual_minutes_per_invoice} minutes of manual handling per invoice, and half that for ones a person still reviews.</li>
      </ul></div></section>
      <section class="panel"><div class="panel-head"><h2>Where AI is used, and where it isn't</h2></div><div class="panel-body"><ul class="prose">
        <li><b>Reading:</b> an LLM reads the PDF (including scans) into a fixed schema with per-field confidence. A regex parser reads the same document independently.</li>
        <li><b>Cross-check:</b> if the two reads disagree on a money field, the invoice is held. Neither is trusted alone.</li>
        <li><b>Repair:</b> OCR misreads in GSTINs (O for 0, I for 1) are fixed using the GSTIN's fixed shape and its check digit, not by guessing. Text from a real text layer is never altered, so an invalid GSTIN stays invalid.</li>
        <li><b>Two OCR passes:</b> scans are read in two layout modes and any field the first pass missed is filled from the second.</li>
        <li><b>Not AI:</b> matching, tolerances, duplicate logic, GST rules and the decision itself. These need to be explainable and identical every time.</li>
        <li>Without an API key the parser runs alone, so the process still works offline.</li>
      </ul></div></section>
    </div>`;
}

loadReader();
route();
