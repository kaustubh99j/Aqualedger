/* AquaLedger shared front-end behaviour */
(function () {
  "use strict";

  const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const state = (window.AQUALEDGER = window.AQUALEDGER || {});

  state.csrfToken = CSRF;

  /* ------------------------------------------------------------------ helpers */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function fmt(value, digits = 0) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "0";
    return n.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  function fmtDate(value, opts) {
    if (!value) return "--";
    const date = value instanceof Date ? value : new Date(value + (String(value).length === 10 ? "T00:00:00" : ""));
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleDateString("en-IN", opts || { day: "2-digit", month: "short", year: "numeric" });
  }

  function shortHash(value, head = 10, tail = 6) {
    if (!value) return "--";
    const str = String(value);
    if (str.length <= head + tail + 3) return str;
    return str.slice(0, head) + "..." + str.slice(-tail);
  }

  state.fmt = fmt;
  state.fmtDate = fmtDate;
  state.shortHash = shortHash;
  state.escapeHtml = escapeHtml;

  /* -------------------------------------------------------------------- toast */
  function toast(message, type = "info", timeout = 6500) {
    const stack = $("#flashStack");
    if (!stack) return;
    const el = document.createElement("div");
    el.className = "flash " + type;
    el.setAttribute("role", "status");
    const icons = { success: "check-circle", danger: "alert", warning: "alert", info: "info" };
    el.innerHTML =
      '<svg class="icon" aria-hidden="true"><use href="#i-' + (icons[type] || "info") + '"></use></svg>' +
      "<span>" + escapeHtml(message) + "</span>" +
      '<button class="flash-close" type="button" aria-label="Dismiss">×</button>';
    stack.appendChild(el);
    const close = () => {
      el.classList.add("hide");
      setTimeout(() => el.remove(), 260);
    };
    el.querySelector(".flash-close").addEventListener("click", close);
    if (timeout) setTimeout(close, timeout);
  }
  state.toast = toast;

  /* ---------------------------------------------------------------------- api */
  async function api(path, options = {}) {
    const opts = Object.assign({ method: "GET" }, options);
    const method = (opts.method || "GET").toUpperCase();
    const headers = Object.assign(
      { Accept: "application/json" },
      method !== "GET" ? { "Content-Type": "application/json" } : {},
      opts.headers || {}
    );
    if (method !== "GET") headers["X-CSRF-Token"] = CSRF;
    if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    opts.headers = headers;
    let response;
    try {
      response = await fetch(path, opts);
    } catch (err) {
      throw Object.assign(new Error("Network error. Check your connection and try again."), { cause: err });
    }
    let data = null;
    try {
      data = await response.json();
    } catch (err) {
      /* non-JSON response */
    }
    if (!response.ok || (data && data.ok === false)) {
      const message = (data && (data.error || data.message)) || "Request failed (" + response.status + ")";
      throw Object.assign(new Error(message), { status: response.status, data });
    }
    return data || {};
  }
  state.api = api;

  /* ------------------------------------------------------------------- sidebar */
  $$("[data-sidebar-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.body.classList.toggle("sidebar-open");
      btn.setAttribute("aria-expanded", document.body.classList.contains("sidebar-open"));
    })
  );
  $$("[data-sidebar-close]").forEach((el) =>
    el.addEventListener("click", () => document.body.classList.remove("sidebar-open"))
  );

  /* --------------------------------------------------------------------- modals */
  function openModal(id) {
    const dialog = typeof id === "string" ? document.getElementById(id) : id;
    if (!dialog) return;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    document.body.style.overflow = "hidden";
  }
  function closeModal(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
    document.body.style.overflow = "";
  }
  state.openModal = openModal;
  state.closeModal = closeModal;

  $$("[data-open-modal]").forEach((btn) =>
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      openModal(btn.getAttribute("data-open-modal"));
    })
  );
  $$("[data-close-modal]").forEach((btn) =>
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      closeModal(btn.closest("dialog"));
    })
  );
  $$("dialog.modal").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) closeModal(dialog);
    });
    dialog.addEventListener("close", () => {
      document.body.style.overflow = "";
    });
  });

  /* ----------------------------------------------------------------------- flash */
  $$("#flashStack .flash").forEach((flash) => {
    const close = () => {
      flash.classList.add("hide");
      setTimeout(() => flash.remove(), 260);
    };
    const btn = flash.querySelector(".flash-close");
    if (btn) btn.addEventListener("click", close);
    const isDanger = flash.classList.contains("danger");
    setTimeout(close, isDanger ? 12000 : 7000);
  });

  /* ------------------------------------------------------------------- copy text */
  document.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-copy]");
    if (!btn) return;
    event.preventDefault();
    const value = btn.getAttribute("data-copy");
    try {
      await navigator.clipboard.writeText(value);
      toast("Copied to clipboard.", "success", 2500);
    } catch (err) {
      const textarea = document.createElement("textarea");
      textarea.value = value;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      try {
        document.execCommand("copy");
        toast("Copied to clipboard.", "success", 2500);
      } catch (e) {
        toast("Copy failed. Select the text manually.", "danger");
      }
      textarea.remove();
    }
  });

  /* ------------------------------------------------------------------ confirms */
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form.matches("[data-confirm]")) {
      const message = form.getAttribute("data-confirm") || "Are you sure?";
      if (!window.confirm(message)) {
        event.preventDefault();
        return;
      }
    }
    const submitter = event.submitter;
    if (submitter && submitter.hasAttribute("data-loading")) {
      const label = submitter.getAttribute("data-loading") || "Working...";
      setTimeout(() => {
        submitter.disabled = true;
        submitter.dataset.originalText = submitter.innerHTML;
        submitter.innerHTML = '<span class="spinner"></span> ' + escapeHtml(label);
      }, 0);
    }
  });

  /* ------------------------------------------------------------- password toggle */
  $$("[data-password-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.getAttribute("data-password-toggle"));
      if (!input) return;
      input.type = input.type === "password" ? "text" : "password";
      btn.classList.toggle("revealed");
    })
  );

  /* -------------------------------------------------------------------- nav active */
  const params = new URLSearchParams(window.location.search);
  if (params.get("toast")) toast(params.get("toast"), params.get("toastType") || "info");

  /* ------------------------------------------------------------------- utilities */
  state.debounce = function (fn, wait = 250) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  };
})();
