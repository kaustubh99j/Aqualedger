/* Profile: MetaMask wallet linking and authorisation status */
(function () {
  "use strict";
  const A = window.AQUALEDGER;
  const dataEl = document.getElementById("walletProfileData");
  if (!dataEl) return;
  const profile = JSON.parse(dataEl.textContent);

  const badge = document.getElementById("walletStatusBadge");
  const addressText = document.getElementById("walletAddressText");
  const verifiedText = document.getElementById("walletVerifiedText");
  const authState = document.getElementById("walletAuthState");
  const banner = document.getElementById("walletBanner");
  const bannerTitle = document.getElementById("walletBannerTitle");
  const bannerText = document.getElementById("walletBannerText");
  const errorBox = document.getElementById("walletError");
  const connectBtn = document.getElementById("walletConnectBtn");
  const unlinkBtn = document.getElementById("walletUnlinkBtn");
  const refreshBtn = document.getElementById("walletRefreshBtn");

  function showError(message) {
    if (errorBox) errorBox.textContent = message || "";
    if (message) A.toast(message, "danger", 9000);
  }

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      if (!btn.disabled) btn.dataset.original = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> ' + (label || "Working...");
    } else {
      btn.disabled = false;
      if (btn.dataset.original) {
        btn.innerHTML = btn.dataset.original;
        delete btn.dataset.original;
      }
    }
  }

  function render(status) {
    const linked = !!status.linked;
    const authorized = status.authorized === true;
    const pendingAuth = linked && status.mode === "sepolia" && status.authorized === false;

    if (badge) {
      badge.className = "badge badge-" + (linked ? (authorized || status.mode === "simulation" ? "success" : "warning") : "neutral");
      badge.innerHTML = '<span class="dot"></span>' + (linked ? (authorized || status.mode === "simulation" ? "Linked" : "Awaiting authorisation") : "Not linked");
    }
    if (addressText) addressText.textContent = status.address || "No wallet linked";
    if (verifiedText) verifiedText.textContent = status.verified ? "Yes" : "No";
    if (authState) {
      authState.textContent = !linked ? "Not linked" : authorized ? "Authorised on contract" : status.mode === "simulation" ? "Simulation mode" : "Not authorised yet";
      authState.style.color = authorized || status.mode === "simulation" ? "var(--green)" : "var(--amber)";
    }
    if (banner) {
      banner.className = "banner " + (!linked ? "info" : pendingAuth ? "warn" : "success");
    }
    if (bannerTitle) {
      bannerTitle.textContent = !linked ? "Link your wallet" : pendingAuth ? "Wallet linked — authorisation needed" : "Wallet ready to sign proofs";
    }
    if (bannerText) {
      bannerText.textContent = !linked
        ? "Clicking link asks MetaMask to sign a free, off-chain message. No gas is spent and no transaction is created."
        : pendingAuth
        ? "Your wallet is verified. An administrator must authorise it on the AquaLedger contract before you can sign anchoring transactions."
        : "You can sign anchoring transactions. Personal data never reaches the chain — only pseudonymous record proofs do.";
    }
    if (connectBtn) {
      const svg = connectBtn.querySelector("svg");
      connectBtn.dataset.icon = svg ? svg.outerHTML : "";
      connectBtn.innerHTML = connectBtn.dataset.icon + " " + (linked ? "Re-link wallet" : "Connect MetaMask");
    }
  }

  // Preserve the icon markup from the server-rendered button.
  if (connectBtn) {
    const svg = connectBtn.querySelector("svg");
    connectBtn.dataset.icon = svg ? svg.outerHTML : "";
  }

  async function refreshStatus(silent) {
    try {
      const result = await A.api("/api/wallet/status");
      render(result);
      if (!silent) A.toast("Wallet status refreshed.", "success", 2500);
      return result;
    } catch (err) {
      if (!silent) showError(err.message);
      return null;
    }
  }

  async function connect() {
    showError("");
    if (!window.ethereum) {
      showError("MetaMask is not installed. Install the browser extension from metamask.io and reload this page.");
      return;
    }
    if (!window.ethers) {
      showError("The ethers.js library failed to load. Refresh the page and try again.");
      return;
    }
    setBusy(connectBtn, true, "Connecting...");
    try {
      const provider = new ethers.BrowserProvider(window.ethereum);
      const accounts = await provider.send("eth_requestAccounts", []);
      if (!accounts || !accounts.length) throw new Error("MetaMask did not return an account.");
      const address = accounts[0];
      const signer = await provider.getSigner();

      const challenge = await A.api("/api/wallet/nonce?address=" + encodeURIComponent(address));
      if (connectBtn) setBusy(connectBtn, true, "Confirm in MetaMask...");
      const signature = await signer.signMessage(challenge.message);

      if (connectBtn) setBusy(connectBtn, true, "Verifying...");
      await A.api("/api/wallet/link", {
        method: "POST",
        body: { address: address, signature: signature },
      });
      A.toast("Wallet linked successfully. Reloading your profile...", "success");
      setTimeout(() => window.location.reload(), 1000);
    } catch (err) {
      let message = err && err.message ? err.message : "Wallet linking failed.";
      if (err && (err.code === 4001 || err.code === "ACTION_REJECTED")) message = "You rejected the signature request in MetaMask.";
      else if (/user rejected/i.test(message)) message = "You rejected the signature request in MetaMask.";
      showError(message);
    } finally {
      setBusy(connectBtn, false);
    }
  }

  async function unlink() {
    if (!window.confirm("Unlink this wallet? You will no longer be able to sign anchoring transactions until you link again.")) return;
    showError("");
    setBusy(unlinkBtn, true, "Unlinking...");
    try {
      await A.api("/api/wallet/unlink", { method: "POST", body: {} });
      A.toast("Wallet unlinked. Reloading...", "success");
      setTimeout(() => window.location.reload(), 900);
    } catch (err) {
      showError(err.message);
      setBusy(unlinkBtn, false);
    }
  }

  if (connectBtn) connectBtn.addEventListener("click", connect);
  if (unlinkBtn) unlinkBtn.addEventListener("click", unlink);
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      setBusy(refreshBtn, true, "Refreshing...");
      await refreshStatus(false);
      setBusy(refreshBtn, false);
    });
  }

  refreshStatus(true);
})();
