/* Manual reading entry helpers */
(function () {
  "use strict";
  const A = window.AQUALEDGER;
  const form = document.getElementById("readingForm");
  if (!form) return;
  const metaEl = document.getElementById("readingHouseholdMeta");
  const meta = metaEl ? JSON.parse(metaEl.textContent) : {};
  const household = document.getElementById("household_id");
  const dateInput = document.getElementById("reading_date");
  const prevInput = document.getElementById("previous_reading_m3");
  const currInput = document.getElementById("current_reading_m3");
  const calcText = document.getElementById("calcText");
  const latestPanel = document.getElementById("latestPanel");
  const latestHousehold = document.getElementById("latestHousehold");
  const prevHint = document.getElementById("previousHint");
  const dateHint = document.getElementById("dateHint");

  function localToday() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  function addDays(iso, days) {
    const d = new Date(iso + "T00:00:00");
    d.setDate(d.getDate() + days);
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  function fmtDate(iso) {
    if (!iso) return "--";
    return new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  }

  function currentMeta() {
    return meta[household.value] || null;
  }

  function renderLatest() {
    const info = currentMeta();
    if (!info) return;
    if (latestHousehold) latestHousehold.textContent = info.code + " · " + info.name;

    if (info.latest_current !== null && info.latest_current !== undefined) {
      prevInput.value = Number(info.latest_current).toFixed(3);
      prevInput.readOnly = true;
      prevInput.setAttribute("aria-readonly", "true");
      prevHint.textContent = "Locked to the latest recorded value (" + fmtDate(info.latest_date) + ").";
      latestPanel.innerHTML =
        '<div class="flex between"><span class="small muted">Last reading date</span><strong class="small">' + fmtDate(info.latest_date) + "</strong></div>" +
        '<div class="flex between"><span class="small muted">Current meter value</span><strong>' + Number(info.latest_current).toFixed(3) + " m³</strong></div>" +
        '<div class="flex between"><span class="small muted">Residents</span><strong>' + info.members + "</strong></div>" +
        '<div class="form-note" style="margin-top:4px">The next reading must be dated after ' + fmtDate(info.latest_date) + ".</div>";

      const minDate = addDays(info.latest_date, 1);
      dateInput.min = minDate;
      if (!dateInput.value || dateInput.value <= info.latest_date) {
        dateInput.value = minDate > localToday() ? minDate : localToday();
      }
      if (minDate > localToday()) {
        dateInput.value = minDate;
        dateHint.textContent = "A reading already exists for today or later dates; the earliest valid date is " + fmtDate(minDate) + ".";
      } else {
        dateHint.textContent = "Earliest valid date: " + fmtDate(minDate) + ".";
      }
    } else {
      prevInput.readOnly = false;
      prevInput.removeAttribute("aria-readonly");
      prevInput.value = "";
      prevHint.textContent = "First reading: enter the meter's starting value.";
      dateInput.min = "";
      dateInput.value = dateInput.value || localToday();
      dateHint.textContent = "This is the first reading for this household.";
      latestPanel.innerHTML =
        '<div class="empty" style="padding:14px 6px"><div class="empty-icon" style="width:44px;height:44px">' +
        '<svg class="icon" aria-hidden="true"><use href="#i-readings"></use></svg></div>' +
        '<h3 style="font-size:0.9rem">No readings yet</h3><p style="font-size:0.75rem">Enter the meter\'s current value as the starting point. The next reading will use it as the previous value.</p></div>';
    }
    updateCalc();
  }

  function updateCalc() {
    const info = currentMeta();
    const prev = prevInput.value === "" ? null : Number(prevInput.value);
    const curr = currInput.value === "" ? null : Number(currInput.value);
    if (prev === null || curr === null || !Number.isFinite(prev) || !Number.isFinite(curr)) {
      calcText.textContent = "Enter the current reading to see consumption.";
      currInput.setCustomValidity("");
      return;
    }
    const diff = (curr - prev) * 1000;
    if (diff < 0) {
      calcText.innerHTML = '<span style="color:var(--red)">Current reading is lower than the previous value. Enter a value of at least ' + prev.toFixed(3) + " m³.</span>";
      currInput.setCustomValidity("Current reading cannot be lower than the previous reading.");
      return;
    }
    const limit = info ? Number(info.members) * 135 : 0;
    const pct = limit ? Math.round(diff / limit * 100) : 0;
    calcText.innerHTML =
      "<strong>" + A.fmt(diff, 0) + " L</strong> consumed (" + (diff / 1000).toFixed(3) + " m³) · " +
      '<span class="' + (diff > 600 ? "muted" : "muted") + '">' +
      (limit ? pct + "% of the household's per-person target" : "") + "</span>";
    currInput.setCustomValidity("");
  }

  household.addEventListener("change", renderLatest);
  prevInput.addEventListener("input", updateCalc);
  currInput.addEventListener("input", updateCalc);

  form.addEventListener("submit", (event) => {
    const info = currentMeta();
    const prev = prevInput.value === "" ? null : Number(prevInput.value);
    const curr = currInput.value === "" ? null : Number(currInput.value);
    if (prev === null || !Number.isFinite(prev)) {
      event.preventDefault();
      A.toast("Enter the previous meter reading.", "danger");
      prevInput.focus();
      return;
    }
    if (curr === null || !Number.isFinite(curr)) {
      event.preventDefault();
      A.toast("Enter the current meter reading.", "danger");
      currInput.focus();
      return;
    }
    if (curr < prev) {
      event.preventDefault();
      A.toast("Current reading cannot be lower than the previous reading.", "danger");
      currInput.focus();
      return;
    }
    if (info && info.latest_date && dateInput.value <= info.latest_date) {
      event.preventDefault();
      A.toast("Choose a date after the latest reading (" + fmtDate(info.latest_date) + ").", "danger");
      dateInput.focus();
    }
  });

  if (!dateInput.value) dateInput.value = localToday();
  renderLatest();
})();
