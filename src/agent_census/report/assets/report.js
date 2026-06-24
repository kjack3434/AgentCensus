(function () {
  "use strict";

  var R = JSON.parse(document.getElementById("data").textContent);
  var agents = (R.agents || []).map(function (a, i) { a._i = i; return a; });
  var summary = R.summary || {};

  var SEV = ["critical", "high", "medium", "low", "info"];
  var SEVRANK = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };

  // ── helpers ──────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  var SRC_LABELS = {
    copilot_studio: "Copilot Studio",
    azure_ai_foundry: "Azure AI Foundry",
    vertex_ai_agent_engine: "Vertex AI Agent Engine",
    agentspace: "Agentspace",
    dialogflow_cx: "Dialogflow CX"
  };
  function srcLabel(s) { return SRC_LABELS[s] || s || "—"; }
  var CLOUD_LABELS = { microsoft: "Microsoft", google: "GCP" };
  function cloudKey(a) { return (a.provider || "microsoft").toLowerCase(); }
  function cloudLabel(a) { var k = cloudKey(a); return CLOUD_LABELS[k] || pretty(k); }
  // Where each provider's real content-safety / DLP posture actually lives (not in discovery).
  var GUARDRAILS_NOTE = {
    microsoft: "Not exposed by discovery — governed via Microsoft Purview / Azure AI Content Safety",
    google: "Not exposed by discovery — model safety applied by Vertex AI / Gemini; governed via Model Armor / Cloud DLP"
  };
  function guardrailsNote(a) { return GUARDRAILS_NOTE[cloudKey(a)] || "Not exposed by discovery"; }
  function unobservable(a) { return (a.properties && a.properties.unobservable) || []; }
  function pretty(s) {
    if (!s) return "";
    s = String(s).replace(/_/g, " ");
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  function ownerName(a) {
    var o = a.owners && a.owners[0];
    return o ? (o.name || o.email || "") : "";
  }
  function maxSev(a) {
    var r = -1, fs = a.findings || [];
    for (var i = 0; i < fs.length; i++) if (SEVRANK[fs[i].severity] > r) r = SEVRANK[fs[i].severity];
    return r;
  }
  function sevByRank(rank) {
    for (var k in SEVRANK) if (SEVRANK[k] === rank) return k;
    return null;
  }
  function datePart(s) { return s ? String(s).slice(0, 10) : ""; }

  // ── summary cards ────────────────────────────────────────────────────────
  function breakdown(dict, opts) {
    opts = opts || {};
    var wrap = el("div", "breakdown");
    var entries = Object.keys(dict || {}).map(function (k) { return [k, dict[k]]; });
    entries.sort(function (a, b) { return b[1] - a[1]; });
    if (opts.limit) entries = entries.slice(0, opts.limit);
    if (!entries.length) { wrap.appendChild(el("div", "sub", "none")); return wrap; }
    entries.forEach(function (e) {
      var row = el("div", "row");
      row.appendChild(el("span", "name", opts.fmt ? opts.fmt(e[0]) : e[0]));
      row.appendChild(el("span", "val", String(e[1])));
      wrap.appendChild(row);
    });
    return wrap;
  }

  function card(label) {
    var c = el("div", "card");
    c.appendChild(el("div", "label", label));
    return c;
  }

  function buildCards() {
    var host = document.getElementById("cards");
    var bySrc = summary.by_source || {};

    // Agents
    var c1 = card("Agents");
    c1.appendChild(el("div", "big", String(summary.total_agents || 0)));
    var srcSub = Object.keys(bySrc).map(function (k) { return bySrc[k] + " " + srcLabel(k); }).join("  ·  ");
    c1.appendChild(el("div", "sub", srcSub || "—"));
    host.appendChild(c1);

    // Findings + severity bar
    var c2 = card("Findings");
    c2.appendChild(el("div", "big", String(summary.total_findings || 0)));
    var sevd = summary.findings_by_severity || {};
    var totalSev = SEV.reduce(function (n, s) { return n + (sevd[s] || 0); }, 0);
    var bar = el("div", "sevbar");
    SEV.forEach(function (s) {
      var n = sevd[s] || 0;
      if (!n) return;
      var seg = el("span", "sev-" + s);
      seg.style.width = (100 * n / totalSev) + "%";
      seg.title = n + " " + s;
      bar.appendChild(seg);
    });
    c2.appendChild(bar);
    var legend = el("div", "sevlegend");
    SEV.forEach(function (s) {
      var item = el("div", "item");
      item.appendChild(el("span", "dot sev-" + s));
      item.appendChild(el("span", null, pretty(s) + " " + (sevd[s] || 0)));
      legend.appendChild(item);
    });
    c2.appendChild(legend);
    host.appendChild(c2);

    // Top models
    var c3 = card("Top models");
    c3.appendChild(breakdown(summary.by_model, { limit: 5, fmt: function (m) { return m || "unknown"; } }));
    host.appendChild(c3);

    // Categories
    var c4 = card("Categories");
    c4.appendChild(breakdown(summary.by_category, { fmt: pretty }));
    host.appendChild(c4);
  }

  // ── filters ──────────────────────────────────────────────────────────────
  function fillSelect(id, label, values, fmt) {
    var sel = document.getElementById(id);
    sel.appendChild(new Option(label, ""));
    values.forEach(function (v) { sel.appendChild(new Option(fmt ? fmt(v) : v, v)); });
  }

  function buildFilters() {
    var srcs = Object.keys(summary.by_source || {});
    fillSelect("f-source", "All sources", srcs, srcLabel);
    var models = Object.keys(summary.by_model || {}).sort();
    fillSelect("f-model", "All models", models, function (m) { return m || "unknown"; });
    fillSelect("f-sev", "Any severity", SEV, function (s) { return pretty(s) + "+"; });
  }

  // ── detail panel ───────────────────────────────────────────────────────────
  function fieldList(title, items, render) {
    var f = el("div", "field");
    f.appendChild(el("h4", null, title));
    if (!items || !items.length) { f.appendChild(el("div", "muted", "none")); return f; }
    var ul = el("ul", "list");
    items.forEach(function (it) { ul.appendChild(render(it)); });
    f.appendChild(ul);
    return f;
  }

  function tagRow(tags) {
    var kv = el("div", "kv");
    tags.forEach(function (t) { if (t) kv.appendChild(el("span", "tag", t)); });
    return kv;
  }

  function buildDetail(a) {
    var inner = el("div", "detail-inner");

    // Instructions (full width)
    var instr = el("div", "field full");
    instr.appendChild(el("h4", null, "Instructions"));
    if (a.instructions && a.instructions.trim()) {
      instr.appendChild(el("div", "pre", a.instructions));
    } else if (unobservable(a).indexOf("instructions") !== -1) {
      instr.appendChild(el("div", "muted", "Not exposed by discovery — defined in the deployed code"));
    } else {
      instr.appendChild(el("div", "muted", "empty"));
    }
    inner.appendChild(instr);

    // Discovery coverage — what the API genuinely can't see for this agent.
    var uo = unobservable(a);
    if (uo.length) {
      var cov = el("div", "field full");
      cov.appendChild(el("h4", null, "Discovery coverage"));
      cov.appendChild(el("div", "muted",
        "Not exposed by the discovery API for this agent: " + uo.join(", ") +
        ". These live inside the deployed code and can't be read remotely, so they aren't scored."));
      inner.appendChild(cov);
    }

    // Behavior provenance — when model/instructions/tools were grafted from a
    // different underlying agent (e.g. a Gemini agent backed by an Agent Engine).
    var bsrc = a.properties && a.properties.behavior_source;
    if (bsrc) {
      var bs = el("div", "field full");
      bs.appendChild(el("h4", null, "Behavior source"));
      var ref = bsrc.name || bsrc.lowcode_agent || bsrc.engine_id || "underlying agent";
      var via = bsrc.kind === "agent_engine" ? "Agent Engine" : "underlying agent";
      bs.appendChild(el("div", "muted",
        "Model, instructions, and tools are referenced from the " + via + ": " + ref + "."));
      inner.appendChild(bs);
    }

    // Tools
    inner.appendChild(fieldList("Tools", a.tools, function (t) {
      var li = el("li");
      li.appendChild(el("span", "lead", t.name));
      var bits = [pretty(t.tool_type)];
      if (t.write_capable) bits.push("write");
      if (t.requires_approval && t.requires_approval !== "unknown") bits.push("approval: " + t.requires_approval);
      li.appendChild(document.createTextNode(" — " + bits.join(", ")));
      if (t.server_url) { li.appendChild(document.createElement("br")); li.appendChild(el("code", null, t.server_url)); }
      return li;
    }));

    // Knowledge
    inner.appendChild(fieldList("Knowledge", a.knowledge, function (k) {
      var li = el("li");
      li.appendChild(el("span", "lead", k.name));
      var extra = [pretty(k.kind)];
      if (k.assignment === "app") extra.push("app-level (shared)");
      if (k.external_source) extra.push("external: " + k.external_source);
      if (k.scope) extra.push(k.scope);
      if (k.index_name) extra.push("index: " + k.index_name);
      li.appendChild(document.createTextNode(" — " + extra.join(", ")));
      return li;
    }));

    // Guardrails — absence is NOT scored: content safety is on by default and its
    // real posture is only visible in each provider's governance plane (not discovery).
    var gf = el("div", "field");
    gf.appendChild(el("h4", null, "Guardrails"));
    if (a.guardrails && a.guardrails.length) {
      var gul = el("ul", "list");
      a.guardrails.forEach(function (g) {
        var li = el("li");
        li.appendChild(el("span", "lead", pretty(g.kind)));
        var extra = [];
        if (g.level) extra.push("level: " + g.level);
        if (g.source) extra.push(g.source);
        if (extra.length) li.appendChild(document.createTextNode(" — " + extra.join(", ")));
        gul.appendChild(li);
      });
      gf.appendChild(gul);
    } else {
      gf.appendChild(el("div", "muted", guardrailsNote(a)));
    }
    inner.appendChild(gf);

    // Owners
    inner.appendChild(fieldList("Owners", a.owners, function (o) {
      var li = el("li");
      li.appendChild(el("span", "lead", o.name || o.email || "—"));
      if (o.name && o.email) li.appendChild(document.createTextNode(" <" + o.email + ">"));
      return li;
    }));

    // Posture
    var posture = el("div", "field");
    posture.appendChild(el("h4", null, "Posture"));
    var tags = ["status: " + (a.status || "?"), "kind: " + pretty(a.kind)];
    if (a.category) tags.push("category: " + pretty(a.category));
    if (a.version) tags.push("v" + a.version);
    if (a.autonomous) tags.push("autonomous");
    if (a.shared_with_everyone) tags.push("shared with everyone");
    if (a.no_auth_required) tags.push("no auth");
    if (a.multi_tenant) tags.push("multi-tenant");
    if (a.model_tier && a.model_tier !== "standard") tags.push("model tier: " + a.model_tier);
    if (a.properties && a.properties.behavior_source) tags.push("behavior: referenced");
    posture.appendChild(tagRow(tags));
    inner.appendChild(posture);

    // Channels
    var ch = el("div", "field");
    ch.appendChild(el("h4", null, "Channels"));
    ch.appendChild((a.channels && a.channels.length) ? tagRow(a.channels) : el("div", "muted", "none"));
    inner.appendChild(ch);

    // Lifecycle
    var life = el("div", "field");
    life.appendChild(el("h4", null, "Lifecycle"));
    var lifeTags = [];
    if (a.created_on) lifeTags.push("created " + datePart(a.created_on));
    if (a.modified_on) lifeTags.push("modified " + datePart(a.modified_on));
    if (a.published_on) lifeTags.push("published " + datePart(a.published_on));
    life.appendChild(lifeTags.length ? tagRow(lifeTags) : el("div", "muted", "unknown"));
    inner.appendChild(life);

    // Findings (full width)
    var fwrap = el("div", "field full");
    fwrap.appendChild(el("h4", null, "Findings (" + (a.findings ? a.findings.length : 0) + ")"));
    if (a.findings && a.findings.length) {
      a.findings.forEach(function (f) {
        var d = el("div", "finding s-" + f.severity);
        var head = el("div");
        head.appendChild(el("span", "fsev s-" + f.severity, f.severity));
        head.appendChild(el("span", "ftitle", f.title + "  (" + f.rule_id + ")"));
        d.appendChild(head);
        if (f.message) d.appendChild(el("div", "fmsg", f.message));
        if (f.remediation) {
          var rem = el("div", "frem");
          rem.appendChild(el("b", null, "Fix: "));
          rem.appendChild(document.createTextNode(f.remediation));
          d.appendChild(rem);
        }
        fwrap.appendChild(d);
      });
    } else {
      fwrap.appendChild(el("div", "muted", "No governance findings."));
    }
    inner.appendChild(fwrap);

    return inner;
  }

  // ── table rows ─────────────────────────────────────────────────────────────
  var tbody = document.getElementById("rows");

  function buildRow(a) {
    var tr = el("tr", "agent");
    tr.appendChild(el("td", "expander").appendChild(el("span", "exp-toggle", "▸")).parentNode);

    var nameTd = el("td");
    nameTd.appendChild(el("div", "agent-name", a.name));
    nameTd.appendChild(el("div", "agent-id", a.external_id));
    tr.appendChild(nameTd);

    var srcTd = el("td");
    // Cloud chip first (Microsoft / GCP) so mixed-tenant reports group at a glance.
    srcTd.appendChild(el("span", "cloud cloud-" + cloudKey(a), cloudLabel(a)));
    srcTd.appendChild(el("span", "tag", srcLabel(a.source_system)));
    tr.appendChild(srcTd);

    tr.appendChild(el("td", null, pretty(a.kind)));
    tr.appendChild(el("td", null, a.model || "—"));
    var owner = ownerName(a);
    tr.appendChild(owner ? el("td", null, owner) : (function () { var t = el("td"); t.appendChild(el("span", "muted", "none")); return t; })());
    tr.appendChild(el("td", "num", String((a.channels || []).length)));

    var fTd = el("td", "num");
    var cell = el("span", "findings-cell");
    if (a.findings && a.findings.length) {
      var ms = sevByRank(maxSev(a));
      cell.appendChild(el("span", null, String(a.findings.length)));
      cell.appendChild(el("span", "dot sev-" + ms));
    } else {
      cell.appendChild(el("span", "clean", "✓"));
    }
    fTd.appendChild(cell);
    tr.appendChild(fTd);

    var detail = el("tr", "detail");
    detail.style.display = "none";
    var dtd = el("td");
    dtd.colSpan = 8;
    dtd.appendChild(buildDetail(a));
    detail.appendChild(dtd);

    function toggle() {
      var open = detail.style.display === "none";
      detail.style.display = open ? "" : "none";
      tr.classList.toggle("open", open);
      tr.setAttribute("aria-expanded", open ? "true" : "false");
    }
    tr.addEventListener("click", toggle);

    return [tr, detail];
  }

  // ── search / sort / filter state ───────────────────────────────────────────
  var state = { q: "", source: "", model: "", minSev: "", sortKey: "findings", asc: false };

  function searchText(a) {
    var parts = [a.name, a.external_id, a.model, a.kind, srcLabel(a.source_system),
      cloudLabel(a), a.provider, a.instructions];
    (a.owners || []).forEach(function (o) { parts.push(o.name, o.email); });
    (a.tools || []).forEach(function (t) { parts.push(t.name, t.tool_type); });
    (a.channels || []).forEach(function (c) { parts.push(c); });
    return parts.filter(Boolean).join(" ").toLowerCase();
  }

  function sortVal(a, key) {
    switch (key) {
      case "source_system": return srcLabel(a.source_system).toLowerCase();
      case "model": return (a.model || "").toLowerCase();
      case "kind": return (a.kind || "").toLowerCase();
      case "owner": return ownerName(a).toLowerCase();
      case "channels": return (a.channels || []).length;
      case "findings": return maxSev(a) * 1000 + (a.findings ? a.findings.length : 0);
      default: return (a.name || "").toLowerCase();
    }
  }

  function render() {
    var q = state.q.trim().toLowerCase();
    var filtered = agents.filter(function (a) {
      if (state.source && a.source_system !== state.source) return false;
      if (state.model && (a.model || "unknown") !== state.model) return false;
      if (state.minSev && maxSev(a) < SEVRANK[state.minSev]) return false;
      if (q && searchText(a).indexOf(q) === -1) return false;
      return true;
    });

    filtered.sort(function (x, y) {
      var vx = sortVal(x, state.sortKey), vy = sortVal(y, state.sortKey), c;
      if (typeof vx === "number" && typeof vy === "number") c = vx - vy;
      else c = String(vx).localeCompare(String(vy));
      if (c === 0) c = (x.name || "").localeCompare(y.name || "");
      return state.asc ? c : -c;
    });

    tbody.textContent = "";
    filtered.forEach(function (a) {
      var pair = buildRow(a);
      tbody.appendChild(pair[0]);
      tbody.appendChild(pair[1]);
    });

    document.getElementById("showing").textContent =
      "Showing " + filtered.length + " of " + agents.length + " agents";
    document.getElementById("empty").hidden = filtered.length !== 0;
  }

  // Coverage note — only the sentences for the providers actually present here.
  function buildCoverageNote() {
    var node = document.getElementById("coverage-note");
    if (!node) return;
    var clouds = {};
    agents.forEach(function (a) { clouds[cloudKey(a)] = true; });
    var parts = ["Coverage: AgentCensus reports what the discovery APIs expose; gaps are shown, not scored."];
    if (clouds.microsoft) {
      parts.push("Microsoft: content-safety / RAI and DLP posture is governed via Microsoft Purview "
        + "(DSPM for AI) and Azure AI Content Safety, which discovery can't see; verified ownership is "
        + "often not harvestable.");
    }
    if (clouds.google) {
      parts.push("Google Cloud: no org-wide project auto-enumeration (coverage = the projects and regions "
        + "scanned); code-deployed Agent Engine / Agentspace agents keep their model, instructions, and "
        + "tools in the deployment package — shown as “not exposed” unless a no-code design "
        + "config is available. Safety / DLP posture (Vertex AI safety, Model Armor) is not surfaced by "
        + "discovery.");
    }
    node.textContent = parts.join(" ");
  }

  // ── wire up ────────────────────────────────────────────────────────────────
  function debounce(fn, ms) {
    var t;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  function init() {
    document.getElementById("m-total").textContent = String(summary.total_agents || 0);
    buildCards();
    buildFilters();
    buildCoverageNote();

    var q = document.getElementById("q");
    q.addEventListener("input", debounce(function () { state.q = q.value; render(); }, 120));
    document.getElementById("f-source").addEventListener("change", function (e) { state.source = e.target.value; render(); });
    document.getElementById("f-model").addEventListener("change", function (e) { state.model = e.target.value; render(); });
    document.getElementById("f-sev").addEventListener("change", function (e) { state.minSev = e.target.value; render(); });

    var ths = document.querySelectorAll("thead th[data-k]");
    function paint() {
      ths.forEach(function (th) {
        var on = th.getAttribute("data-k") === state.sortKey;
        th.classList.toggle("sorted", on);
        th.classList.toggle("asc", on && state.asc);
      });
    }
    ths.forEach(function (th) {
      th.addEventListener("click", function () {
        var k = th.getAttribute("data-k");
        if (state.sortKey === k) state.asc = !state.asc;
        else { state.sortKey = k; state.asc = (k === "name" || k === "owner" || k === "model" || k === "kind"); }
        paint();
        render();
      });
    });

    paint();
    render();
  }

  init();
})();
