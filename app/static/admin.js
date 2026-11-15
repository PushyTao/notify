"use strict";

const byId = (id) => document.getElementById(id);
const adminFeedback = byId("admin-feedback");
const refreshButton = byId("refresh");
const logoutButton = byId("logout");
const authorForm = byId("author-form");
const subscriberForm = byId("subscriber-form");
const modelForm = byId("model-form");
const promptForm = byId("prompt-form");
const subscriptionOptions = byId("subscription-options");
let csrf = document.querySelector('meta[name="csrf-token"]').content;
let state;

const configuration = {
  collector: byId("collector-form"),
  asr: byId("asr-form"),
  smtp: byId("smtp-form"),
  prompt: promptForm,
};
const asrController = createAsrFormController(configuration.asr, byId("asr-hint"));
const metrics = {
  authors: "监测博主",
  subscribers: "订阅者",
  videos: "已收录视频",
  pending: "待处理视频",
  failed: "处理失败视频",
};
const statuses = {
  pending: "待处理", retry: "等待重试", processing: "处理中",
  failed: "失败", complete: "已完成", sent: "已发送", cancelled: "已取消",
};
const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  dateStyle: "medium",
  timeStyle: "medium",
});

function displayTime(value, empty = "—") {
  return value == null ? empty : dateFormat.format(new Date(value * 1000));
}

function announce(message, kind = "success") {
  adminFeedback.textContent = message;
  adminFeedback.dataset.kind = kind;
  adminFeedback.hidden = false;
  if (kind === "error") adminFeedback.focus();
}

function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content != null) node.textContent = content;
  if (className) node.className = className;
  return node;
}

function controls(form) {
  return [...form.elements].filter((input) =>
    input.name && input.type !== "submit" && input.type !== "button"
  );
}

function formValues(form) {
  const values = {};
  for (const input of controls(form)) {
    if (input.type === "checkbox") values[input.name] = input.checked;
    else if (input.type === "number") values[input.name] = input.valueAsNumber;
    else values[input.name] = input.value;
  }
  return values;
}

function fillForm(form, values) {
  for (const input of controls(form)) {
    const value = values[input.name];
    if (input.type === "checkbox") input.checked = Boolean(value);
    else if (input.type === "password") input.value = "";
    else input.value = value ?? input.defaultValue ?? "";
  }
}

function checkedAuthors() {
  return [...subscriptionOptions.querySelectorAll("input:checked")].map((input) => Number(input.value));
}

function renderSubscriptionOptions(ids) {
  subscriptionOptions.replaceChildren();
  if (!state.authors.length) {
    subscriptionOptions.append(element("p", "当前没有可单独选择的博主。", "muted"));
    return;
  }
  for (const author of state.authors) {
    const label = element("label", null, "switch");
    const checkbox = element("input");
    checkbox.type = "checkbox";
    checkbox.value = author.id;
    checkbox.checked = ids.includes(Number(author.id));
    label.append(checkbox, document.createTextNode(author.name));
    subscriptionOptions.append(label);
  }
}

function applySettings(group) {
  const form = configuration[group];
  const settings = state.settings[group] ?? {};
  fillForm(form, group === "prompt" ? { text: settings.text || state.default_prompt } : settings);
  for (const input of form.querySelectorAll('input[type="password"]')) {
    const saved = settings[`${input.name}_set`];
    input.placeholder = saved ? "已配置，留空保留" : "未配置";
  }
  if (group === "asr") asrController.load(settings);
}

function run(button, work) {
  if (button.disabled) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  adminFeedback.hidden = true;
  return Promise.resolve()
    .then(work)
    .catch((error) => announce(error.message, "error"))
    .finally(() => {
      button.removeAttribute("aria-busy");
      button.disabled = false;
    });
}

function request(path, options = {}) {
  return notifyRequest(path, { ...options, csrf, redirectOnUnauthorized: true });
}

async function mutate(path, { method = "POST", body } = {}) {
  const result = await request(path, { method, body });
  if (result.ok === false) throw new Error(result.message || "操作失败。");
  return result;
}

function addButton(label, action, style = "") {
  const button = element("button", label, `button compact ${style}`);
  button.type = "button";
  button.addEventListener("click", () => run(button, action));
  return button;
}

function item(title, info, { error, status } = {}) {
  const entry = element("article", null, "list-item");
  const data = element("div", null, "item-data");
  data.append(element("h3", title));
  for (const line of info) data.append(element("p", line, "muted"));
  if (error) data.append(element("p", error, "item-error"));
  if (status) {
    const badge = element("span", statuses[status] || status, "badge");
    badge.dataset.status = status;
    data.append(badge);
  }
  const actions = element("div", null, "item-actions");
  entry.append(data, actions);
  return { entry, actions };
}

function renderList(id, entries, empty, build) {
  const list = byId(id);
  list.replaceChildren();
  if (entries.length) list.append(...entries.map(build));
  else list.append(element("p", empty, "list-empty"));
}

