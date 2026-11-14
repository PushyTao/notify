"use strict";
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = readFileSync(join(__dirname, '../app/static/market-strip.js'), 'utf8');

class Element {
  constructor() { this.handlers = {}; this.dataset = {}; this.attributes = {}; this.hidden = false; this.disabled = false; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  emit(type, args = {}) {
    const event = { target: this, prevented: false, preventDefault() { this.prevented = true; }, ...args };
    this.handlers[type]?.(event); return event;
  }
}

function browser({ collapsed = false, reduced = false } = {}) {
  const panel = new Element(), rail = new Element(), content = new Element();
  const prev = new Element(), next = new Element(), toggle = new Element(), count = new Element();
  const dialog = new Element(), opener = new Element(), closer = new Element();
  dialog.open = false; dialog.showModal = () => { dialog.open = true; }; dialog.close = () => { dialog.open = false; };
  opener.closest = () => ({ querySelector: () => dialog });
  closer.closest = () => dialog;
  rail.scrollLeft = 0; rail.clientWidth = 600; rail.scrollWidth = 2000;
  rail.getBoundingClientRect = () => ({left: 0, right: 600});
  rail.scrollBy = ({left, behavior}) => { rail.behavior = behavior; rail.scrollLeft = Math.max(0, Math.min(1400, rail.scrollLeft + left)); rail.emit('scroll'); };
  rail.scrollTo = ({left, behavior}) => { rail.behavior = behavior; rail.scrollLeft = left; rail.emit('scroll'); };
  const cards = Array.from({length: 10}, (_, i) => ({ getBoundingClientRect: () => ({left: i*200-rail.scrollLeft, right: (i+1)*200-rail.scrollLeft}) }));
  const selectors = {'[data-market-rail]':rail, '[data-market-content]':content, '[data-market-prev]':prev, '[data-market-next]':next, '[data-market-toggle]':toggle, '[data-market-range]':count};
  panel.querySelector = s => selectors[s];
  panel.querySelectorAll = s => s === '[data-market-open]' ? [opener] : s === '[data-market-close]' ? [closer] : cards;
  rail.querySelectorAll = () => cards;
  panel.getBoundingClientRect = () => ({height: content.hidden ? 80 : 260});
  const storage = new Map(collapsed ? [['changhong-market-collapsed','true']] : []);
  vm.runInNewContext(source, {
    document: {querySelector: () => panel, documentElement: {style: {setProperty() {}}}},
    window: {matchMedia: () => ({matches: reduced}), addEventListener() {}},
    localStorage: {getItem:k => storage.get(k), setItem:(k,v) => storage.set(k,v)},
    ResizeObserver: class { constructor(fn) {this.fn=fn;} observe() { this.fn(); } },
  });
  return {panel, rail, content, prev, next, toggle, count, dialog, opener, closer, storage};
}

test('vertical wheel browses quotes only while horizontal room remains', () => {
  const page = browser();
  assert.equal(page.rail.emit('wheel', {deltaY:100, deltaX:0, deltaMode:0}).prevented, true);
  assert.equal(page.rail.scrollLeft, 100);
  page.rail.scrollLeft = 1400;
  assert.equal(page.rail.emit('wheel', {deltaY:100, deltaX:0, deltaMode:0}).prevented, false);
  page.rail.scrollLeft = 0;
  assert.equal(page.rail.emit('wheel', {deltaY:-100, deltaX:0, deltaMode:0}).prevented, false);
});

test('native horizontal gestures and browser zoom are left untouched', () => {
  const page = browser();
  assert.equal(page.rail.emit('wheel', {deltaY:2, deltaX:100}).prevented, false);
  assert.equal(page.rail.emit('wheel', {deltaY:100, deltaX:0, ctrlKey:true}).prevented, false);
  assert.equal(page.rail.scrollLeft, 0);
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
