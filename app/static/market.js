"use strict";

(() => {
  const panel = document.querySelector("[data-market-panel]");
  if (!panel) return;
  const status = panel.querySelector("[data-market-status]");
  const cache = panel.querySelector("[data-market-cache]");
  const cards = [...panel.querySelectorAll("[data-market-item]")];
  const total = panel.querySelector("[data-market-total]");

  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const text = (value, fallback = "—") => typeof value === "string" && value.trim() ? value : fallback;
  const number = (value) => finite(value) ? value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
  const signed = (value) => finite(value) ? `${value > 0 ? "+" : ""}${number(value)}%` : "—";
  const tone = (value) => finite(value) ? value > 0 ? "market-up" : value < 0 ? "market-down" : "market-flat" : "market-flat";
  const field = (card, name, value, className) => {
    const element = card.querySelector(`[data-market-field="${name}"]`);
    if (!element) return;
    element.textContent = value;
    if (className) element.className = className;
  };
  const timestamp = (value) => {
    if (!finite(value)) return "—";
    const date = new Date(value * 1000);
    if (!Number.isFinite(date.getTime())) return "—";
    return date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
  };

  function comparison(item) {
    if (!finite(item.delta_yi)) return "暂无可比数据";
    const delta = item.delta_yi;
    if (delta === 0) return `持平 · 0.00 亿元（${finite(item.delta_pct) ? number(item.delta_pct) + "%" : "比例暂无可比数据"}）`;
    const amount = Math.abs(delta) < 0.005 ? "不足 0.01" : number(Math.abs(delta));
    const percent = finite(item.delta_pct) ? `${Math.abs(item.delta_pct) > 0 && Math.abs(item.delta_pct) < 0.005 ? "不足 0.01" : number(Math.abs(item.delta_pct))}%` : "比例暂无可比数据";
    return `${delta > 0 ? "放量" : "缩量"} ${amount} 亿元（${percent}）`;
  }

  // Both 11:30 and 13:00 sit at minute 120: the lunch break has no trading width.
  function tradingMinute(time) {
    if (typeof time !== "string" || !/^\d{2}:\d{2}$/.test(time)) return null;
    const [hour, minute] = time.split(":").map(Number);
    if (hour > 23 || minute > 59) return null;
    const value = hour * 60 + minute;
    if (value >= 570 && value <= 690) return value - 570;
    if (value >= 780 && value <= 900) return value - 660;
    return null;
  }

  function svgElement(tag, attributes = {}, value) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, attribute] of Object.entries(attributes)) element.setAttribute(key, String(attribute));
    if (value !== undefined) element.textContent = value;
    return element;
  }

  function validateResponse(data) {
    const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
    const item = (value) => object(value) && ["ready", "unavailable"].includes(value.status);
    if (!object(data) || !["ready", "partial", "unavailable"].includes(data.status)
        || !Array.isArray(data.items) || data.items.length !== cards.length
        || !data.items.every(item) || !item(data.total) || data.total.name !== total.dataset.marketName) {
      throw new Error("行情格式不可用");
    }
    const byName = new Map(data.items.map((value) => [value.name, value]));
    if (byName.size !== cards.length || cards.some((card) => !byName.has(card.dataset.marketName))) {
      throw new Error("行情格式不可用");
    }
    return byName;
  }

  function drawChart(card, item) {
    const container = card.querySelector("[data-market-chart]");
    if (!container) return;
    const points = (Array.isArray(item.points) ? item.points : [])
      .filter((point) => point && finite(point.price) && tradingMinute(point.time) !== null)
      .map((point) => ({ ...point, minute: tradingMinute(point.time) }))
      .sort((a, b) => a.minute - b.minute);
    if (!points.length) {
      const empty = document.createElement("p");
      empty.className = "market-chart-empty";
      empty.textContent = "暂无分时数据";
      container.replaceChildren(empty);
      return;
    }
    const prices = points.map((point) => point.price);
    const first = points[0];
    const low = Math.min(...prices);
    const high = Math.max(...prices);
    const padding = Math.max((high - low) * 0.15, Math.abs(first.price) * 0.0005, 0.01);
    const x = (minute) => 8 + minute / 240 * 264;
    const y = (price) => 28 + (high + padding - price) / (high - low + padding * 2) * 64;
    const svg = svgElement("svg", { viewBox: "0 18 280 80", role: "img", class: tone(item.change_pct), "aria-label": `${card.dataset.marketName} ${text(item.trade_date)}分时价格，首点基准 ${number(first.price)}，${first.time}至${points.at(-1).time}，午休不计交易时长` });
    svg.append(svgElement("line", { x1: 8, x2: 272, y1: y(first.price), y2: y(first.price), class: "chart-baseline" }));
    svg.append(svgElement("line", { x1: 140, x2: 140, y1: 24, y2: 95, class: "chart-grid" }));
    // Do not connect across missing minutes: incomplete data remains visibly incomplete.
    let path = "";
    points.forEach((point, index) => {
      const connected = index > 0 && point.minute - points[index - 1].minute <= 1;
      path += `${connected ? "L" : "M"}${x(point.minute).toFixed(2)},${y(point.price).toFixed(2)} `;
    });
    svg.append(svgElement("path", { d: path.trim(), class: "chart-line" }));
    points.forEach((point, index) => {
      const hasPrevious = index > 0 && point.minute - points[index - 1].minute <= 1;
      const hasNext = index + 1 < points.length && points[index + 1].minute - point.minute <= 1;
      if ((!hasPrevious && !hasNext) || index === points.length - 1) {
        svg.append(svgElement("circle", { cx: x(point.minute), cy: y(point.price), r: 2, class: "chart-dot" }));
      }
    });
    container.replaceChildren(svg);
  }

  function renderCard(card, item = {}) {
    field(card, "price", number(item.price));
    field(card, "change", signed(item.change_pct), tone(item.change_pct));
    field(card, "amount", number(item.amount_yi));
    field(card, "comparison", comparison(item), `market-comparison ${tone(item.delta_yi)}`);
    field(card, "date", `行情日期 ${text(item.trade_date)} · 截至 ${text(item.as_of)}（北京时间）`);
    field(card, "baseline", `同期基准 ${text(item.previous_date)} · ${text(item.as_of)}${item.as_of === "15:00" ? "（全天）" : ""}`);
    field(card, "source", `数据源 ${text(item.source)}`);
    field(card, "scope", text(item.scope, card === total ? "沪深 A 股独立统计，不含北交所与 B 股；不对各项行情求和。" : "统计口径待更新"));
    field(card, "brief", item.status === "ready"
      ? `${text(item.trade_date)} ${text(item.as_of)}${item.stale ? " · 已过期" : ""}` : "行情暂不可用",
      item.stale || item.status !== "ready" ? "market-brief market-warning" : "market-brief");
    const label = item.status === "ready" ? "缓存行情" : "行情不可用";
    field(card, "status", `${label}${item.stale ? " · 已过期" : ""} · 获取 ${timestamp(item.fetched_at)}${item.message ? " · " + text(item.message) : ""}`);
    drawChart(card, item);
  }

  let inFlight = false;
  let timer;
  let hasSnapshot = false;
  async function refresh() {
    if (document.hidden || inFlight) return;
    inFlight = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/api/market", { signal: controller.signal, headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error("行情请求失败");
      const data = await response.json();
      // Malformed payloads must not erase a previously successful snapshot.
      const byName = validateResponse(data);
      cards.forEach((card) => renderCard(card, byName.get(card.dataset.marketName)));
      renderCard(total, data.total);
      status.textContent = `${data.status === "unavailable" ? "等待行情更新" : data.status === "partial" ? "部分行情可用" : "缓存行情"}${data.stale ? " · 已过期" : ""}${data.message ? " · " + text(data.message) : ""}`;
      status.dataset.state = data.stale ? "error" : "ready";
      cache.textContent = `本站缓存 · 获取 ${timestamp(data.fetched_at)}（北京时间） · ${text(data.source, "数据源待更新")}`;
      hasSnapshot = true;
    } catch {
      status.textContent = hasSnapshot ? "刷新失败，保留上次行情 · 数据可能已过期" : "行情暂不可用 · 等待更新，稍后自动重试";
      status.dataset.state = "error";
    } finally {
      window.clearTimeout(timeout);
      inFlight = false;
      window.clearTimeout(timer);
      if (!document.hidden) timer = window.setTimeout(refresh, 60000);
    }
  }
  document.addEventListener("visibilitychange", () => {
    window.clearTimeout(timer);
    if (!document.hidden) refresh();
  });
  refresh();
})();
