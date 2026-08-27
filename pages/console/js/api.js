/* bridge-sdk 封装：统一等待 ready、错误转中文消息。 */

let ctx = null;

function friendly(err) {
  const msg = err && err.message ? String(err.message) : String(err);
  return msg || "请求失败";
}

export async function initBridge() {
  if (!window.AstrBotPluginPage) {
    throw new Error("未检测到 AstrBot bridge-sdk：请通过 WebUI 插件页面打开本页，而非直接打开文件");
  }
  ctx = await window.AstrBotPluginPage.ready();
  return ctx;
}

export function context() {
  return ctx;
}

export async function get(endpoint, params = {}) {
  try {
    return await window.AstrBotPluginPage.apiGet(endpoint, params);
  } catch (e) {
    throw new Error(friendly(e));
  }
}

export async function post(endpoint, body = {}) {
  try {
    return await window.AstrBotPluginPage.apiPost(endpoint, body);
  } catch (e) {
    throw new Error(friendly(e));
  }
}

export async function put(endpoint, body = {}) {
  // bridge 未提供 put 时以 _method 覆盖协商（Dashboard 支持 POST+覆盖头）；
  // 当前所有写端点均有 POST 版本可用，put 仅配置项使用。
  try {
    if (window.AstrBotPluginPage.apiPut) {
      return await window.AstrBotPluginPage.apiPut(endpoint, body);
    }
    return await window.AstrBotPluginPage.apiPost(endpoint, { ...body, _method: "PUT" });
  } catch (e) {
    throw new Error(friendly(e));
  }
}

export async function del(endpoint) {
  try {
    if (window.AstrBotPluginPage.apiDelete) {
      return await window.AstrBotPluginPage.apiDelete(endpoint);
    }
    return await window.AstrBotPluginPage.apiPost(endpoint, { _method: "DELETE" });
  } catch (e) {
    throw new Error(friendly(e));
  }
}

export async function upload(kind, file) {
  try {
    return await window.AstrBotPluginPage.upload(`uploads/${kind}`, file);
  } catch (e) {
    throw new Error(friendly(e));
  }
}

export async function importPending(row) {
  // 待确认导入执行：复用 confirm 端点，指定 kind 避免按文件名猜测
  return post("imports/confirm", { file_name: row.file_name, kind: row.kind });
}

export async function downloadExport(periodNo) {
  const params = periodNo ? { period_no: periodNo } : {};
  await window.AstrBotPluginPage.download("exports", params, "");
}
