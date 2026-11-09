/* Household detail charts */
(function () {
  "use strict";
  const A = window.AQUALEDGER;
  const el = document.getElementById("householdChartData");
  if (!el || !window.Chart) return;
  const data = JSON.parse(el.textContent);

  Chart.defaults.font.family = "'Inter', 'Segoe UI', sans-serif";
  Chart.defaults.color = "#5f7a86";
  Chart.defaults.font.size = 11;

  const dailyCanvas = document.getElementById("houseDailyChart");
  if (dailyCanvas) {
    new Chart(dailyCanvas, {
      type: "line",
      data: {
        labels: data.daily_labels.map((d) => A.fmtDate(d, { day: "2-digit", month: "short" })),
        datasets: [
          {
            label: "Consumption",
            data: data.daily_values,
            borderColor: "#0e9488",
            borderWidth: 2.3,
            pointRadius: 0,
            tension: 0.34,
            fill: true,
            backgroundColor: (ctx) => {
              const chart = ctx.chart;
              const area = chart.chartArea;
              if (!area) return "rgba(14,148,136,.1)";
              const g = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
              g.addColorStop(0, "rgba(34,211,238,.3)");
              g.addColorStop(1, "rgba(34,211,238,0)");
              return g;
            },
          },
          {
            label: "Daily limit",
            data: data.daily_values.map(() => data.daily_limit),
            borderColor: "#f59e0b",
            borderDash: [5, 5],
            borderWidth: 1.4,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: "#052c3d", cornerRadius: 8 },
        },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
          y: { beginAtZero: true, grid: { color: "#edf3f5", drawBorder: false } },
        },
      },
    });
  }

  const monthlyCanvas = document.getElementById("houseMonthlyChart");
  if (monthlyCanvas) {
    new Chart(monthlyCanvas, {
      type: "bar",
      data: {
        labels: data.monthly_labels.map((label) => {
          const [y, m] = label.split("-");
          return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("en-IN", { month: "short", year: "2-digit" });
        }),
        datasets: [
          {
            data: data.monthly_values,
            backgroundColor: "rgba(14,148,136,.7)",
            hoverBackgroundColor: "#0e9488",
            borderRadius: 6,
            maxBarThickness: 28,
          },
          {
            type: "line",
            data: data.monthly_values.map(() => data.monthly_limit),
            borderColor: "#f59e0b",
            borderDash: [5, 5],
            borderWidth: 1.4,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { backgroundColor: "#052c3d", cornerRadius: 8 } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, grid: { color: "#edf3f5", drawBorder: false } },
        },
      },
    });
  }
})();
