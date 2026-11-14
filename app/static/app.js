"use strict";

const themePreference = (() => {
  const allowed = ["light", "dark", "system"];
  let value = "system";
  try {
    const saved = localStorage.getItem("notify-theme");
    if (allowed.includes(saved)) value = saved;
  } catch {
    // Private or restricted storage can be unavailable.
  }
  document.documentElement.dataset.theme = value;
  return {
    get value() { return value; },
    set value(next) {
      if (!allowed.includes(next)) return;
      value = next;
      document.documentElement.dataset.theme = value;
      try {
        localStorage.setItem("notify-theme", value);
      } catch {
        // The selected theme remains applied for this page.
      }
    },
  };
})();

async function notifyRequest(path, { method = "GET", body, csrf, redirectOnUnauthorized = false } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrf) headers["X-CSRF-Token"] = csrf;
  let response;
  try {
    response = await fetch(path, { method, headers, body: JSON.stringify(body) });
  } catch {
    throw new Error("网络连接失败，请重试。");
  }
  if (response.status === 401 && redirectOnUnauthorized) {
    window.location.assign("/admin");
    throw new Error("登录已过期，请重新登录。");
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = result.detail;
    if (Array.isArray(detail)) {
      const issues = detail.map(({ loc, msg }) => `${loc?.slice(1).join(".") || "输入"}：${msg}`);
      throw new Error(issues.join("；") || "请检查填写内容。");
    }
    throw new Error(typeof detail === "string" ? detail : `请求失败（${response.status}）。`);
  }
  return result;
}

document.addEventListener("DOMContentLoaded", () => {
  const themeControl = document.querySelector("[data-theme-control]");
  const themeSelect = document.getElementById("theme-select");
  themeSelect.value = themePreference.value;
  themeSelect.addEventListener("change", () => { themePreference.value = themeSelect.value; });
  themeControl.hidden = false;

  const login = document.getElementById("login-form");
  if (!login) return;
  const button = login.querySelector("button");
  const feedback = document.getElementById("login-feedback");
  try {
    const notice = sessionStorage.getItem("notify-login-message");
    if (notice) {
      sessionStorage.removeItem("notify-login-message");
      feedback.textContent = notice;
      feedback.hidden = false;
    }
  } catch {
    // The form remains available without session storage.
  }
  button.disabled = false;
  login.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    feedback.hidden = true;
    button.disabled = true;
    button.textContent = "登录中…";
    try {
      const result = await notifyRequest("/api/login", {
        method: "POST", body: { password: login.elements.namedItem("password").value },
      });
      if (!result.ok) throw new Error("登录失败，请重试。");
      window.location.assign("/admin");
    } catch (error) {
      feedback.textContent = error.message;
      feedback.dataset.kind = "error";
      feedback.hidden = false;
      button.textContent = "登录";
      button.disabled = false;
    }
  });
});
