"use strict";

(() => {
  const panel = document.querySelector("[data-market-panel]");
  if (!panel) return;
  const rail = panel.querySelector("[data-market-rail]");
  const content = panel.querySelector("[data-market-content]");
  const previous = panel.querySelector("[data-market-prev]");
  const next = panel.querySelector("[data-market-next]");
  const toggle = panel.querySelector("[data-market-toggle]");
  const range = panel.querySelector("[data-market-range]");
  const cards = () => [...rail.querySelectorAll("[data-market-item]")];
  const sortStatus = panel.querySelector("[data-market-sort-status]");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const storageKey = "changhong-market-collapsed";
  const orderKey = "changhong-market-order";
  const defaultCards = cards();
  try {
    const saved = JSON.parse(localStorage.getItem(orderKey));
    if (Array.isArray(saved)) {
      const byName = new Map(defaultCards.map(card => [card.dataset.marketName, card]));
      const names = [...new Set(saved.filter(name => typeof name === "string" && byName.has(name)))];
      rail.append(...names.map(name => byName.get(name)), ...defaultCards.filter(card => !names.includes(card.dataset.marketName)));
    }
  } catch { /* Malformed or unavailable storage leaves the default order intact. */ }
  let collapsed = false;
  try { collapsed = localStorage.getItem(storageKey) === "true"; } catch { /* Storage is optional. */ }

  function update() {
    const maximum = Math.max(0, rail.scrollWidth - rail.clientWidth);
    previous.disabled = rail.scrollLeft <= 1;
    next.disabled = rail.scrollLeft >= maximum - 1;
    const bounds = rail.getBoundingClientRect();
    const ordered = cards();
    const visible = ordered.map((card, index) => ({ bounds: card.getBoundingClientRect(), index }))
      .filter(({ bounds: card }) => card.right > bounds.left + 2 && card.left < bounds.right - 2);
    range.textContent = visible.length ? `${visible[0].index + 1}–${visible.at(-1).index + 1} / ${ordered.length}` : `${ordered.length} 项`;
    document.documentElement.style.setProperty("--market-sticky-height", `${panel.getBoundingClientRect().height + 16}px`);
  }

  function move(direction) {
    rail.scrollBy({ left: direction * rail.clientWidth, behavior: reduced.matches ? "instant" : "smooth" });
  }
  previous.addEventListener("click", () => move(-1));
  next.addEventListener("click", () => move(1));
  rail.addEventListener("scroll", update, { passive: true });
  rail.addEventListener("wheel", (event) => {
    // Modal details remain vertically scrollable even though their DOM lives in a card.
    if (event.target.closest?.("dialog")) return;
    // Consume the whole wheel gesture, including both endpoints; keep browser zoom native.
    if (event.ctrlKey) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rail.clientWidth : 1;
    const delta = (Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY) * unit;
    event.preventDefault();
    rail.scrollBy({ left: delta, behavior: "instant" });
  }, { passive: false });
  rail.addEventListener("keydown", (event) => {
    if (drag) {
      if (event.key === "Escape") { event.preventDefault(); finishDrag(false); }
      return;
    }
    const handle = event.target.closest?.("[data-market-drag]");
    if (handle && !event.altKey && !event.ctrlKey && !event.metaKey) {
      const ordered = cards(), card = handle.closest("[data-market-item]");
      const index = ordered.indexOf(card);
      const destination = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: ordered.length - 1 }[event.key];
      if (destination !== undefined) {
        event.preventDefault();
        const target = Math.max(0, Math.min(ordered.length - 1, destination));
        if (index !== target) {
          ordered.splice(index, 1); ordered.splice(target, 0, card);
          rail.append(...ordered); saveOrder(card); reveal(card);
        }
        handle.focus({ preventScroll: true }); update();
      }
      return;
    }
    if (event.target !== rail || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault(); move(event.key === "ArrowLeft" ? -1 : 1);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      rail.scrollTo({ left: event.key === "Home" ? 0 : rail.scrollWidth - rail.clientWidth,
        behavior: reduced.matches ? "instant" : "smooth" });
    }
  });

  function saveOrder(card) {
    const ordered = cards();
    let message = "顺序已保存到此浏览器";
    try { localStorage.setItem(orderKey, JSON.stringify(ordered.map(item => item.dataset.marketName))); }
    catch { message = "顺序已调整，本次浏览有效"; }
    sortStatus.textContent = `${card.dataset.marketName}已移至第 ${ordered.indexOf(card) + 1} 项；${message}`;
  }

  function reveal(card) {
    const bounds = rail.getBoundingClientRect(), box = card.getBoundingClientRect();
    const left = box.left < bounds.left ? box.left - bounds.left : box.right > bounds.right ? box.right - bounds.right : 0;
    if (left) rail.scrollBy({ left, behavior: "instant" });
  }

  let drag = null;
  let dragFrame;
  function arrangeDrag() {
    const bounds = rail.getBoundingClientRect();
    const x = Math.max(bounds.left + 1, Math.min(bounds.right - 1, drag.x));
    const before = cards().find(card => {
      const box = card.getBoundingClientRect();
      return card !== drag.card && x < (box.left + box.right) / 2;
    }) || null;
    if (drag.card.nextElementSibling !== before) {
      const left = rail.scrollLeft;
      rail.insertBefore(drag.card, before);
      rail.scrollLeft = left;
      // Moving a focused subtree with insertBefore can drop focus to body.
      drag.handle.focus({ preventScroll: true });
      update();
    }
  }

  function autoScroll() {
    if (!drag?.active) return;
    const bounds = rail.getBoundingClientRect();
    if (drag.y >= bounds.top - 32 && drag.y <= bounds.bottom + 32) {
      const edge = 48;
      const distance = drag.x < bounds.left + edge ? drag.x - bounds.left - edge
        : drag.x > bounds.right - edge ? drag.x - bounds.right + edge : 0;
      if (distance) {
        rail.scrollBy({ left: Math.max(-16, Math.min(16, distance / 3)), behavior: "instant" });
        arrangeDrag();
      }
    }
    dragFrame = window.requestAnimationFrame(autoScroll);
  }

  function finishDrag(commit) {
    if (!drag) return;
    const current = drag;
    drag = null;
    window.cancelAnimationFrame(dragFrame);
    delete rail.dataset.sorting;
    delete current.card.dataset.dragging;
    if (current.active) {
      if (commit) saveOrder(current.card);
      else {
        const left = rail.scrollLeft;
        rail.append(...current.original);
        rail.scrollLeft = left;
        sortStatus.textContent = "已取消拖动，保留原顺序";
      }
    }
    if (rail.hasPointerCapture(current.id)) rail.releasePointerCapture(current.id);
    current.handle.focus({ preventScroll: true });
    update();
  }

  rail.querySelectorAll("[data-market-drag]").forEach(handle => { handle.hidden = false; });
  rail.addEventListener("pointerdown", (event) => {
    const handle = event.target.closest?.("[data-market-drag]");
    if (!handle || drag || event.button !== 0 || event.isPrimary === false) return;
    event.preventDefault();
    handle.focus({ preventScroll: true });
    rail.setPointerCapture(event.pointerId);
    drag = { handle, card: handle.closest("[data-market-item]"), id: event.pointerId,
      startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY, active: false, original: cards() };
  });
  rail.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    drag.x = event.clientX; drag.y = event.clientY;
    if (!drag.active && Math.hypot(drag.x - drag.startX, drag.y - drag.startY) >= 6) {
      drag.active = true;
      drag.card.dataset.dragging = "true";
      rail.dataset.sorting = "true";
      sortStatus.textContent = `正在调整${drag.card.dataset.marketName}，松开保存，按 Esc 取消`;
      dragFrame = window.requestAnimationFrame(autoScroll);
    }
    if (drag.active) arrangeDrag();
  });
  rail.addEventListener("pointerup", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const bounds = rail.getBoundingClientRect();
    finishDrag(event.clientX >= bounds.left && event.clientX <= bounds.right
      && event.clientY >= bounds.top && event.clientY <= bounds.bottom);
  });
  rail.addEventListener("pointercancel", event => { if (drag?.id === event.pointerId) finishDrag(false); });
  rail.addEventListener("lostpointercapture", event => { if (drag?.id === event.pointerId) finishDrag(false); });

  function setCollapsed(value) {
    collapsed = value;
    content.hidden = value;
    previous.hidden = next.hidden = range.hidden = value;
    toggle.setAttribute("aria-expanded", String(!value));
    toggle.textContent = value ? "展开行情" : "收起行情";
    update();
  }
  toggle.hidden = false;
  toggle.addEventListener("click", () => {
    setCollapsed(!collapsed);
    try { localStorage.setItem(storageKey, String(collapsed)); } catch { /* Keep the current session usable. */ }
  });
  setCollapsed(collapsed);

  panel.querySelectorAll("[data-market-open]").forEach((button) => {
    button.hidden = false;
    button.addEventListener("click", () => {
      const dialog = button.closest("article").querySelector("dialog");
      if (!dialog.open) dialog.showModal();
    });
  });
  panel.querySelectorAll("[data-market-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
  new ResizeObserver(update).observe(panel);
  window.addEventListener("resize", update, { passive: true });
})();
