/* Reading detail: verification, server anchoring and MetaMask signing */
(function () {
  "use strict";
  const A = window.AQUALEDGER;
  const el = document.getElementById("readingData");
  if (!el) return;
  const data = JSON.parse(el.textContent);
  const id = data.id;

  const verifyBtn = document.getElementById("verifyBtn");
  const verifyInline = document.getElementById("verifyBtnInline");
  const verifyError = document.getElementById("verifyError");
  const anchorError = document.getElementById("anchorError");
  const anchorHint = document.getElementById("anchorHint");
  const serverBtn = document.getElementById("anchorServerBtn");
  const metaBtn = document.getElementById("metamaskAnchorBtn");
  const refreshBtn = document.getElementById("refreshAnchorBtn");
  const storedHash = document.getElementById("storedHash");
  const recomputedHash = document.getElementById("recomputedHash");
  const verdict = document.getElementById("verifyVerdict");
  const dbMatchText = document.getElementById("dbMatchText");
  const chainMatchText = document.getElementById("chainMatchText");

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      if (!btn.disabled) btn.dataset.original = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = (label || "Working...");
    } else {
      btn.disabled = false;
      if (btn.dataset.original) {
        btn.innerHTML = btn.dataset.original;
        delete btn.dataset.original;
      }
    }
  }

  function hashBox(el, value, match) {
    if (!el) return;
    el.textContent = value || "--";
    el.classList.remove("match", "mismatch");
    if (match === true) el.classList.add("match");
    if (match === false) el.classList.add("mismatch");
  }

  function statusChip(status) {
    return '<span class="badge badge-' + status + '"><span class="dot"></span>' + status.charAt(0).toUpperCase() + status.slice(1) + "</span>";
  }

  async function verify() {
    if (verifyError) verifyError.textContent = "";
    setBusy(verifyBtn, true, "Verifying...");
    setBusy(verifyInline, true, "Verifying...");
    try {
      const result = await A.api(data.verify_url);
      const db = result.db || {};
      const chain = result.chain || {};
      hashBox(storedHash, db.stored_hash, db.match);
      hashBox(recomputedHash, db.recomputed_hash, db.match);
      if (dbMatchText) {
        dbMatchText.textContent = db.match ? "Match" : "Mismatch — record modified";
        dbMatchText.style.color = db.match ? "var(--green)" : "var(--red)";
      }
      if (chainMatchText) {
        if (chain.error) chainMatchText.textContent = "Chain check failed";
        else if (chain.exists) chainMatchText.textContent = chain.hash_matches ? "Proof matches" : "Proof mismatch";
        else chainMatchText.textContent = "Not anchored yet";
        chainMatchText.style.color = !chain.exists ? "var(--muted)" : chain.hash_matches ? "var(--green)" : "var(--red)";
      }
      if (verdict) {
        verdict.className = "badge badge-" + (result.verdict === "valid" ? "success" : result.verdict === "pending" ? "warning" : "danger");
        verdict.textContent = { valid: "Verified", tampered: "Tampered", pending: "Pending proof", error: "Chain error" }[result.verdict] || result.verdict;
      }
      A.toast(result.message, result.verdict === "valid" ? "success" : result.verdict === "pending" ? "warning" : "danger", 9000);
    } catch (err) {
      if (verifyError) verifyError.textContent = err.message;
      A.toast(err.message, "danger");
    } finally {
      setBusy(verifyBtn, false);
      setBusy(verifyInline, false);
    }
  }

  if (verifyBtn) verifyBtn.addEventListener("click", verify);
  if (verifyInline) verifyInline.addEventListener("click", verify);

  function showAnchorError(message) {
    if (anchorError) anchorError.textContent = message || "";
    if (message) A.toast(message, "danger", 9000);
  }

  function updateAnchorUi(result) {
    const badge = document.getElementById("anchorStatusBadge");
    if (badge) badge.innerHTML = statusChip(result.status);
    const txCell = document.getElementById("txHashCell");
    if (txCell && result.tx_hash) txCell.textContent = result.tx_hash;
    const blockCell = document.getElementById("blockCell");
    if (blockCell && result.block) blockCell.textContent = result.block;
    const timeCell = document.getElementById("anchorTimeCell");
    if (timeCell && result.chain_timestamp) timeCell.textContent = String(result.chain_timestamp).replace("T", " ").replace("Z", " UTC");
  }

  async function poll(attempt) {
    attempt = attempt || 0;
    try {
      const result = await A.api(data.anchor_status_url);
      updateAnchorUi(result);
      if (result.status === "anchored") {
        A.toast("Proof anchored successfully. Reloading...", "success");
        setTimeout(() => window.location.reload(), 1200);
        return;
      }
      if (result.status === "failed") {
        const tx = (result.transactions || [])[0];
        showAnchorError("Anchoring failed" + (tx && tx.error_message ? ": " + tx.error_message : "."));
        if (serverBtn) setBusy(serverBtn, false);
        return;
      }
      if (anchorHint) anchorHint.textContent = "Waiting for the transaction to be confirmed… This can take up to a minute on Sepolia.";
      if (attempt < 45) {
        setTimeout(() => poll(attempt + 1), 4000);
      } else {
        showAnchorError("Still waiting. Check the transaction on Etherscan, then press Refresh status.");
        if (serverBtn) setBusy(serverBtn, false);
      }
    } catch (err) {
      if (attempt < 5) setTimeout(() => poll(attempt + 1), 4000);
      else showAnchorError(err.message);
    }
  }

  if (serverBtn) {
    serverBtn.addEventListener("click", async () => {
      showAnchorError("");
      setBusy(serverBtn, true, "Submitting...");
      try {
        const result = await A.api(data.anchor_url, { method: "POST", body: {} });
        A.toast(result.reason === "already in progress" ? "Anchoring is already in progress." : "Anchoring started.", "info");
        poll(0);
      } catch (err) {
        showAnchorError(err.message);
        setBusy(serverBtn, false);
      }
    });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      try {
        const result = await A.api(data.anchor_status_url);
        updateAnchorUi(result);
        if (result.status === "anchored") {
          A.toast("Proof confirmed.", "success");
          setTimeout(() => window.location.reload(), 900);
        } else {
          A.toast("Current status: " + result.status, "info");
        }
      } catch (err) {
        A.toast(err.message, "danger");
      }
    });
  }

  async function addSepolia() {
    await window.ethereum.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: "0xaa36a7",
          chainName: "Sepolia",
          nativeCurrency: { name: "Sepolia Ether", symbol: "ETH", decimals: 18 },
          rpcUrls: ["https://ethereum-sepolia-rpc.publicnode.com"],
          blockExplorerUrls: ["https://sepolia.etherscan.io"],
        },
      ],
    });
  }

  async function metamaskAnchor() {
    showAnchorError("");
    if (!window.ethereum) {
      showAnchorError("MetaMask is not installed. Install the MetaMask browser extension to sign transactions.");
      return;
    }
    if (!window.ethers) {
      showAnchorError("The ethers.js library failed to load. Refresh the page and try again.");
      return;
    }
    setBusy(metaBtn, true, "Connecting...");
    try {
      const contractInfo = await A.api("/api/contract");
      if (!contractInfo.address) throw new Error("No contract address is configured.");
      const prepare = await A.api(data.anchor_prepare_url);

      let provider = new ethers.BrowserProvider(window.ethereum);
      const accounts = await provider.send("eth_requestAccounts", []);
      if (!accounts.length) throw new Error("MetaMask did not return an account.");
      if (prepare.wallet && accounts[0].toLowerCase() !== prepare.wallet.toLowerCase()) {
        throw new Error("MetaMask is using " + accounts[0] + ", but your linked wallet is " + prepare.wallet + ". Switch accounts in MetaMask and try again.");
      }

      const network = await provider.getNetwork();
      if (Number(network.chainId) !== Number(prepare.chain_id)) {
        try {
          await window.ethereum.request({
            method: "wallet_switchEthereumChain",
            params: [{ chainId: "0x" + Number(prepare.chain_id).toString(16) }],
          });
        } catch (switchErr) {
          if (switchErr && (switchErr.code === 4902 || switchErr.code === -32603)) await addSepolia();
          else throw new Error("Switch MetaMask to the Sepolia testnet and try again.");
        }
        provider = new ethers.BrowserProvider(window.ethereum);
      }

      const signer = await provider.getSigner();
      const contract = new ethers.Contract(contractInfo.address, contractInfo.abi, signer);
      if (anchorHint) anchorHint.textContent = "Confirm the anchoring transaction in MetaMask…";

      const tx = await contract.anchorRecord(
        prepare.args.householdRef,
        BigInt(prepare.args.sequence),
        BigInt(prepare.args.consumptionMl),
        BigInt(prepare.args.readingDate),
        prepare.args.recordHash
      );

      if (anchorHint) {
        anchorHint.innerHTML =
          'Transaction submitted: <a href="' + contractInfo.explorer + "/tx/" + tx.hash + '" target="_blank" rel="noopener">' +
          A.shortHash(tx.hash) + "</a>. Waiting for confirmation…";
      }
      A.toast("Transaction submitted. Waiting for Sepolia confirmation…", "info", 8000);
      setBusy(metaBtn, true, "Waiting for block...");
      await tx.wait(1);

      let confirm = null;
      for (let attempt = 0; attempt < 8; attempt++) {
        confirm = await A.api(data.anchor_confirm_url, { method: "POST", body: { tx_hash: tx.hash } });
        if (confirm.status === "confirmed") break;
        await new Promise((resolve) => setTimeout(resolve, 4000));
      }
      if (confirm && confirm.status === "confirmed") {
        A.toast("Proof anchored with your MetaMask wallet. Reloading...", "success");
        setTimeout(() => window.location.reload(), 1200);
      } else {
        throw new Error("The transaction was mined but confirmation is still pending. Press Refresh status.");
      }
    } catch (err) {
      let message = err && err.message ? err.message : "MetaMask anchoring failed.";
      if (err && err.code === 4001) message = "You rejected the transaction in MetaMask.";
      else if (err && err.code === "ACTION_REJECTED") message = "You rejected the transaction in MetaMask.";
      else if (/user rejected/i.test(message)) message = "You rejected the transaction in MetaMask.";
      else if (/already exists/i.test(message)) message = "This record already has an on-chain proof.";
      showAnchorError(message);
      if (anchorHint) anchorHint.textContent = "MetaMask anchoring did not complete. You can retry or use the server recorder.";
    } finally {
      setBusy(metaBtn, false);
      setBusy(serverBtn, false);
    }
  }

  if (metaBtn) metaBtn.addEventListener("click", metamaskAnchor);
})();
