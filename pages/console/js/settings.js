/* 配置页：按功能域分组展示，逐项行内编辑（复用聊天侧同一套校验强转）。 */

import * as api from "./api.js";
import { el, esc, renderTable, toast, errorNote } from "./ui.js";

const BOOL_KEYS = new Set([
  "advance_default_carryover",
  "import_require_confirm",
  "notify_on_league_advance",
]);

export async function render(root, ctx) {
  root.appendChild(el(`<div>
    <h2 class="page-title">配置</h2>
    <p class="page-sub">与聊天命令 /成长 配置 完全同一份配置；保存立即生效</p>
    <div id="st-body"><div class="empty-state">加载中…</div></div>
  </div>`));
  const body = root.querySelector("#st-body");

  try {
    const r = await api.get("config");
    body.innerHTML = "";
    for (const g of r.groups) {
      const card = el(`<div class="card"><h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px"></h3></div>`);
      card.querySelector("h3").textContent = g.title;
      card.appendChild(
        renderTable(
          [
            { label: "键", render: (row) => `<span class="tag">${esc(row.key)}</span>` },
            {
              label: "值",
              render: (row) => editorFor(row),
            },
            { label: "", render: (row) => row.dirty ? '<button type="button" class="btn" data-save style="min-height:30px;padding:2px 14px;font-size:12.5px">保存</button>' : "" },
          ],
          g.items,
          ""
        )
      );
      body.appendChild(card);
    }

    function editorFor(item) {
      if (BOOL_KEYS.has(item.key)) {
        return `<label style="display:inline-flex;align-items:center;gap:6px">
          <input type="checkbox" data-edit="${esc(item.key)}" ${item.value ? "checked" : ""} style="width:auto">
        </label>`;
      }
      const v = Array.isArray(item.value)
        ? JSON.stringify(item.value)
        : String(item.value ?? "");
      return `<input type="text" data-edit="${esc(item.key)}" value="${esc(v)}" style="max-width:340px;display:inline-block" data-orig="${esc(v)}">`;
    }

    /* 委托监听：输入即标脏并显示保存按钮 */
    body.addEventListener("input", (ev) => {
      const inp = ev.target.closest("[data-edit]");
      if (!inp || inp.type === "checkbox") return;
      setDirty(inp);
    });
    body.addEventListener("change", (ev) => {
      const inp = ev.target.closest("[data-edit]");
      if (!inp) return;
      if (inp.type === "checkbox") saveItem(inp, inp.checked);
      else setDirty(inp);
    });
    body.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-save]");
      if (!btn) return;
      const tr = btn.closest("tr");
      const inp = tr && tr.querySelector("[data-edit]");
      if (!inp) return;
      await saveItem(inp, inp.value);
    });

    function setDirty(inp) {
      inp.dataset.dirty = "1";
      const tr = inp.closest("tr");
      const last = tr && tr.querySelector("td:last-child");
      if (last && !last.querySelector("[data-save]")) {
        last.innerHTML = '<button type="button" class="btn" data-save style="min-height:30px;padding:2px 14px;font-size:12.5px">保存</button>';
      }
    }

    async function saveItem(inp, rawValue) {
      try {
        const res = await api.put(`config/${encodeURIComponent(inp.dataset.edit)}`, { value: String(rawValue) });
        delete inp.dataset.dirty;
        if (inp.type !== "checkbox") {
          inp.value = res.formatted != null ? res.formatted : res.value;
          inp.dataset.orig = inp.value;
        }
        toast(`已保存 ${res.key}`);
        const tr = inp.closest("tr");
        if (tr) tr.querySelector("td:last-child").innerHTML = "";
      } catch (e) {
        toast(e.message, true);
      }
    }
  } catch (e) {
    body.innerHTML = "";
    body.appendChild(errorNote(e.message));
  }
  return null;
}
