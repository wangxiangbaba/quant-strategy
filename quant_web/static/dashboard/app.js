const priceChart = echarts.init(document.getElementById("priceChart"));
const indicatorChart = echarts.init(document.getElementById("indicatorChart"));

function fmtMoney(v) {
  const n = Number(v || 0);
  return "¥" + n.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function levelTag(level) {
  const lv = level || "info";
  return `<span class="tag tag-${lv}">${lv}</span>`;
}

function toLocalTime(s) {
  return new Date(s).toLocaleString("zh-CN", { hour12: false });
}

function updateSummary(data) {
  const latest = data.latest_snapshot || {};
  document.getElementById("unreadAlerts").textContent = data.unread_alert_count || 0;
  document.getElementById("updatedAt").textContent = latest.created_at ? `更新时间 ${toLocalTime(latest.created_at)}` : "暂无数据";

  document.getElementById("balance").textContent = fmtMoney(latest.balance);
  document.getElementById("equity").textContent = fmtMoney(latest.equity);
  document.getElementById("available").textContent = fmtMoney(latest.available);
  document.getElementById("margin").textContent = fmtMoney(latest.margin);
  document.getElementById("floatProfit").textContent = fmtMoney(latest.float_profit);
  document.getElementById("curPos").textContent = latest.cur_pos ?? "--";
  document.getElementById("heldSymbols").textContent = latest.held_symbols || "无持仓";
  document.getElementById("symbol").textContent = latest.symbol || "--";
  document.getElementById("close").textContent = Number(latest.close || 0).toFixed(1);

  const alertsRows = (data.latest_alerts || []).map((row) => {
    const cls = row.level === "error" ? "row-error" : (row.level === "warn" ? "row-warn" : "");
    const btn = row.is_read ? "-" : `<button onclick="markAlertRead(${row.id})">已读</button>`;
    return `<tr class="${cls}">
      <td>${toLocalTime(row.created_at)}</td>
      <td>${levelTag(row.level)}</td>
      <td>${row.alert_type}</td>
      <td>${row.message}</td>
      <td>${btn}</td>
    </tr>`;
  }).join("");
  document.getElementById("alertsTable").innerHTML = alertsRows || "<tr><td colspan='5'>暂无告警</td></tr>";

  const eventRows = (data.latest_events || []).map((row) => {
    const cls = row.level === "error" ? "row-error" : (row.level === "warn" ? "row-warn" : "");
    return `<tr class="${cls}">
      <td>${toLocalTime(row.created_at)}</td>
      <td>${levelTag(row.level)}</td>
      <td>${row.event_type}</td>
      <td>${row.message}</td>
    </tr>`;
  }).join("");
  document.getElementById("eventsTable").innerHTML = eventRows || "<tr><td colspan='4'>暂无事件</td></tr>";
}

function updateCharts(series) {
  const x = series.map((r) => toLocalTime(r.created_at));
  const close = series.map((r) => Number(r.close || 0));
  const maS = series.map((r) => Number(r.ma_short || 0));
  const maL = series.map((r) => Number(r.ma_long || 0));
  const rsi = series.map((r) => Number(r.rsi || 0));
  const adx = series.map((r) => Number(r.adx || 0));
  const equity = series.map((r) => Number(r.equity || 0));

  priceChart.setOption({
    tooltip: { trigger: "axis" },
    legend: { data: ["close", "ma_short", "ma_long", "equity"] },
    xAxis: { type: "category", data: x },
    yAxis: [{ type: "value", scale: true }, { type: "value", scale: true }],
    series: [
      { name: "close", type: "line", data: close, smooth: true },
      { name: "ma_short", type: "line", data: maS, smooth: true },
      { name: "ma_long", type: "line", data: maL, smooth: true },
      { name: "equity", type: "line", yAxisIndex: 1, data: equity, smooth: true },
    ],
  });

  indicatorChart.setOption({
    tooltip: { trigger: "axis" },
    legend: { data: ["rsi", "adx"] },
    xAxis: { type: "category", data: x },
    yAxis: { type: "value", min: 0, max: 100 },
    series: [
      { name: "rsi", type: "line", data: rsi, smooth: true },
      { name: "adx", type: "line", data: adx, smooth: true },
    ],
  });
}

async function loadSummary() {
  const res = await fetch("/api/dashboard/summary");
  const data = await res.json();
  if (data.ok) updateSummary(data);
}

async function loadSeries() {
  const res = await fetch("/api/dashboard/timeseries?limit=200");
  const data = await res.json();
  if (data.ok) updateCharts(data.series || []);
}

async function markAlertRead(alertId) {
  await fetch(`/api/dashboard/alerts/${alertId}/read`, { method: "POST" });
  await loadSummary();
}
window.markAlertRead = markAlertRead;

async function refreshAll() {
  await Promise.all([loadSummary(), loadSeries()]);
}

refreshAll();
setInterval(refreshAll, 5000);
window.addEventListener("resize", () => {
  priceChart.resize();
  indicatorChart.resize();
});
