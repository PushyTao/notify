"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const { createAsrFormController } = require("../app/static/asr.js");

function setup(values, api_key_set = false) {
  const fields = Object.fromEntries(["backend", "model", "base_url", "language", "api_key", "cache_dir"].map(name => {
    const label = { hidden: false };
    return [name, { value: values[name] || "", closest: () => label,
      addEventListener(event, callback) { this[event] = callback; } }];
  }));
  const hint = {};
  const control = createAsrFormController({ elements: { namedItem: name => fields[name] } }, hint);
  control.load({ ...values, api_key_set });
  const choose = backend => { fields.backend.value = backend; fields.backend.change(); };
  return { fields, hint, control, choose };
}

test("selecting MiMo supplies usable defaults and restores custom local settings", () => {
  const { fields, hint, choose } = setup({ backend: "local", model: "base", language: "zh", cache_dir: "/data/models" });
  assert.equal(fields.api_key.closest().hidden, true);
  choose("mimo");
  assert.equal(fields.model.value, "mimo-v2.5-asr");
  assert.equal(fields.base_url.value, "https://api.xiaomimimo.com/v1");
  assert.equal(fields.api_key.required, true);
  assert.equal(fields.cache_dir.closest().hidden, true);
  assert.match(hint.textContent, /60 秒/);
  fields.api_key.value = "mimo-secret";
  choose("local");
  assert.equal(fields.model.value, "base");
  assert.equal(fields.api_key.value, "");
  assert.equal(fields.api_key.required, false);
  assert.equal(fields.cache_dir.closest().hidden, false);
});

test("saved MiMo key can stay blank while switching providers clears entered credentials", () => {
  const { fields, choose } = setup({ backend: "mimo", model: "mimo-v2.5-asr", base_url: "https://gateway.example/v1", language: "auto" }, true);
  assert.equal(fields.api_key.required, false);
  assert.match(fields.api_key.placeholder, /留空保留/);
  choose("api");
  assert.equal(fields.model.value, "whisper-1");
  assert.equal(fields.base_url.required, true);
  assert.equal(fields.model.readOnly, false);
  fields.base_url.value = "https://transcribe.example/v1";
  fields.model.value = "custom-asr";
  fields.api_key.value = "different-secret";
  choose("mimo");
  assert.equal(fields.base_url.value, "https://gateway.example/v1");
  assert.equal(fields.language.value, "auto");
  assert.equal(fields.api_key.value, "");
  choose("api");
  assert.equal(fields.model.value, "custom-asr");
  assert.equal(fields.base_url.value, "https://transcribe.example/v1");
});
