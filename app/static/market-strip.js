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
  const cards = [...rail.querySelectorAll("[data-market-item]")];
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const storageKey = "changhong-market-collapsed";
  let collapsed = false;
  try { collapsed = localStorage.getItem(storageKey) === "true"; } catch { /* Storage is optional. */ }

  function update() {
    const maximum = Math.max(0, rail.scrollWidth - rail.clientWidth);
    previous.disabled = rail.scrollLeft <= 1;
    next.disabled = rail.scrollLeft >= maximum - 1;
    const bounds = rail.getBoundingClientRect();
    const visible = cards.map((card, index) => ({ bounds: card.getBoundingClientRect(), index }))
      .filter(({ bounds: card }) => card.right > bounds.left + 2 && card.left < bounds.right - 2);
    range.textContent = visible.length ? `${visible[0].index + 1}–${visible.at(-1).index + 1} / ${cards.length}` : `${cards.length} 项`;
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
    // Leave trackpad horizontal gestures, Shift-scroll and browser zoom native.
    if (event.ctrlKey || event.shiftKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rail.clientWidth : 1;
    const delta = event.deltaY * unit;
    const maximum = rail.scrollWidth - rail.clientWidth;
    if (maximum <= 1 || (delta < 0 && rail.scrollLeft <= 1) || (delta > 0 && rail.scrollLeft >= maximum - 1)) return;
    event.preventDefault();
    rail.scrollBy({ left: delta, behavior: "instant" });
  }, { passive: false });
  rail.addEventListener("keydown", (event) => {
    if (event.target !== rail || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault(); move(event.key === "ArrowLeft" ? -1 : 1);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      rail.scrollTo({ left: event.key === "Home" ? 0 : rail.scrollWidth - rail.clientWidth,
        behavior: reduced.matches ? "instant" : "smooth" });
    }
  });

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
