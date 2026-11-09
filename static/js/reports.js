/* Reports: monthly consumption, limit and cost charts */
(function () {
  "use strict";
  const A = window.AQUALEDGER;
  const el = document.getElementById("reportsData");
  if (!el || typeof Chart === "undefined") return;
  const data = JSON.parse(el.textContent);

  const gridColor = "rgba(5, 44, 61, 0.07)";
  const tickColor = "#5f7a86";

  Chart.defaults.font.family = "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif";
  Chart.defaults.color = tickColor;

  const reportCanvas = document.getElementById("reportChart");
  if (reportCanvas) {
    new Chart(reportCanvas, {
      type: "bar",
      data: {
        labels: data.labels,
        datasets: [
          {
            label: "Consumption (L)",
            data: data.totals,
            backgroundColor: "rgba(14, 148, 136, 0.78)",
            hoverBackgroundColor: "#0e9488",
            borderRadius: 6,
            maxBarThickness: 34,
            order: 2,
          },
          {
            label: "Monthly limit (L)",
            data: data.limits,
            type: "line",
            borderColor: "#f59e0b",
            backgroundColor: "rgba(245, 158, 11, 0.14)",
            borderWidth: 2,
            borderDash: [6, 4],
            pointRadius: 0.5,
            pointHoverRadius: 4,
            tension: 0,
            order: 1,
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
            callbacks: {
              label: (ctx) => ctx.dataset.label + ": " + A.fmt(ctx.parsed.y, 0) + " L",
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
          y: {
            beginAtZero: true,
            grid: { color: gridColor },
            ticks: { callback: (value) => A.fmt(value, 0) },
            title: { display: true, text: "Litres" },
          },
        },
      },
    });
  }

  const costCanvas = document.getElementById("costChart");
  if (costCanvas) {
    const currency = (value) => "₹" + Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
    new Chart(costCanvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [
          {
            label: "Estimated cost (₹)",
            data: data.costs,
            borderColor: "#0891b2",
            backgroundColor: "rgba(8, 145, 178, 0.12)",
            fill: true,
            tension: 0.35,
            borderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 5,
            pointBackgroundColor: "#0891b2",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => "Estimated cost: " + currency(ctx.parsed.y),
              afterLabel: (ctx) => {
                const avg = data.avg_daily[ctx.dataIndex];
                return avg ? "Average use: " + A.fmt(avg, 1) + " L/day" : "";
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { maxRotation: 0 } },
          y: {
            beginAtZero: true,
            grid: { color: gridColor },
            ticks: { callback: (value) => currency(value) },
          },
        },
      },
    });
  }
})();
