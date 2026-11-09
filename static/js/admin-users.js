/* Admin users: wallet authorisation and password reset helpers */
(function () {
  "use strict";
  const A = window.AQUALEDGER;

  const resetForm = document.getElementById("resetPasswordForm");
  const resetName = document.getElementById("resetPasswordName");

  document.querySelectorAll("[data-reset-user]").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (!resetForm) return;
      resetForm.action = "/admin/users/" + btn.getAttribute("data-reset-user") + "/password";
      resetForm.reset();
      if (resetName) resetName.textContent = btn.getAttribute("data-user-name") || "this user";
      A.openModal("resetPasswordModal");
    })
  );

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

  document.querySelectorAll("[data-wallet-auth]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const userId = Number(btn.getAttribute("data-user-id"));
      const allowed = btn.getAttribute("data-allowed") === "1";
      const name = btn.getAttribute("data-user-name") || "this user";
      const verb = allowed ? "authorise" : "revoke";
      if (!window.confirm("Are you sure you want to " + verb + " " + name + "'s wallet on the contract?")) return;

      setBusy(btn, true, allowed ? "Authorising..." : "Revoking...");
      try {
        const result = await A.api("/api/wallet/authorize", {
          method: "POST",
          body: { user_id: userId, allowed: allowed },
        });
        let message = result.message || ("Wallet " + verb + "d.");
        if (result.tx_hash && result.explorer) message += " Transaction submitted.";
        A.toast(message, "success", 9000);
        setTimeout(() => window.location.reload(), 1200);
      } catch (err) {
        A.toast(err.message, "danger", 9000);
        setBusy(btn, false);
      }
    })
  );
})();
