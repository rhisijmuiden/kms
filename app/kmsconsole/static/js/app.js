(function () {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const T = window.KMS_T || {};
  const t = (k) => T[k] || k;

  function post(url, data) {
    const body = new URLSearchParams(data || {});
    return fetch(url, { method: "POST", body, headers: { "X-CSRFToken": csrf }, credentials: "same-origin" })
      .then((r) => r.json());
  }

  // copy buttons
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    const src = document.querySelector(btn.dataset.copy);
    const text = src ? src.innerText : "";
    const done = () => { const old = btn.textContent; btn.textContent = t("Copied"); setTimeout(() => (btn.textContent = old), 1600); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } finally { ta.remove(); }
    }
  });

  // mobile menu
  const toggle = document.querySelector(".menu-toggle");
  toggle?.addEventListener("click", () => {
    const side = document.querySelector(".side");
    const open = side.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });

  // auto-submit selects
  document.querySelectorAll("select[data-autosubmit], input[data-autosubmit]").forEach((s) => s.addEventListener("change", () => s.form.submit()));

  // ---------------------------------------------------------------- info dialog
  const dlg = document.getElementById("info-dialog");
  if (dlg) {
    const box = dlg.querySelector(".update-box");
    const title = dlg.querySelector("[data-update-title]");
    const sub = dlg.querySelector("[data-update-sub]");
    const updBtn = dlg.querySelector("[data-update-start]");
    const checkBtn = dlg.querySelector("[data-update-check]");
    const hint = dlg.querySelector("[data-channel-hint]");
    const logEl = dlg.querySelector(".log");
    let channel = dlg.dataset.channel || "production";

    function setChannel(c) {
      channel = c;
      dlg.querySelectorAll("[data-channel]").forEach((b) => {
        const on = b.dataset.channel === c;
        b.classList.toggle("is-on", on);
        b.setAttribute("aria-pressed", String(on));
      });
      hint.textContent = c === "test" ? t("Newest features from the test branch. May contain bugs.") : t("Stable releases from the production branch.");
    }

    function render(info) {
      if (!info) return;
      const switching = info.local_branch && info.local_branch !== info.branch;
      dlg.querySelector("[data-local]").textContent = `${info.current_version} · ${(info.local_commit || "").slice(0, 7)} (${info.local_branch || "-"})`;
      dlg.querySelector("[data-checked]").textContent = new Date(info.checked * 1000).toLocaleString();
      if (info.available) {
        box.classList.add("has-update");
        title.textContent = switching ? t("Switch to {branch}").replace("{branch}", info.branch) + ` (${info.remote_version})`
                                      : t("Version {v} is available").replace("{v}", info.remote_version);
        sub.textContent = `${t("Commit")} ${(info.remote_commit || "").slice(0, 7)} · ${t("A backup is made automatically before updating.")}`;
        updBtn.hidden = false;
      } else {
        box.classList.remove("has-update");
        title.textContent = t("You are up to date");
        sub.textContent = `${info.branch} · ${(info.remote_commit || "").slice(0, 7)}`;
        updBtn.hidden = true;
      }
    }

    function check() {
      checkBtn.disabled = true;
      title.textContent = t("Checking for updates...");
      sub.textContent = "";
      post("/api/update/check", { channel }).then((r) => {
        if (r.ok) render(r.info);
        else { title.textContent = t("Update check failed"); sub.textContent = r.message; updBtn.hidden = true; }
      }).catch(() => { title.textContent = t("Update check failed"); })
        .finally(() => (checkBtn.disabled = false));
    }

    function pollLog(tries) {
      fetch("/api/update/log", { credentials: "same-origin" }).then((r) => (r.ok ? r.text() : "")).then((txt) => {
        if (txt) { logEl.hidden = false; logEl.textContent = txt; logEl.scrollTop = logEl.scrollHeight; }
        if (/UPDATE DONE/.test(txt)) { setTimeout(() => location.reload(), 2500); return; }
        if (/UPDATE FAILED/.test(txt)) { updBtn.disabled = false; return; }
        if (tries > 0) setTimeout(() => pollLog(tries - 1), 2000);
      }).catch(() => tries > 0 && setTimeout(() => pollLog(tries - 1), 3000));
    }

    document.querySelectorAll("[data-open-info]").forEach((b) => b.addEventListener("click", () => {
      dlg.showModal();
      const last = window.KMS_UPDATE;
      if (last && last.checked) render(last);
      if (!last || !last.checked || Date.now() / 1000 - last.checked > 3600) check();
    }));
    dlg.querySelector(".x").addEventListener("click", () => dlg.close());
    dlg.querySelectorAll("[data-channel]").forEach((b) => b.addEventListener("click", () => { setChannel(b.dataset.channel); check(); }));
    checkBtn.addEventListener("click", check);
    updBtn.addEventListener("click", () => {
      updBtn.disabled = true;
      title.textContent = t("Making a backup and starting the update...");
      post("/api/update/start", { channel }).then((r) => {
        sub.textContent = r.message || "";
        if (r.ok) pollLog(90); else updBtn.disabled = false;
      }).catch(() => { updBtn.disabled = false; });
    });
    setChannel(channel);
  }

  // ---------------------------------------------------------------- backups
  const testBtn = document.querySelector("[data-remote-test]");
  testBtn?.addEventListener("click", () => {
    const out = document.querySelector("[data-remote-test-out]");
    testBtn.disabled = true; out.className = "flash flash-info"; out.hidden = false; out.textContent = t("Connecting...");
    post("/backups/test").then((r) => {
      out.className = "flash " + (r.ok ? "flash-ok" : "flash-error");
      out.textContent = r.message;
    }).finally(() => (testBtn.disabled = false));
  });

  const remoteBtn = document.querySelector("[data-remote-list]");
  remoteBtn?.addEventListener("click", () => {
    const body = document.querySelector("[data-remote-body]");
    const tpl = document.getElementById("remote-row");
    remoteBtn.disabled = true;
    body.innerHTML = "";
    const tr = document.createElement("tr");
    const td = document.createElement("td"); td.colSpan = 5; td.className = "empty"; td.textContent = t("Loading...");
    tr.appendChild(td); body.appendChild(tr);
    fetch("/backups/remote", { credentials: "same-origin" }).then((r) => r.json()).then((r) => {
      body.innerHTML = "";
      if (!r.ok || !r.items.length) {
        const tr2 = document.createElement("tr"); const td2 = document.createElement("td");
        td2.colSpan = 5; td2.className = "empty"; td2.textContent = r.ok ? t("No backups found on the remote location.") : r.message;
        tr2.appendChild(td2); body.appendChild(tr2); return;
      }
      r.items.forEach((b) => {
        const row = tpl.content.cloneNode(true);
        row.querySelector("[data-f=date]").textContent = b.created_text;
        const badge = row.querySelector("[data-f=kind]");
        badge.textContent = b.kind === "full" ? t("Full") : t("Incremental");
        badge.className = "badge " + (b.kind === "full" ? "b-full" : "b-inc");
        row.querySelector("[data-f=size]").textContent = (b.size / 1024).toFixed(1) + " KB";
        row.querySelector("[data-f=name]").textContent = b.name + (b.encrypted ? " 🔒" : "");
        row.querySelector("[data-restore]").dataset.name = b.name;
        body.appendChild(row);
      });
    }).finally(() => (remoteBtn.disabled = false));
  });

  const rdlg = document.getElementById("restore-dialog");
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-restore]");
    if (!b || !rdlg) return;
    rdlg.querySelector("[name=name]").value = b.dataset.name;
    rdlg.querySelector("[name=source]").value = b.dataset.source || "remote";
    rdlg.querySelector("[data-restore-name]").textContent = b.dataset.name;
    rdlg.querySelector("[name=confirm]").value = "";
    rdlg.showModal();
  });
  rdlg?.querySelector(".x")?.addEventListener("click", () => rdlg.close());

  // protocol default ports
  const proto = document.querySelectorAll("input[name=protocol]");
  proto.forEach((p) => p.addEventListener("change", () => {
    const port = document.querySelector("input[name=port]");
    const defaults = { ftp: 21, ftps: 21, scp: 22, sftp: 22 };
    if (port && Object.values(defaults).includes(Number(port.value))) port.value = defaults[p.value];
    document.querySelectorAll("[data-ssh-only]").forEach((el) => (el.hidden = !["scp", "sftp"].includes(p.value)));
    document.querySelectorAll("[data-tls-only]").forEach((el) => (el.hidden = p.value !== "ftps"));
  }));
  document.querySelector("input[name=protocol]:checked")?.dispatchEvent(new Event("change"));

  document.querySelectorAll("form[data-confirm]").forEach((f) => f.addEventListener("submit", (e) => {
    if (!window.confirm(f.dataset.confirm)) e.preventDefault();
  }));
})();

// Network & access: show the settings that belong to the chosen mode
(() => {
  const radios = document.querySelectorAll("input[name=mode][data-mode]");
  if (!radios.length) return;
  const sync = () => {
    const mode = [...radios].find((r) => r.checked)?.value;
    document.querySelectorAll("[data-mode-panel]").forEach((p) => {
      p.hidden = !p.dataset.modePanel.split(" ").includes(mode);
    });
  };
  radios.forEach((r) => r.addEventListener("change", sync));
  sync();
})();
