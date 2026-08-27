/* 导入页：上传 → 预览 → 确认 三步流 + 待确认列表 + 文件直导。 */

import * as api from "./api.js";
import { el, esc, renderTable, confirmDialog, toast, errorNote } from "./ui.js";

const KIND_NAME = { rule: "规则", players: "球员", matches: "比赛" };

export async function render(root, ctx) {
  root.appendChild(el(`<div>
    <h2 class="page-title">导入</h2>
    <p class="page-sub">规则 / 球员名册 / 比赛数据 的 Excel·CSV·JSON 导入；先预览，后确认</p>
    <div id="im-body"></div>
  </div>`));
  const body = root.querySelector("#im-body");

  const uploadCard = el(`<div class="card">
    <h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">① 上传文件</h3>
    <div class="form-row">
      <label class="field" style="flex:0 0 auto"><span>类型</span>
        <select id="im-kind">
          <option value="rule">成长规则（.json/.xlsx/.csv）</option>
          <option value="players">球员名册（.xlsx/.csv）</option>
          <option value="matches">比赛数据（.xlsx/.csv）</option>
        </select>
      </label>
      <label class="field" style="flex:1 1 220px"><span>文件</span>
        <input type="file" id="im-file" accept=".json,.xlsx,.csv">
      </label>
      <button type="button" class="btn" id="im-upload">上传并生成预览</button>
    </div>
    <p style="color:var(--muted);font-size:12.5px;margin:8px 0 0">文件名无需特定前缀；上传后立即生成预览，不会写入数据。</p>
  </div>`);
  body.appendChild(uploadCard);

  uploadCard.querySelector("#im-upload").addEventListener("click", () => doUpload(ctx));

  const flowCard = el(`<div class="card"><h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">② 预览与确认</h3><div id="im-flow"><div class="empty-state">尚未上传文件<div class="hint">上传后在待确认列表中逐条预览、确认或驳回</div></div></div></div>`);
  body.appendChild(flowCard);
  const flowZone = flowCard.querySelector("#im-flow");

  const listCard = el(`<div class="card"><h3 style="margin:0 0 10px;font-family:var(--serif);font-size:15px">③ 待确认与历史</h3><div id="im-list"><div class="empty-state">加载中…</div></div></div>`);
  body.appendChild(listCard);

  let reloadList = null;

  async function doUpload(rc) {
    const kind = uploadCard.querySelector("#im-kind").value;
    const fileInput = uploadCard.querySelector("#im-file");
    const file = fileInput.files && fileInput.files[0];
    if (!file) { toast("请先选择文件", true); return; }
    try {
      const r = await api.upload(kind, file);
      toast("已上传并生成预览");
      fileInput.value = "";
      showFlow(r, rc);
      if (reloadList) reloadList();
    } catch (e) {
      toast(e.message, true);
    }
  }

  function showFlow(r, rc) {
    flowZone.innerHTML = "";
    const head = el(`<p style="margin:4px 0 8px;font-size:13.5px"></p>`);
    head.innerHTML = `<span class="tag ok">${esc(KIND_NAME[r.kind] || r.kind)}</span> <b>${esc(r.file_name)}</b>`;
    flowZone.appendChild(head);
    const pre = el(`<pre class="preview"></pre>`);
    pre.textContent = r.preview || "（无预览内容）";
    flowZone.appendChild(pre);
    const btnRow = el(`<div style="display:flex;gap:10px;margin-top:10px">
      <button type="button" class="btn" data-act="confirm">确认导入「${esc(KIND_NAME[r.kind] || r.kind)}」数据</button>
      <button type="button" class="btn danger" data-act="reject">丢弃</button>
    </div>`);
    btnRow.querySelector('[data-act="confirm"]').addEventListener("click", async () => {
      try {
        const res = await api.post("imports/confirm", { file_name: r.file_name, kind: r.kind });
        const summary =
          res.added != null
            ? `新增 ${res.added} 名、更新 ${res.updated} 名球员`
            : res.ok != null
              ? `成功导入 ${res.ok} 条比赛记录`
              : `规则已保存`;
        toast(`导入完成：${summary}`);
        flowZone.innerHTML = "";
        flowZone.appendChild(el(`<div class="empty-state">本次导入已完成<div class="hint">${esc(summary)}</div></div>`));
        if (reloadList) reloadList();
        rc.refreshBadges();
      } catch (e) {
        toast(e.message, true);
      }
    });
    btnRow.querySelector('[data-act="reject"]').addEventListener("click", async () => {
      flowZone.innerHTML = `<div class="empty-state">已丢弃预览，未写入任何数据</div>`;
      if (reloadList) reloadList();
    });
    flowZone.appendChild(btnRow);
  }

  async function loadList() {
    const zone = listCard.querySelector("#im-list");
    try {
      const r = await api.get("imports/pending");
      zone.innerHTML = "";
      const pendingRows = (r.pending || []).filter((x) => x.status === "pending");
      const doneRows = (r.pending || []).filter((x) => x.status !== "pending");

      const t = renderTable(
        [
          { label: "#", num: true, render: (row) => esc(row.id) },
          { label: "类型", render: (row) => esc(KIND_NAME[row.kind] || row.kind) },
          { label: "文件", render: (row) => esc(row.file_name) },
          { label: "预览", render: () => "" },
          { label: "操作", render: () => "" },
        ],
        pendingRows,
        "没有等待确认的导入"
      );
      t.addEventListener("click", async (ev) => {
        const tr = ev.target.closest("tbody tr");
        if (!tr) return;
        const idx = Array.from(t.tBodies[0].rows).indexOf(tr);
        const row = pendingRows[idx];
        const d = openDrawer(`待确认 #${row.id} · ${KIND_NAME[row.kind] || row.kind}`);
        const pre = el(`<pre class="preview"></pre>`);
        pre.textContent = row.preview || "（无预览）";
        d.body.appendChild(pre);
        const btns = el(`<div style="display:flex;gap:10px;margin-top:12px">
          <button type="button" class="btn" data-act="ok">确认执行导入</button>
          <button type="button" class="btn danger" data-act="no">驳回</button>
        </div>`);
        btns.querySelector('[data-act="ok"]').addEventListener("click", async () => {
          try {
            const res = await api.importPending(row);
            d.close();
            toast("导入完成");
            loadList();
            rc.refreshBadges();
          } catch (e) {
            toast(e.message, true);
          }
        });
        btns.querySelector('[data-act="no"]').addEventListener("click", async () => {
          if (!(await confirmDialog({ title: "驳回这条导入？", message: `「${row.file_name}」将被标记为驳回，不再出现在待确认列表。`, danger: true, confirmText: "驳回" }))) return;
          try {
            await api.del(`imports/pending/${row.id}`);
            d.close();
            toast("已驳回");
            loadList();
            rc.refreshBadges();
          } catch (e) {
            toast(e.message, true);
          }
        });
        d.body.appendChild(btns);
      });
      zone.appendChild(t);

      /* 已处理记录折叠显示 */
      if (doneRows.length) {
        const det = el(`<details style="margin-top:12px"><summary style="cursor:pointer;color:var(--muted);font-size:13px">已处理的导入（${doneRows.length}）</summary></details>`);
        det.appendChild(
          renderTable(
            [
              { label: "#", num: true, render: (row) => esc(row.id) },
              { label: "类型", render: (row) => esc(KIND_NAME[row.kind] || row.kind) },
              { label: "文件", render: (row) => esc(row.file_name) },
              { label: "状态", render: (row) => `<span class="tag ${row.status === "done" ? "ok" : "warn"}">${esc(row.status)}</span>` },
            ],
            doneRows
          )
        );
        zone.appendChild(det);
      }
    } catch (e) {
      zone.innerHTML = "";
      zone.appendChild(errorNote(e.message));
    }
  }
  reloadList = loadList;

  await loadList();
  return loadList;
}
