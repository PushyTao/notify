"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = readFileSync(join(__dirname, "../app/static/market.js"), "utf8");
const names = ["上证指数", "深成指", "创业板指", "科创综指", "科创50", "沪深300", "中证1000", "证券板块", "银行板块"];

// Minimal browser boundary: the production renderer creates and changes these nodes.
class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.textContent = "";
    this.selectors = {};
  }
  querySelector(selector) { return this.selectors[selector] || null; }
  querySelectorAll(selector) { return this.selectors[selector] || []; }
  setAttribute(key, value) { this.attributes[key] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}

function card(name, total = false) {
  const element = new Element("article");
  element.dataset.marketName = name;
  for (const field of ["price", "change", "amount", "comparison", "date", "baseline", "source", "scope", "status"]) {
    element.selectors[`[data-market-field="${field}"]`] = new Element("span");
  }
  if (!total) element.selectors["[data-market-chart]"] = new Element();
  return element;
}

function quote(name) {
  return {
    key: name, name, code: "sample", price: 123.45, change_pct: 1.25,
    amount_yi: 456.78, delta_yi: 12.34, delta_pct: 2.78,
    trade_date: "2026-11-13", as_of: "09:42", previous_date: "2026-11-12",
    points: [{ time: "09:30", price: 120 }, { time: "09:31", price: 121 }],
    status: "ready", message: "", source: "test source", scope: "test scope",
    fetched_at: 1794534120, stale: false,
  };
}

function payload() {
  return {
    source: "test source", source_url: "https://example.com", fetched_at: 1794534120,
    stale: false, status: "ready", message: "", items: names.map(quote),
    total: { ...quote("沪深成交额"), price: null, change_pct: null, points: [] },
  };
}

async function browser(responses) {
  const panel = new Element("aside");
  const cards = names.map((name) => card(name));
  const total = card("沪深成交额", true);
  const status = new Element();
  const cache = new Element();
  panel.selectors = {
    "[data-market-item]": cards, "[data-market-total]": total,
    "[data-market-status]": status, "[data-market-cache]": cache,
    "[data-market-details]": new Element("details"),
  };
  const timers = new Map();
  let timerId = 0;
  const document = {
    hidden: false, querySelector: () => panel,
    createElement: (tag) => new Element(tag),
    createElementNS: (_namespace, tag) => new Element(tag),
    addEventListener() {},
  };
  vm.runInNewContext(source, {
    document, AbortController,
    window: {
      matchMedia: () => ({ matches: false, addEventListener() {} }),
      setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
      clearTimeout(id) { timers.delete(id); },
    },
    fetch: async () => ({ ok: true, json: async () => structuredClone(responses.shift()) }),
  });
  await new Promise(setImmediate);
  return {
    cards, total, status, cache,
    async refresh() {
      const pending = [...timers].find(([, timer]) => timer.delay === 60000);
      assert.ok(pending, "production refresh schedules the next update");
      timers.delete(pending[0]);
      await pending[1].callback();
    },
    snapshot() { return JSON.stringify({ cards, total, cache }); },
  };
}

const malformed = {
  "empty items and total": (data) => ({ ...data, items: [], total: {} }),
  "missing card name": (data) => { delete data.items[0].name; return data; },
  "duplicate card name": (data) => { data.items[0].name = data.items[1].name; return data; },
  "unknown card name": (data) => { data.items[0].name = "未知指数"; return data; },
  "array total": (data) => ({ ...data, total: [] }),
  "wrong total name": (data) => { data.total.name = "重叠指数合计"; return data; },
  "missing item status": (data) => { delete data.items[0].status; return data; },
  "invalid total status": (data) => { data.total.status = "partial"; return data; },
};
for (const [label, corrupt] of Object.entries(malformed)) {
  test(`preserves rendered quotes after HTTP 200 with ${label}`, async () => {
    const recovered = payload();
    recovered.items[0].price = 987.65;
    const page = await browser([payload(), corrupt(payload()), recovered]);
    assert.equal(page.cards[0].querySelector('[data-market-field="price"]').textContent, "123.45");
    assert.equal(page.total.querySelector('[data-market-field="amount"]').textContent, "456.78");
    const snapshot = page.snapshot();
    await page.refresh();
    assert.ok(page.snapshot() === snapshot, "all cards, chart nodes, total, and cache timestamp stay intact");
    assert.equal(page.status.dataset.state, "error");
    assert.match(page.status.textContent, /刷新失败.*保留上次行情/);
    await page.refresh();
    assert.equal(page.cards[0].querySelector('[data-market-field="price"]').textContent, "987.65");
    assert.equal(page.status.dataset.state, "ready");
  });
}

for (const state of ["partial", "unavailable"]) {
  test(`accepts complete ${state} placeholders without inventing zeroes`, async () => {
    const data = payload();
    data.status = state;
    const entries = state === "partial" ? [...data.items.slice(1), data.total] : [...data.items, data.total];
    entries.forEach((entry) => Object.assign(entry, {
      status: "unavailable", price: null, change_pct: null, amount_yi: null,
      delta_yi: null, delta_pct: null, trade_date: null, as_of: null,
      previous_date: null, points: [], fetched_at: null, stale: true,
    }));
    const page = await browser([payload(), data]);
    await page.refresh();
    assert.equal(page.cards[1].querySelector('[data-market-field="price"]').textContent, "—");
    assert.equal(page.cards[1].querySelector('[data-market-field="comparison"]').textContent, "暂无可比数据");
    assert.equal(page.total.querySelector('[data-market-field="amount"]').textContent, "—");
    assert.match(page.status.textContent, state === "partial" ? /部分行情可用/ : /等待行情更新/);
  });
}

test("shows every isolated real minute without drawing across missing intervals", async () => {
  const data = payload();
  data.items[0].points = [
    { time: "09:30", price: 120 }, { time: "09:36", price: 123 }, { time: "09:42", price: 122 },
  ];
  const page = await browser([data]);
  const svg = page.cards[0].querySelector("[data-market-chart]").children[0];
  const dots = svg.children.filter((node) => node.tag === "circle");
  assert.equal(dots.length, 3, "each real isolated minute has a visible circle");
  const expectedX = [8, 14.6, 21.2];
  dots.forEach((dot, index) => assert.ok(Math.abs(Number(dot.attributes.cx) - expectedX[index]) < 0.000001));
  assert.equal(new Set(dots.map((dot) => dot.attributes.cy)).size, 3, "all three prices retain their vertical positions");
  assert.ok(dots.every((dot) => Number(dot.attributes.r) > 0));
  const path = svg.children.find((node) => node.tag === "path");
  assert.doesNotMatch(path.attributes.d, /L/, "there is no invented price line across a missing interval");
});
