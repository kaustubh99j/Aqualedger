/* Dashboard charts */
(function () {
  "use strict";
  const A = window.AQUALEDGER;
  const dataEl = document.getElementById("dashboardData");
  const initial = dataEl ? JSON.parse(dataEl.textContent) : {};
  const dailyCanvas = document.getElementById("dailyChart");
  const monthlyCanvas = document.getElementById("monthlyChart");
  const budgetCanvas = document.getElementById("budgetChart");
  const select = document.getElementById("householdFilter");

  if (window.Chart) {
    Chart.defaults.font.family = "'Inter', 'Segoe UI', sans-serif";
    Chart.defaults.color = "#5f7a86";
    Chart.defaults.font.size = 11;
  }

  let days = 30;
  let dailyChart = null;
  let monthlyChart = null;
  let budgetChart = null;

  const centerText = {
    id: "centerText",
    afterDraw(chart) {
      if (!chart.config.options.plugins.centerText) return;
      const { ctx, chartArea } = chart;
      if (!chartArea) return;
      const text = chart.config.options.plugins.centerText;
      const x = (chartArea.left + chartArea.right) / 2;
      const y = (chartArea.top + chartArea.bottom) / 2;
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "#0a2735";
      ctx.font = "800 24px Manrope, sans-serif";
      ctx.fillText(text.value, x, y - 4);
      ctx.fillStyle = "#5f7a86";
      ctx.font = "600 10px Inter, sans-serif";
      ctx.fillText(text.label, x, y + 18);
      ctx.restore();
    },
  };

  function gradient(ctx, area, top, bottom) {
    const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
    g.addColorStop(0, top);
    g.addColorStop(1, bottom);
    return g;
  }

  function chartQuery(extra) {
    const params = new URLSearchParams(extra || {});
    if (select && select.value) params.set("household_id", select.value);
    return params.toString();
  }

  async function loadDaily() {
    if (!dailyCanvas) return;
    try {
      const data = await A.api("/api/charts/daily?days=" + days + "&" + chartQuery());
      const ctx = dailyCanvas.getContext("2d");
      const limit = Number(data.daily_limit_l) || 0;
      const values = data.values.map(Number);
      const limitSeries = values.map(() => limit);
      const labels = data.labels;
      if (dailyChart) dailyChart.destroy();
      dailyChart = new Chart(dailyCanvas, {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              label: "Consumption",
              data: values,
              borderColor: "#0e9488",
              borderWidth: 2.4,
              pointRadius: 0,
              pointHoverRadius: 4,
              pointHoverBackgroundColor: "#0e9488",
              tension: 0.35,
              fill: true,
              backgroundColor: (context) => {
                const chart = context.chart;
                const { ctx: c, chartArea } = chart;
                if (!chartArea) return "rgba(14,148,136,.1)";
                return gradient(c, chartArea, "rgba(34,211,238,.28)", "rgba(34,211,238,0)");
              },
            },
            {
              label: "Limit",
              data: limitSeries,
              borderColor: "#f59e0b",
              borderWidth: 1.4,
              borderDash: [5, 5],
              pointRadius: 0,
              fill: false,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: "#052c3d",
              padding: 10,
              cornerRadius: 8,
              titleFont: { size: 11 },
              bodyFont: { size: 11 },
              callbacks: {
                label: (item) => item.dataset.label + ": " + A.fmt(item.parsed.y, 0) + " L",
              },
            },
          },
          scales: {
            x: {
              grid: { display: false },
              ticks: { maxTicksLimit: days > 30 ? 10 : 8, maxRotation: 0 },
            },
            y: {
              beginAtZero: true,
              grid: { color: "#edf3f5", drawBorder: false },
              ticks: { callback: (v) => A.fmt(v, 0) },
            },
          },
        },
        plugins: [],
      });
      const total = document.getElementById("dailyTotal");
      if (total) total.textContent = "Total " + A.fmt(data.total_l, 0) + " L over " + days + " days";
      const sub = document.getElementById("dailySub");
      if (sub) sub.textContent = "Litres per day · limit " + A.fmt(limit, 0) + " L/day";
    } catch (err) {
      A.toast(err.message, "danger");
    }
  }

  async function loadMonthly() {
    if (!monthlyCanvas) return;
    try {
      const data = await A.api("/api/charts/monthly?months=12&" + chartQuery());
      const limit = Number(data.monthly_limit_l) || 0;
      if (monthlyChart) monthlyChart.destroy();
      monthlyChart = new Chart(monthlyCanvas, {
        type: "bar",
        data: {
          labels: data.labels.map((label) => {
            const [y, m] = label.split("-");
            return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("en-IN", { month: "short", year: "2-digit" });
          }),
          datasets: [
            {
              label: "Consumption",
              data: data.values,
              backgroundColor: "rgba(14,148,136,.72)",
              hoverBackgroundColor: "#0e9488",
              borderRadius: 6,
              maxBarThickness: 26,
            },
            {
              label: "Monthly limit",
              type: "line",
              data: data.values.map(() => limit),
              borderColor: "#f59e0b",
              borderWidth: 1.4,
              borderDash: [5, 5],
              pointRadius: 0,
              fill: false,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: "#052c3d",
              cornerRadius: 8,
              callbacks: { label: (item) => item.dataset.label + ": " + A.fmt(item.parsed.y, 0) + " L" },
            },
          },
          scales: {
            x: { grid: { display: false } },
            y: {
              beginAtZero: true,
              grid: { color: "#edf3f5", drawBorder: false },
              ticks: { callback: (v) => A.fmt(v, 0) },
            },
          },
        },
      });
    } catch (err) {
      A.toast(err.message, "danger");
    }
  }

  function loadBudget() {
    if (!budgetCanvas) return;
    const used = Number(initial.month_l) || 0;
    const limit = Number(initial.monthly_limit_l) || 0;
    const remaining = Math.max(limit - used, 0);
    const pct = limit ? Math.round((used / limit) * 100) : 0;
    budgetChart = new Chart(budgetCanvas, {
      type: "doughnut",
      data: {
        labels: ["Used", "Remaining"],
        datasets: [
          {
            data: limit ? [used, remaining] : [1, 0],
            backgroundColor: [pct > 100 ? "#dc2626" : pct > 80 ? "#f59e0b" : "#0e9488", "#eaf1f4"],
            borderWidth: 0,
            hoverOffset: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "72%",
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: limit > 0,
            backgroundColor: "#052c3d",
            callbacks: { label: (item) => item.label + ": " + A.fmt(item.parsed, 0) + " L" },
          },
          centerText: limit ? { value: pct + "%", label: "of monthly limit" } : { value: "--", label: "no limit" },
        },
      },
      plugins: [centerText],
    });
  }

  document.querySelectorAll("[data-days]").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-days]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      days = Number(btn.getAttribute("data-days")) || 30;
      loadDaily();
    })
  );

  if (select) select.addEventListener("change", () => { loadDaily(); loadMonthly(); });

  loadDaily();
  loadMonthly();
  loadBudget();
})();
