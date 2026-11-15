"use strict";

function createAsrFormController(form, hint) {
  const field = (name) => form.elements.namedItem(name);
  const presets = {
    local: { model: "small", base_url: "", language: "zh" },
    api: { model: "whisper-1", base_url: "", language: "zh" },
    mimo: { model: "mimo-v2.5-asr", base_url: "https://api.xiaomimimo.com/v1", language: "zh" },
  };
  let current = field("backend").value;
  let savedBackend = current;
  let savedKey = false;
  let drafts = {};
  function update() {
    const local = current === "local";
    for (const name of ["base_url", "api_key"]) field(name).closest("label").hidden = local;
    field("cache_dir").closest("label").hidden = !local;
    field("model").readOnly = current === "mimo";
    field("base_url").required = current === "api";
    field("api_key").required = current === "mimo" && !(current === savedBackend && savedKey);
    field("api_key").placeholder = current === savedBackend && savedKey ? "已配置，留空保留" : "请输入当前服务的 API Key";
    field("language").placeholder = current === "mimo" ? "zh、en 或 auto；留空自动识别" : "例如 zh";
    hint.textContent = local
      ? "在服务器本地转写，首次使用会下载模型并缓存，无需 API Key。"
      : current === "mimo"
        ? "使用小米 MiMo 转写，无需下载本地模型。音频会发送至配置的服务，按 60 秒分段处理；切换方式后请填写对应密钥。"
        : "使用兼容的语音转写 API，请填写服务地址、模型和对应密钥。";
  }
  field("backend").addEventListener("change", () => {
    drafts[current] = Object.fromEntries(Object.keys(presets.local).map((name) => [name, field(name).value]));
    current = field("backend").value;
    for (const [name, value] of Object.entries(drafts[current] || presets[current])) field(name).value = value;
    field("api_key").value = "";
    update();
  });
  return {
    load(settings) {
      current = field("backend").value;
      savedBackend = current;
      savedKey = Boolean(settings.api_key_set);
      drafts = {};
      update();
    },
  };
}

if (typeof module !== "undefined") module.exports = { createAsrFormController };
