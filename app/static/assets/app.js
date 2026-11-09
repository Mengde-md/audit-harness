"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { authenticated: false, sandboxAvailable: null, busy: false, listBusy: false, detailBusy: false, selected: null, offset: 0, limit: 10, total: 0, report: null, epoch: 0, rows: [] };
  const statusNames = { queued: "排队中", running: "执行中", passed: "已通过", failed: "未通过", error: "执行异常", interrupted: "已中断", skipped: "已跳过", pending: "等待中" };
  const riskNames = { low: "低风险", medium: "中等风险", high: "高风险", critical: "严重风险", unknown: "待评估" };
  const json = (value) => JSON.stringify(value, null, 2);
  const date = (value) => { const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? "—" : parsed.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }); };
  const node = (tag, className, text) => { const item = document.createElement(tag); if (className) item.className = className; if (text !== undefined) item.textContent = text; return item; };
  const badge = (status) => node("span", `badge ${Object.hasOwn(statusNames, status) ? status : ""}`, statusNames[status] || status || "未知");
  function notify(message = "") { $("global-message").textContent = message; $("global-message").hidden = !message; }
  function updateSubmit() { $("create-submit").disabled = state.busy || state.sandboxAvailable !== true; $("create-submit").textContent = state.busy ? "正在提交…" : state.sandboxAvailable === false ? "当前环境无法运行审计" : state.sandboxAvailable === null ? "等待运行环境确认" : "提交审计 ↗"; }
  function showGate(message = "") {
    state.authenticated = false; state.epoch += 1;
    $("workspace").hidden = true; $("access-gate").hidden = false;
    $("access-message").textContent = message; $("access-code").value = "";
    $("access-submit").disabled = false;
    $("access-code").focus();
  }
  async function request(path, options = {}, raw = false) {
    const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options, headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } });
    const body = await response.json().catch(() => null);
    if (response.status === 401 && path !== "/api/site-access") { showGate("访问已过期，请重新输入访问码"); throw new Error("请重新输入访问码"); }
    if (!response.ok || (!raw && body && Object.hasOwn(body, "code") && body.code !== 0)) {
      let message = body?.msg || body?.message || (response.status === 401 ? "访问码不正确" : `请求失败（${response.status}）`);
      if (body?.data?.errors?.length) message += "：" + body.data.errors.map((e) => `${e.loc.join(".")} ${e.msg}`).join("；");
      throw new Error(message);
    }
    if (!body) throw new Error("服务器返回了无法读取的内容");
    return raw ? body : body.data ?? body;
  }
  async function openWorkspace() {
    state.authenticated = true; state.epoch += 1;
    $("access-code").value = ""; $("access-gate").hidden = true; $("workspace").hidden = false;
    await Promise.allSettled([checkEnvironment(), loadList()]);
  }
  async function checkEnvironment() {
    if (!state.authenticated) return;
    const epoch = state.epoch;
    $("check-environment").disabled = true;
    try {
      const info = await request("/api/site-info", {}, true);
      if (!state.authenticated || epoch !== state.epoch) return;
      state.sandboxAvailable = info.sandbox_available === true;
      $("dependency").disabled = info.dependency_images_enabled === false;
      $("dependency-note").hidden = !$("dependency").disabled;
      $("capability-banner").classList.toggle("warning", !state.sandboxAvailable);
      $("capability-text").textContent = state.sandboxAvailable ? "运行环境已就绪 · 代码将在隔离沙箱中检查" : (info.sandbox_reason || "当前服务器未配置可用的隔离沙箱，暂不能提交执行。你仍可查看已有审计记录和报告。");
      $("model-label").textContent = info.llm_model ? `分析模型 · ${info.llm_model}` : "";
    } catch (error) {
      if (!state.authenticated || epoch !== state.epoch) return;
      state.sandboxAvailable = null;
      $("capability-banner").classList.add("warning");
      $("capability-text").textContent = "暂时无法确认审计运行环境，请重新检查。已有记录仍可查看。";
    } finally { $("check-environment").disabled = false; updateSubmit(); }
  }
  function renderList() {
    const container = $("audit-list"); container.replaceChildren();
    $("audit-total").textContent = state.total;
    $("page-label").textContent = `第 ${Math.floor(state.offset / state.limit) + 1} 页`;
    $("previous-page").disabled = state.offset === 0; $("next-page").disabled = state.offset + state.limit >= state.total;
    if (!state.rows.length) {
      const empty = node("div", "empty-state"); empty.append(node("span", "empty-icon", "▤"), node("h3", "", $("status-filter").value ? "当前筛选条件下没有任务" : "还没有审计记录"), node("p", "", "提交一个项目，检查记录会显示在这里。")); container.append(empty); return;
    }
    state.rows.forEach((row) => {
      const button = node("button", `audit-row${state.selected === row.audit_id ? " selected" : ""}`); button.type = "button"; button.setAttribute("aria-pressed", String(state.selected === row.audit_id));
      const body = node("div"); body.append(node("div", "audit-row-title", `${row.audit_mode === "change" ? "变更审计" : "完整审计"} · ${row.requester_id}`), node("div", "audit-row-id", row.audit_id), node("div", "audit-row-meta", `${date(row.created_at)} · ${riskNames[row.risk_level] || row.risk_level || "待评估"}`));
      const end = node("div", "audit-row-end"); end.append(badge(row.status), node("span", "row-arrow", "↗")); button.append(body, end); button.addEventListener("click", () => selectAudit(row.audit_id)); container.append(button);
    });
  }
  async function loadList() {
    if (!state.authenticated || state.listBusy) return;
    state.listBusy = true; const epoch = state.epoch;
    $("refresh-button").disabled = true;
    try {
      const params = new URLSearchParams({ limit: String(state.limit), offset: String(state.offset) }); if ($("status-filter").value) params.set("status", $("status-filter").value);
      const result = await request(`/api/v1/audits?${params}`);
      if (!state.authenticated || epoch !== state.epoch) return;
      state.rows = result.items || []; state.total = result.total || 0;
      if (state.offset > 0 && !state.rows.length) { state.offset = Math.max(0, Math.floor(Math.max(0, state.total - 1) / state.limit) * state.limit); }
      renderList(); $("refresh-status").textContent = `已更新 ${date(new Date().toISOString())}`;
    } catch (error) { if (state.authenticated && epoch === state.epoch) { $("refresh-status").textContent = "更新失败，稍后重试"; notify(error.message); } }
    finally { state.listBusy = false; $("refresh-button").disabled = false; }
  }
  function summaryCell(label, value) { const cell = node("div", "summary-cell"); cell.append(node("small", "", label), node("strong", "", value)); return cell; }
  function showSteps(steps) {
    const list = $("steps-list"); list.replaceChildren();
    if (!steps?.length) { list.append(node("p", "field-help", "尚无执行步骤。任务开始后将自动更新。")); return; }
    steps.forEach((step) => { const row = node("div", "step-row"); const body = node("div"); body.append(node("strong", "", step.name), node("code", "", step.command)); const tail = node("div"); tail.append(badge(step.status)); if (step.duration_ms !== null && step.duration_ms !== undefined) tail.append(node("small", "", `${(step.duration_ms / 1000).toFixed(1)} 秒`)); row.append(body, tail); list.append(row); });
  }
  async function selectAudit(id) {
    state.selected = id; state.report = null; $("download-report").disabled = true; $("artifact-content").hidden = true; $("artifact-content").textContent = ""; $("report-json").textContent = "正在读取报告…"; $("report-highlight").replaceChildren(); $("artifacts-list").replaceChildren(); renderList(); await loadDetail();
  }
  async function loadDetail() {
    if (!state.authenticated || !state.selected || state.detailBusy) return;
    state.detailBusy = true; const id = state.selected; const epoch = state.epoch;
    try {
      const [detail, reportResult, artifactsResult] = await Promise.all([request(`/api/v1/audits/${encodeURIComponent(id)}`), request(`/api/v1/audits/${encodeURIComponent(id)}/report`).then((value) => ({ value }), (error) => ({ error })), request(`/api/v1/audits/${encodeURIComponent(id)}/artifacts`).then((value) => ({ value }), (error) => ({ error }))]);
      if (id !== state.selected || epoch !== state.epoch || !state.authenticated) return;
      $("detail-empty").hidden = true; $("detail-content").hidden = false; $("detail-id").textContent = id; $("detail-status").replaceChildren(badge(detail.status));
      $("detail-summary").replaceChildren(summaryCell("审计范围", detail.audit_mode === "change" ? "代码变更" : "完整项目"), summaryCell("被审项目模型请求", detail.llm_policy === "mock" ? "模拟响应" : "禁用"), summaryCell("发起时间", date(detail.created_at)));
      showSteps(detail.steps);
      if (reportResult.value) { state.report = reportResult.value; $("report-json").textContent = json(state.report); $("download-report").disabled = false; renderHighlight(state.report); }
      else { state.report = null; $("download-report").disabled = true; $("report-json").textContent = reportResult.error.message; }
      renderArtifacts(artifactsResult.value?.artifacts, artifactsResult.error);
    } catch (error) { if (state.authenticated && id === state.selected && epoch === state.epoch) notify(error.message); }
    finally { state.detailBusy = false; }
  }
  function renderHighlight(report) {
    const area = $("report-highlight"); area.replaceChildren();
    const terminal = ["passed", "failed", "error", "interrupted"].includes(report.status);
    let text = terminal ? `审计${report.passed ? "通过" : "尚未通过"} · ${riskNames[report.risk_level] || report.risk_level || "待评估"}` : "任务正在排队或执行中，下方报告会随检查进度更新。";
    if (report.error) text += ` · ${report.error.message || report.error.type || "执行异常"}`;
    const notice = node("p", `report-notice${report.error ? " error" : ""}`, text); area.append(notice);
    if (report.project_config_warnings?.length) area.append(node("p", "report-notice", report.project_config_warnings.join("；")));
  }
  function renderArtifacts(artifacts, error) {
    const list = $("artifacts-list"); list.replaceChildren();
    if (error) { list.append(node("p", "field-help", error.message)); return; }
    if (!artifacts?.length) { list.append(node("p", "field-help", "尚未生成文件制品。")); return; }
    artifacts.forEach((artifact) => { const button = node("button", "artifact-button"); button.type = "button"; button.append(node("strong", "", artifact.artifact_type), node("span", "", "查看内容 ↗")); button.addEventListener("click", () => loadArtifact(artifact.artifact_type, button)); list.append(button); });
  }
  async function loadArtifact(type, button) {
    const id = state.selected; const epoch = state.epoch; button.disabled = true;
    try { const result = await request(`/api/v1/audits/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(type)}`); if (id !== state.selected || epoch !== state.epoch) return; $("artifact-content").textContent = result.content; $("artifact-content").hidden = false; }
    catch (error) { if (id === state.selected && epoch === state.epoch) notify(error.message); } finally { button.disabled = false; }
  }
  function parseField(id, label, kind) { const text = $(id).value.trim(); if (!text) return undefined; let result; try { result = JSON.parse(text); } catch { throw new Error(`${label}不是有效的 JSON`); } if ((kind === "array" && !Array.isArray(result)) || (kind === "object" && (result === null || Array.isArray(result) || typeof result !== "object"))) throw new Error(`${label}必须是 JSON ${kind === "array" ? "数组" : "对象"}`); return result; }
  function buildRequest() {
    const repo = $("repo").value.trim(); let parsed; try { parsed = new URL(repo); } catch { throw new Error("请填写有效的 HTTPS Git 仓库地址"); }
    let pathParts;
    try { pathParts = decodeURIComponent(repo.match(/^https:\/\/[^/?#]+([^?#]*)/i)?.[1] || "").split("/"); } catch { throw new Error("仓库地址包含无效的路径编码"); }
    if (parsed.protocol !== "https:" || !["github.com", "gitlab.com", "gitee.com"].includes(parsed.hostname) || parsed.username || parsed.password || (parsed.port && parsed.port !== "443") || parsed.search || parsed.hash || pathParts.filter(Boolean).length < 2 || pathParts.some((part) => part === "." || part === "..") || /[\\\n\r\u0000]/.test(repo)) throw new Error("请使用 github.com、gitlab.com 或 gitee.com 的公开 HTTPS 仓库地址，不含凭证、非标准端口、查询参数或片段");
    const mode = $("audit-mode").value; const policy = $("llm-policy").value;
    const advanced = parseField("advanced-json", "其他请求配置", "object") || {};
    const allowed = new Set(["env_files", "llm_adapters", "services", "llm_mock_profile", "llm_mock_rules", "llm_mock_fallback"]);
    for (const key of Object.keys(advanced)) if (!allowed.has(key)) throw new Error(`其他请求配置不支持 ${key}，请使用对应表单字段`);
    if (policy === "disabled" && Object.keys(advanced).some((key) => key.startsWith("llm_mock_"))) throw new Error("禁用模型请求时，请移除自定义模拟规则和模拟配置");
    const payload = { ...advanced, requester_id: $("requester").value.trim(), repo, audit_mode: mode, llm_policy: policy, api_document_markdown: $("api-document").value.trim(), runtime: { language: "python", version: $("runtime").value }, profile: "python-basic" };
    const configPath = $("project-config").value.trim(); if (configPath && configPath !== ".audit-harness/project.json") payload.project_config = configPath;
    if (!payload.requester_id || !payload.api_document_markdown) throw new Error("请填写发起人和 API 接口文档");
    if (mode === "change") { payload.base_commit = $("base-commit").value.trim(); payload.patch = $("patch").value; if (!/^[0-9a-fA-F]{7,64}$/.test(payload.base_commit)) throw new Error("基准提交必须是 7–64 位十六进制 SHA，不支持分支名"); if (!payload.patch.trim()) throw new Error("变更审计需要填写变更补丁"); }
    const manifest = $("dependency").disabled ? "" : $("dependency").value.trim(); if (manifest) payload.dependency = { type: "python", manifest };
    const startup = $("startup-command").value.trim(); const port = $("service-port").value;
    if (!startup || !port) throw new Error("请填写项目实际的服务启动命令和端口，远程仓库不能在提交时自动读取配置");
    if (!Number.isInteger(Number(port)) || Number(port) < 1 || Number(port) > 65535) throw new Error("服务端口必须是 1–65535 之间的整数");
    payload.startup_command = startup; payload.service_port = Number(port);
    const setup = $("setup-commands").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean); if (setup.length > 10 || setup.some((value) => value.length > 500)) throw new Error("准备命令最多 10 条，每条不超过 500 字符"); if (setup.length) payload.setup_commands = setup;
    const checks = parseField("checks-json", "自定义检查", "array"); if (checks) { if (!checks.length || checks.some((check) => !check || typeof check.name !== "string" || !check.name.trim() || typeof check.command !== "string" || !check.command.trim())) throw new Error("每条自定义检查都需要 name 和 command"); payload.checks = checks; payload.profile = "custom"; }
    return payload;
  }
  $("access-form").addEventListener("submit", async (event) => { event.preventDefault(); $("access-submit").disabled = true; $("access-message").textContent = "正在验证…"; try { const result = await request("/api/site-access", { method: "POST", body: json({ code: $("access-code").value }) }, true); if (!result.granted) throw new Error("访问码不正确"); await openWorkspace(); } catch (error) { $("access-message").textContent = error.message; $("access-code").select(); } finally { $("access-submit").disabled = false; } });
  $("lock-button").addEventListener("click", async () => { try { await request("/api/site-access", { method: "DELETE" }, true); } catch (error) { notify(`锁定失败：${error.message}`); return; } showGate(); });
  $("audit-mode").addEventListener("change", () => { const change = $("audit-mode").value === "change"; $("change-fields").hidden = !change; $("base-commit").required = change; $("patch").required = change; });
  $("audit-form").addEventListener("submit", async (event) => {
    event.preventDefault(); if (state.busy || state.sandboxAvailable !== true) return;
    let payload; try { payload = buildRequest(); } catch (error) { $("create-message").textContent = error.message; return; }
    state.busy = true; updateSubmit(); $("create-message").textContent = "正在提交任务…"; notify(); const epoch = state.epoch;
    try { const result = await request("/api/v1/audits", { method: "POST", body: json(payload) }); if (!state.authenticated || epoch !== state.epoch) return; $("create-message").textContent = "审计任务已提交，可在右侧查看进度。"; $("status-filter").value = ""; state.offset = 0; await loadList(); await selectAudit(result.audit_id); }
    catch (error) { if (epoch === state.epoch) $("create-message").textContent = error.message; }
    finally { state.busy = false; updateSubmit(); }
  });
  $("refresh-button").addEventListener("click", async () => { notify(); await Promise.allSettled([loadList(), loadDetail()]); });
  $("check-environment").addEventListener("click", checkEnvironment);
  $("status-filter").addEventListener("change", () => { state.offset = 0; loadList(); });
  $("previous-page").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); loadList(); });
  $("next-page").addEventListener("click", () => { if (state.offset + state.limit < state.total) { state.offset += state.limit; loadList(); } });
  function tab(name) { for (const entry of ["report", "artifacts"]) { const active = entry === name; $(`${entry}-tab`).setAttribute("aria-selected", String(active)); $(`${entry}-view`).hidden = !active; } }
  $("report-tab").addEventListener("click", () => tab("report")); $("artifacts-tab").addEventListener("click", () => tab("artifacts"));
  $("download-report").addEventListener("click", () => { if (!state.report) return; const url = URL.createObjectURL(new Blob([json(state.report)], { type: "application/json;charset=utf-8" })); const link = node("a"); link.href = url; link.download = `audit-${state.selected}.json`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); });
  setInterval(() => { if (state.authenticated && !document.hidden) { loadList(); loadDetail(); } }, 4000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.authenticated) { loadList(); loadDetail(); } });
  request("/api/site-access", {}, true).then((result) => result.granted || result.required === false ? openWorkspace() : showGate()).catch((error) => showGate(error.message));
})();
