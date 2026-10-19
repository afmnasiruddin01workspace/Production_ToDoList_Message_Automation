(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var body = document.body;

  function mode() { var m = $("input[name=mode]:checked"); return m ? m.value : "dry-run"; }
  function paintMode() { body.className = "mode-" + mode(); }
  $$("input[name=mode]").forEach(function (r) { r.addEventListener("change", paintMode); });
  // R7: the mode is never remembered, even when the browser restores a page from its cache.
  window.addEventListener("pageshow", function () { var d = $("input[name=mode][value=dry-run]"); if (d) { d.checked = true; paintMode(); } });

  function el(tag, text, cls) { var e = document.createElement(tag); if (text) e.textContent = text; if (cls) e.className = cls; return e; }

  function post(url, data) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, body: j }; }); });
  }

  // ---- preview (R6)
  $$(".preview").forEach(function (b) {
    b.addEventListener("click", function () {
      var tr = b.closest("tr"), box = tr.nextElementSibling, inner = $(".prevbox", box);
      if (!box.hidden) { box.hidden = true; return; }
      inner.textContent = "loading..."; box.hidden = false;
      var person = b.dataset.person, out = [];
      var calls = ["whatsapp", "email"].filter(function (ch) { return $("input[data-ch=" + ch + "][data-person='" + person + "']"); });
      Promise.all(calls.map(function (ch) {
        return fetch("/api/preview?person=" + encodeURIComponent(person) + "&channel=" + ch).then(function (r) { return r.json(); })
          .then(function (j) { out.push((ch === "whatsapp" ? "WHATSAPP\n" : "EMAIL  Subject: " + (j.subject || "") + "\n") + (j.text || j.error || "")); });
      })).then(function () { inner.textContent = out.join("\n\n-----\n\n") || "(not on any channel)"; });
    });
  });

  if (!$("#confirm")) { return; }  // the Log page has no send controls

  // ---- confirm dialog
  var pending = null;
  function openConfirm(title, nodes, go) {
    $("#confirm-title").textContent = title;
    var b = $("#confirm-body"); b.textContent = "";
    nodes.forEach(function (n) { b.appendChild(n); });
    pending = go; $("#confirm").hidden = false; checkGo();
  }
  function checkGo() { $("#confirm-go").disabled = $$("#confirm-body input[type=checkbox]").some(function (c) { return !c.checked; }); }
  $("#confirm-cancel").addEventListener("click", function () { $("#confirm").hidden = true; pending = null; });
  $("#confirm-go").addEventListener("click", function () { var g = pending; $("#confirm").hidden = true; pending = null; if (g) g(); });
  $("#confirm-body").addEventListener("change", checkGo);
  function ticky(text) { var l = el("label"); var c = el("input"); c.type = "checkbox"; l.appendChild(c); l.appendChild(document.createTextNode(" " + text)); return l; }

  // ---- send (R7, R8, R17)
  function chosen() {
    var out = { whatsapp: [], email: [], resend: { whatsapp: [], email: [] }, firsts: {} };
    $$("input[type=checkbox][data-ch]").forEach(function (c) {
      if (!c.checked) return;
      out[c.dataset.ch].push(c.dataset.person);
      if (c.dataset.resend) { out.resend[c.dataset.ch].push(c.dataset.person); out.firsts[c.dataset.ch + ":" + c.dataset.person] = c.dataset.first; }
    });
    return out;
  }
  var sendBtn = $("#send-btn");
  if (sendBtn) sendBtn.addEventListener("click", function () {
    var c = chosen(), m = mode();
    if (!c.whatsapp.length && !c.email.length) { showError("Tick at least one person."); return; }
    var nodes = [];
    nodes.push(el("p", "Mode: " + m.toUpperCase() + ". WhatsApp: " + c.whatsapp.length + " person(s) · Email: " + c.email.length + " person(s)."));
    if (m === "live") nodes.push(el("p", "These go to real people.", "banner bad"));
    if (m === "live") nodes.push(ticky("I understand these messages go to real people."));
    var re = [];
    ["whatsapp", "email"].forEach(function (ch) {
      c.resend[ch].forEach(function (p) {
        re.push([ch, p]);
        nodes.push(ticky("Send again: " + p + " (" + ch + ") already got today's list at " + (c.firsts[ch + ":" + p] || "?") + "."));
      });
    });
    if (re.length && m !== "live") { showError("A person already sent today can only be sent again in live mode. Untick them, or switch to LIVE."); return; }
    openConfirm("Send now?", nodes, function () {
      post("/api/send", { mode: m, whatsapp: c.whatsapp, email: c.email, resend: c.resend, confirm_live: m === "live", confirm_resend: re.length > 0 })
        .then(function (r) { if (r.ok) { watch(); } else { showError(r.body.error || ("Refused (" + r.status + ")")); } });
    });
  });

  var rb = $("#rebuild-btn");
  if (rb) rb.addEventListener("click", function () {
    openConfirm("Rebuild the list?", [el("p", "This runs a headless Claude session for module 01 only. It costs roughly a few tenths of a US dollar and takes 1-2 minutes (10 minutes at most). It holds the scheduler lock meanwhile.")], function () {
      post("/api/rebuild", { confirm: true }).then(function (r) { if (r.ok) { watch(); } else { showError(r.body.error || "Refused"); } });
    });
  });
  var rf = $("#refresh-btn");
  if (rf) rf.addEventListener("click", function () {
    openConfirm("Refresh email receipts?", [el("p", "This looks for bounce messages (undeliverable replies) for the last 3 days. Outlook may be started if it is closed. Nothing is sent.")], function () {
      post("/api/reconcile", { confirm: true }).then(function (r) { if (r.ok) { watch(); } else { showError(r.body.error || "Refused"); } });
    });
  });

  // ---- job progress (R12)
  var timer = null;
  function showError(msg) {
    var b = $("#jobbox"); if (!b) return;
    b.hidden = false; $("#jobstate").textContent = "refused"; $("#jobstate").className = "pill bad";
    $("#jobstep").textContent = ""; $("#jobmsg").textContent = msg; $("#joblines").textContent = ""; $("#outcome").hidden = true;
  }
  function watch() { if (timer) clearInterval(timer); poll(); timer = setInterval(poll, 1000); }
  function poll() {
    fetch("/api/job").then(function (r) { return r.json(); }).then(function (j) {
      if (j.state === "idle") return;
      $("#jobbox").hidden = false;
      var st = $("#jobstate"); st.textContent = j.state; st.className = "pill " + (j.state === "done" ? "ok" : j.state === "failed" ? "bad" : "amber");
      $("#jobstep").textContent = j.step || "";
      var pre = $("#joblines"); var stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 5;
      pre.textContent = (j.lines || []).join("\n"); if (stick) pre.scrollTop = pre.scrollHeight;
      var msg = $("#jobmsg"); msg.textContent = j.state === "running" ? "" : (j.message || "");
      if (j.state === "running" && !timer) { timer = setInterval(poll, 1000); }
      if (j.state !== "running") {
        if (timer) { clearInterval(timer); timer = null; }
        var t = $("#outcome"), tb = $("tbody", t); tb.textContent = "";
        (j.outcome || []).forEach(function (o) {
          var tr = el("tr"); [o.person, o.channel, o.status + (o.resend ? " (resend)" : ""), o.delivery, o.detail].forEach(function (v) { tr.appendChild(el("td", v)); });
          tb.appendChild(tr);
        });
        t.hidden = !(j.outcome || []).length;
        (j.steps || []).forEach(function (s) {
          if (s.status !== "ok") { msg.appendChild(el("p", s.channel + " failed: " + s.message + " — see " + s.help + " (exit " + s.exit + ")", "banner bad")); }
        });
        fetch("/api/state").then(function (r) { return r.json(); }).then(refreshStates);
      }
    });
  }
  function refreshStates(s) {
    s.table.rows.forEach(function (row) {
      var tr = $("tr[data-person='" + row.person + "']"); if (!tr) return;
      var tds = $$("td.st", tr);
      ["whatsapp", "email"].forEach(function (ch, i) {
        var c = row[ch]; if (!c.present || !tds[i]) return;
        tds[i].className = "st " + (c.cls || ""); tds[i].textContent = c.state;
        tds[i].appendChild(el("small", c.count + " item(s)" + (c.due ? " · due today" : " · not due")));
        var cb = $("input[data-ch=" + ch + "][data-person='" + row.person + "']", tr);
        if (cb) { cb.dataset.resend = c.resend ? "1" : ""; cb.dataset.first = c.first_sent || ""; cb.checked = false; }
      });
    });
  }
  poll();  // pick up a job already running (e.g. after a page reload)
})();
