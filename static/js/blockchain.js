/* Blockchain explorer: live chain status refresh */
(function () {
  "use strict";
  const A = window.AQUALEDGER;
  const refreshBtn = document.getElementById("refreshChainBtn");
  const badge = document.getElementById("chainConnectBadge");
  const badgeText = document.getElementById("chainConnectText");
  const errorBanner = document.getElementById("chainErrorBanner");
  const errorText = document.getElementById("chainErrorText");
  const chainId = document.getElementById("chainIdValue");
  const blockNumber = document.getElementById("chainBlockValue");
  const recordCount = document.getElementById("chainRecordValue");
  const balance = document.getElementById("chainBalanceValue");

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      if (!btn.disabled) btn.dataset.original = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> ' + (label || "Refreshing...");
    } else {
      btn.disabled = false;
      if (btn.dataset.original) btn.innerHTML = btn.dataset.original;
    }
  }

  function render(status) {
    if (chainId) chainId.textContent = status.chain_id != null ? status.chain_id : "--";
    if (blockNumber) blockNumber.textContent = status.block_number != null ? status.block_number : "--";
    if (recordCount) recordCount.textContent = status.record_count != null ? status.record_count : "--";
    if (balance) balance.textContent = status.balance_eth != null ? status.balance_eth + " ETH" : "--";

    if (badge) {
      badge.className = "badge " + (status.connected ? "badge-success" : "badge-danger");
      badge.style.background = "rgba(255,255,255,.1)";
      badge.style.color = status.connected ? "#86efac" : "#fca5a5";
    }
    if (badgeText) badgeText.textContent = status.connected ? "Connected" : "Disconnected";

    if (errorBanner && errorText) {
      if (status.error) {
        errorText.textContent = status.error;
        errorBanner.style.display = "";
      } else {
        errorBanner.style.display = "none";
      }
    }
  }

  async function load(force, notify) {
    setBusy(refreshBtn, true, "Refreshing...");
    try {
      const result = await A.api("/api/blockchain/status" + (force ? "?force=1" : ""));
      render(result.status || {});
      if (notify) A.toast("Chain status refreshed.", "success", 2500);
    } catch (err) {
      if (errorBanner && errorText) {
        errorText.textContent = err.message;
        errorBanner.style.display = "";
      }
      if (notify) A.toast(err.message, "danger", 8000);
    } finally {
      setBusy(refreshBtn, false);
    }
  }

  if (refreshBtn) refreshBtn.addEventListener("click", () => load(true, true));

  // Keep the status panel fresh without hammering the RPC endpoint.
  let timer = null;
  function schedule() {
    if (timer) clearInterval(timer);
    timer = setInterval(() => {
      if (document.visibilityState === "visible") load(false, false);
    }, 60000);
  }
  schedule();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") load(false, false);
  });
})();
