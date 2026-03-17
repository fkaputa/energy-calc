// Fetch high-resolution chart data for a given date window and refresh plots.
window.updateChartsForWindow = async function (projectName, startDay, endDay) {
  const params = new URLSearchParams({ start: startDay, end: endDay });
  const url = `/project/${encodeURIComponent(projectName)}/chart-data/?` + params.toString();
  try {
    const resp = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
    if (!resp.ok) return;
    const payload = await resp.json();
    const chartData = payload.chart_data || {};
    const container = document.getElementById('charts-container');
    if (!container) return;
    // Replace global chartData used by inline script if present
    window.__vizChartData = chartData;
    // Let inline script rebuild plots on next load; simplest is to reload page for now
    window.location.reload();
  } catch (e) {
    console.error("Failed to update charts for window", e);
  }
};

