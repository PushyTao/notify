"use strict";
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = readFileSync(join(__dirname, '../app/static/market-strip.js'), 'utf8');

class Element {
  constructor() { this.handlers = {}; this.dataset = {}; this.attributes = {}; this.hidden = false; this.disabled = false; this.children = []; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  append(...nodes) { nodes.forEach(node => this.insertBefore(node, null)); }
  insertBefore(node, before) {
    if (node === before) return;
    if (node.parentElement) node.parentElement.children.splice(node.parentElement.children.indexOf(node), 1);
    const index = before ? this.children.indexOf(before) : this.children.length;
    this.children.splice(index, 0, node); node.parentElement = this;
    // Moving a focused subtree with insertBefore/append loses browser focus.
    if (node.handle) node.handle.focused = false;
  }
  get nextElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this)+1] || null; }
  focus() { this.focused = true; }
  setPointerCapture(id) { this.capture = id; }
  hasPointerCapture(id) { return this.capture === id; }
  releasePointerCapture() { this.capture = null; }
  emit(type, args = {}) {
    const event = { target: this, prevented: false, preventDefault() { this.prevented = true; }, ...args };
    this.handlers[type]?.(event); return event;
  }
}

function browser({ collapsed = false, reduced = false, order = null } = {}) {
  const panel = new Element(), rail = new Element(), content = new Element();
  const prev = new Element(), next = new Element(), toggle = new Element(), count = new Element();
  const dialog = new Element(), opener = new Element(), closer = new Element();
  dialog.open = false; dialog.showModal = () => { dialog.open = true; }; dialog.close = () => { dialog.open = false; };
  opener.closest = () => ({ querySelector: () => dialog });
  closer.closest = () => dialog;
  rail.scrollLeft = 0; rail.clientWidth = 600; rail.scrollWidth = 2000;
  rail.getBoundingClientRect = () => ({left: 0, right: 600, top: 0, bottom: 200});
  rail.scrollBy = ({left, behavior}) => { rail.behavior = behavior; rail.scrollLeft = Math.max(0, Math.min(1400, rail.scrollLeft + left)); rail.emit('scroll'); };
  rail.scrollTo = ({left, behavior}) => { rail.behavior = behavior; rail.scrollLeft = left; rail.emit('scroll'); };
  const cards = Array.from({length: 10}, (_, i) => {
    const card = new Element(), handle = new Element();
    card.dataset.marketName = `card-${i}`;
    handle.closest = selector => selector === '[data-market-drag]' ? handle : selector === '[data-market-item]' ? card : null;
    card.closest = selector => selector === '[data-market-item]' ? card : null;
    card.querySelector = () => handle; card.handle = handle;
    card.getBoundingClientRect = () => { const index = rail.children.indexOf(card); return {left:index*200-rail.scrollLeft,right:(index+1)*200-rail.scrollLeft}; };
    return card;
  });
  rail.append(...cards);
  const announce = new Element();
  const selectors = {'[data-market-rail]':rail, '[data-market-content]':content, '[data-market-prev]':prev, '[data-market-next]':next, '[data-market-toggle]':toggle, '[data-market-range]':count, '[data-market-sort-status]':announce};
  panel.querySelector = s => selectors[s];
  panel.querySelectorAll = s => s === '[data-market-open]' ? [opener] : s === '[data-market-close]' ? [closer] : cards;
  rail.querySelectorAll = selector => selector === '[data-market-drag]' ? rail.children.map(card => card.handle) : rail.children;
  panel.getBoundingClientRect = () => ({height: content.hidden ? 80 : 260});
  const storage = new Map(collapsed ? [['changhong-market-collapsed','true']] : []);
  if (order !== null) storage.set('changhong-market-order', order);
  const frames = new Map(); let frameId = 0;
  vm.runInNewContext(source, {
    document: {querySelector: () => panel, documentElement: {style: {setProperty() {}}}},
    window: {matchMedia: () => ({matches: reduced}), addEventListener() {},
      requestAnimationFrame(fn) { frames.set(++frameId, fn); return frameId; },
      cancelAnimationFrame(id) { frames.delete(id); }},
    localStorage: {getItem:k => storage.get(k), setItem:(k,v) => storage.set(k,v)},
    ResizeObserver: class { constructor(fn) {this.fn=fn;} observe() { this.fn(); } },
  });
  return {panel, rail, content, prev, next, toggle, count, dialog, opener, closer, storage, cards, announce,
    order: () => rail.children.map(card => card.dataset.marketName),
    frame() { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn()); }, frames};
}

test('vertical wheel stays inside quotes at both ends', () => {
  const page = browser();
  assert.equal(page.rail.emit('wheel', {deltaY:100, deltaX:0, deltaMode:0}).prevented, true);
  assert.equal(page.rail.scrollLeft, 100);
  page.rail.scrollLeft = 1400;
  assert.equal(page.rail.emit('wheel', {deltaY:100, deltaX:0, deltaMode:0}).prevented, true);
  page.rail.scrollLeft = 0;
  assert.equal(page.rail.emit('wheel', {deltaY:-100, deltaX:0, deltaMode:0}).prevented, true);
});