function editForm(form, values, label) {
  form.reset();
  form.dataset.id = values.id;
  fillForm(form, values);
  if (form === subscriberForm) renderSubscriptionOptions((values.author_ids ?? []).map(Number));
  if (form === modelForm) {
    byId("model-secret-status").textContent = values.api_key_set
      ? "已配置密钥。留空会保留现有密钥。"
      : "尚未配置密钥。";
  }
  byId(`${form.id}-heading`).textContent = label;
  form.querySelector('[type="submit"]').textContent = "保存修改";
  form.querySelector("[data-cancel]").hidden = false;
  controls(form)[0].focus();
}

function resetEditor(form, label) {
  form.reset();
  delete form.dataset.id;
  if (form === subscriberForm) renderSubscriptionOptions([]);
  if (form === modelForm) byId("model-secret-status").textContent = "编辑模型时，留空保留现有密钥。";
  byId(`${form.id}-heading`).textContent = label;
  form.querySelector('[type="submit"]').textContent = label;
  form.querySelector("[data-cancel]").hidden = true;
}

async function reloadAfterChange(message, afterRefresh) {
  try {
    await loadState();
    afterRefresh?.();
    announce(message);
  } catch (error) {
    announce(`${message} 刷新状态失败：${error.message}`, "error");
  }
}

function editorActions(resource, value, title, label, form) {
  const path = `/api/admin/${resource}/${value.id}`;
  return [
    addButton("编辑", () => editForm(form, value, `编辑${label}`)),
    addButton("删除", async () => {
      if (!window.confirm(`确定删除${label}「${title}」？`)) return;
      await mutate(path, { method: "DELETE" });
      if (form.dataset.id === String(value.id)) resetEditor(form, `添加${label}`);
      await reloadAfterChange(`已删除${label}。`);
    }, "danger"),
  ];
}

function renderAuthors() {
  renderList("author-list", state.authors, "尚未添加博主。", (author) => {
    const provider = author.provider === "generic" ? "通用数据 API" : "自部署采集";
    const interval = `${author.interval_seconds} 秒检查一次`;
    const initialized = author.initialized ? "已初始化" : "待初始化";
    const last = displayTime(author.last_polled, "尚未检查");
    const { entry, actions } = item(author.name, [
      author.source_id, `${author.enabled ? "已启用" : "已停用"} · ${provider} · ${interval}`,
      `${initialized} · 最近检查：${last}`,
    ], { error: author.error });
    const poll = addButton("立即检查", async () => {
      const result = await mutate(`/api/admin/authors/${author.id}/poll`);
      await reloadAfterChange(result.message || "已加入检查队列。");
    });
    poll.disabled = !author.enabled;
    actions.append(poll, ...editorActions("authors", author, author.name, "博主", authorForm));
    return entry;
  });
}

function renderSubscribers() {
  const names = new Map(state.authors.map((author) => [String(author.id), author.name]));
  renderList("subscriber-list", state.subscribers, "尚未添加订阅者。", (subscriber) => {
    const ids = subscriber.author_ids ?? [];
    const scope = ids.length ? ids.map((id) => names.get(String(id)) ?? `博主 #${id}`).join("、") : "全部博主";
    const { entry, actions } = item(subscriber.email, [`${subscriber.enabled ? "已启用" : "已停用"} · ${scope}`]);
    actions.append(...editorActions("subscribers", subscriber, subscriber.email, "订阅者", subscriberForm));
    return entry;
  });
}

function renderModels() {
  renderList("model-list", state.models, "尚未添加总结模型。", (model) => {
    const { entry, actions } = item(model.name, [
      `${model.enabled ? "当前模型" : "未启用"} · ${model.model_id}`,
      `${model.base_url} · ${model.api_key_set ? "已配置密钥" : "未配置密钥"} · 超时 ${model.timeout_seconds} 秒`,
    ]);
    actions.append(addButton("测试连接", async () => {
      const result = await request(`/api/admin/models/${model.id}/test`, { method: "POST" });
      const elapsed = Number.isFinite(result.elapsed_ms) ? `（${result.elapsed_ms} 毫秒）` : "";
      announce(`${result.message || (result.ok ? "连接成功" : "连接失败")}${elapsed}`, result.ok ? "success" : "error");
    }), ...editorActions("models", model, model.name, "模型", modelForm));
    return entry;
  });
}

function renderTasks() {
  function retryButton(resource, task, actions) {
    if (!["failed", "retry"].includes(task.status)) return;
    actions.append(addButton("重试", async () => {
      const result = await mutate(`/api/admin/${resource}/${task.id}/retry`);
      await reloadAfterChange(result.message || "已加入重试队列。");
    }));
  }
  renderList("video-list", state.videos, "暂无视频处理任务。", (video) => {
    const { entry, actions } = item(video.title || `视频 #${video.id}`, [
      `${video.author_name} · ${displayTime(video.published_at)} · 已尝试 ${video.attempts} 次`,
    ], video);
    retryButton("videos", video, actions);
    return entry;
  });
  renderList("delivery-list", state.deliveries, "暂无邮件投递任务。", (delivery) => {
    const { entry, actions } = item(delivery.email, [
      `视频 #${delivery.video_id} · 已尝试 ${delivery.attempts} 次`,
    ], delivery);
    retryButton("deliveries", delivery, actions);
    return entry;
  });
}

