/* Household create/edit modal */
(function () {
  "use strict";
  const A = window.AQUALEDGER;
  const form = document.getElementById("householdForm");
  if (!form) return;
  const title = document.getElementById("householdModalTitle");
  const label = document.getElementById("householdSubmitLabel");
  const createAction = form.getAttribute("action");

  const fields = {
    code: document.getElementById("hh_code"),
    name: document.getElementById("hh_name"),
    address: document.getElementById("hh_address"),
    city: document.getElementById("hh_city"),
    members: document.getElementById("hh_members"),
    meter: document.getElementById("hh_meter"),
    installed: document.getElementById("hh_installed"),
    user: document.getElementById("hh_user"),
  };

  function resetForm() {
    form.reset();
    form.action = createAction;
    if (fields.city) fields.city.value = "Pune";
    if (fields.members) fields.members.value = "4";
    if (title) title.textContent = "Add household";
    if (label) label.textContent = "Create household";
  }

  const createBtn = document.getElementById("createHouseholdBtn");
  if (createBtn) createBtn.addEventListener("click", resetForm);

  document.querySelectorAll("[data-edit-household]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const data = btn.dataset;
      form.action = "/households/" + data.id + "/update";
      if (title) title.textContent = "Edit " + data.code;
      if (label) label.textContent = "Save changes";
      fields.code.value = data.code || "";
      fields.name.value = data.name || "";
      fields.address.value = data.address || "";
      fields.city.value = data.city || "Pune";
      fields.members.value = data.members || "1";
      fields.meter.value = data.meter || "";
      fields.installed.value = data.installed || "";
      fields.user.value = data.user || "";
      A.openModal("householdModal");
    })
  );
})();