test('horizontal and shift wheel stay inside quotes while browser zoom is untouched', () => {
  const page = browser();
  assert.equal(page.rail.emit('wheel', {deltaY:2, deltaX:100}).prevented, true);
  assert.equal(page.rail.scrollLeft, 100);
  assert.equal(page.rail.emit('wheel', {deltaY:100, deltaX:0, shiftKey:true}).prevented, true);
  assert.equal(page.rail.emit('wheel', {deltaY:100, deltaX:0, ctrlKey:true}).prevented, false);
  assert.equal(page.rail.scrollLeft, 200);
});

test('saved priorities restore known names once and append newly added cards', () => {
  const page = browser({order:JSON.stringify(['card-8','unknown','card-8','card-2'])});
  assert.deepEqual(page.order(), ['card-8','card-2','card-0','card-1','card-3','card-4','card-5','card-6','card-7','card-9']);
  assert.equal(browser({order:'invalid JSON'}).order()[0], 'card-0');
});

test('dragging a handle reorders and persists priorities', () => {
  const page = browser();
  page.rail.emit('pointerdown', {target:page.cards[0].handle,button:0,pointerId:1,isPrimary:true,clientX:20,clientY:30});
  page.rail.emit('pointermove', {pointerId:1,clientX:410,clientY:30});
  page.rail.emit('pointerup', {pointerId:1,clientX:410,clientY:30});
  assert.deepEqual(page.order().slice(0,3), ['card-1','card-0','card-2']);
  assert.deepEqual(JSON.parse(page.storage.get('changhong-market-order')), page.order());
  assert.equal(page.frames.size, 0);
  const restored = browser({order:page.storage.get('changhong-market-order')});
  assert.deepEqual(restored.order(), page.order());
});

test('cancelled drag restores original priority without overwriting storage', () => {
  const page = browser();
  const before = page.order();
  page.rail.emit('pointerdown', {target:page.cards[0].handle,button:0,pointerId:1,isPrimary:true,clientX:20,clientY:30});
  page.rail.emit('pointermove', {pointerId:1,clientX:410,clientY:30});
  page.rail.emit('pointercancel', {pointerId:1});
  assert.deepEqual(page.order(), before);
  assert.equal(page.storage.has('changhong-market-order'), false);
  assert.equal(page.frames.size, 0);
});

test('reordering preserves handle focus so Escape can cancel through the rail', () => {
  const page = browser();
  const handle = page.cards[0].handle;
  page.rail.emit('pointerdown', {target:handle,button:0,pointerId:1,isPrimary:true,clientX:20,clientY:30});
  page.rail.emit('pointermove', {pointerId:1,clientX:410,clientY:30});
  assert.equal(handle.focused, true);
  assert.equal(page.rail.emit('keydown', {target:handle,key:'Escape'}).prevented, true);
  assert.equal(page.order()[0], 'card-0');
  assert.equal(page.storage.has('changhong-market-order'), false);
});

test('dragging near the edge scrolls the rail and stops on release', () => {
  const page = browser();
  page.rail.emit('pointerdown', {target:page.cards[0].handle,button:0,pointerId:1,isPrimary:true,clientX:20,clientY:30});
  page.rail.emit('pointermove', {pointerId:1,clientX:590,clientY:30});
  page.frame(); assert.ok(page.rail.scrollLeft > 0);
  page.rail.emit('pointerup', {pointerId:1,clientX:590,clientY:30});
  assert.equal(page.frames.size, 0);
});

test('keyboard can move a priority to the front without starting a drag', () => {
  const page = browser();
  const handle = page.cards[8].handle;
  assert.equal(page.rail.emit('keydown', {target:handle,key:'Home'}).prevented, true);
  assert.equal(page.order()[0], 'card-8');
  assert.equal(handle.focused, true);
  assert.deepEqual(JSON.parse(page.storage.get('changhong-market-order')), page.order());
});

test('scrolling a detail dialog never moves the underlying quote rail', () => {
  const page = browser();
  const event = page.rail.emit('wheel', {deltaY:100, deltaX:0, target:{closest: () => page.dialog}});
  assert.equal(event.prevented, false);
  assert.equal(page.rail.scrollLeft, 0);
});

test('buttons and keyboard traverse the list with endpoint feedback', () => {
  const page = browser({reduced:true});
  assert.equal(page.prev.disabled, true);
  page.next.emit('click');
  assert.ok(page.rail.scrollLeft > 0); assert.equal(page.rail.behavior, 'instant');
  assert.equal(page.prev.disabled, false);
  page.rail.emit('keydown', {key:'End'});
  assert.equal(page.rail.scrollLeft, 1400); assert.equal(page.next.disabled, true);
  assert.match(page.count.textContent, /10/);
  page.rail.emit('keydown', {key:'Home'});
  assert.equal(page.rail.scrollLeft, 0);
});

test('collapse retains the total ribbon and remembers the choice', () => {
  const page = browser();
  page.toggle.emit('click');
  assert.equal(page.content.hidden, true);
  assert.equal(page.toggle.attributes['aria-expanded'], 'false');
  assert.equal(page.storage.get('changhong-market-collapsed'), 'true');
  page.toggle.emit('click');
  assert.equal(page.content.hidden, false);
  assert.equal(browser({collapsed:true}).content.hidden, true);
});

test('detail buttons open and close accessible native dialogs', () => {
  const page = browser();
  page.opener.emit('click'); assert.equal(page.dialog.open, true);
  page.closer.emit('click'); assert.equal(page.dialog.open, false);
});