function renderOverview() {
  const metricNodes = Object.entries(metrics).map(([key, name]) => {
    const metric = element("div", null, "metric");
    metric.append(element("p", name, "muted"), element("strong", state.status.counts?.[key] ?? 0));
    return metric;
  });
  byId("metrics").replaceChildren(...metricNodes);
  byId("heartbeat").textContent = displayTime(state.status.worker_heartbeat, "尚未收到心跳");
}

async function loadState() {
  const ticket = ++loadState.generation;
  const next = await request("/api/admin/state");
  if (ticket !== loadState.generation) return;
  const firstLoad = !state;
  const selected = checkedAuthors();
  state = next;
  csrf = state.csrf ?? csrf;
  renderOverview();
  renderAuthors();
  renderSubscriptionOptions(selected);
  renderSubscribers();
  renderModels();
  renderTasks();
  if (firstLoad) Object.keys(configuration).forEach(applySettings);
  logoutButton.disabled = false;
}
loadState.generation = 0;

function bindSubmit(form, action, { requireState = true } = {}) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.submitter ?? form.querySelector('button[type="submit"]');
    run(button, () => {
      if (requireState && !state) throw new Error("请先刷新后台状态。");
      return action(form);
    });
  });
}

function bindEditor(form, resource, label, extend = () => ({})) {
  form.querySelector("[data-cancel]").addEventListener("click", () => resetEditor(form, `添加${label}`));
  bindSubmit(form, async () => {
    const id = form.dataset.id;
    const path = `/api/admin/${resource}${id ? `/${id}` : ""}`;
    const body = { ...formValues(form), ...extend() };
    await mutate(path, { method: id ? "PUT" : "POST", body });
    resetEditor(form, `添加${label}`);
    await reloadAfterChange(id ? `已更新${label}。` : `已添加${label}。`);
  });
}

bindEditor(authorForm, "authors", "博主");
bindEditor(subscriberForm, "subscribers", "订阅者", () => ({ author_ids: checkedAuthors() }));
bindEditor(modelForm, "models", "模型");

for (const [group, form] of Object.entries(configuration)) {
  bindSubmit(form, async () => {
    await mutate(`/api/admin/settings/${group}`, { method: "PUT", body: formValues(form) });
    for (const input of form.querySelectorAll('input[type="password"]')) input.value = "";
    await reloadAfterChange("已保存配置。", () => applySettings(group));
  });
}

byId("restore-prompt").addEventListener("click", () => {
  if (!state) {
    announce("请先刷新后台状态。", "error");
    return;
  }
  promptForm.elements.namedItem("text").value = state.default_prompt;
  promptForm.elements.namedItem("text").focus();
  announce("已载入默认提示词。点击保存以应用。");
});

bindSubmit(byId("cookie-form"), async (form) => {
  const result = await mutate("/api/admin/collector/cookie", { body: formValues(form) });
  form.reset();
  await reloadAfterChange(result.message || "已导入 Cookie。", () => {
    const identity = configuration.collector.elements.namedItem("identity_id");
    identity.value = result.identity_id ?? state.settings.collector.identity_id;
  });
});

bindSubmit(byId("email-form"), async (form) => {
  const result = await mutate("/api/admin/test-email", { body: formValues(form) });
  announce(result.message || "已发送测试邮件。");
});

bindSubmit(byId("password-form"), async (form) => {
  const result = await mutate("/api/admin/password", { body: formValues(form) });
  form.reset();
  if (result.csrf) csrf = result.csrf;
  else {
    try {
      sessionStorage.setItem("notify-login-message", result.message || "密码已修改，请重新登录。");
    } catch {
      // The login page is still available without session storage.
    }
    window.location.assign("/admin");
    return;
  }
  announce(result.message || "管理密码已更新。");
}, { requireState: false });

logoutButton.addEventListener("click", () => run(logoutButton, async () => {
  await mutate("/api/logout");
  window.location.assign("/admin");
}));

function refresh() {
  return run(refreshButton, loadState);
}

refreshButton.addEventListener("click", refresh);

const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(tab) {
  for (const current of tabs) {
    const active = current === tab;
    current.setAttribute("aria-selected", String(active));
    current.tabIndex = active ? 0 : -1;
    byId(current.getAttribute("aria-controls")).hidden = !active;
  }
}

for (const tab of tabs) {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    const index = tabs.indexOf(tab);
    const keys = {
      ArrowLeft: (index + tabs.length - 1) % tabs.length,
      ArrowRight: (index + 1) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    };
    if (!Object.hasOwn(keys, event.key)) return;
    event.preventDefault();
    const target = tabs[keys[event.key]];
    selectTab(target);
    target.focus();
  });
}

refresh();
