import { api } from '../api.js';

export async function renderSettings(view, setMsg) {
  const j = await api('/api/settings');
  const ledger = await api('/api/settings/cutovers');
  const cur = j.current_threshold_kb;
  const last = j.latest_cutover;
  view.innerHTML = `
    <section class="card">
      <h2>台参数</h2>
      <p class="muted">压实阈值与对账模式；保存阈值即切流，写切流账册并同步在途单口径。</p>
      <p>
        <span class="chip ok">现行阈值 ${cur} KB</span>
        ${last ? `<span class="chip">现行凭证 ${last.cutover_code}</span>
          <span class="chip">${last.old_threshold_kb} → ${last.new_threshold_kb} KB</span>` : '<span class="chip">尚无切流凭证</span>'}
      </p>
      <div class="wall">
        ${(j.items || []).map(s => `<article class="card">
          <h3>${s.label}</h3>
          <p class="muted">${s.key}</p>
          <input data-key="${s.key}" value="${s.value}" />
          ${s.key === 'compact_threshold_kb' && last
            ? `<p class="muted">最近切流 ${last.cutover_code} · ${last.old_threshold_kb} → ${last.new_threshold_kb} KB</p>` : ''}
          <label>切流说明<input data-note="${s.key}" placeholder="如：夜间口径收紧" /></label>
          <button data-save="${s.key}" class="primary" type="button">保存</button>
        </article>`).join('')}
      </div>
    </section>
    <section class="card" style="margin-top:1rem">
      <h2>切流账册</h2>
      <p class="muted">阈值切换凭证流水；在途单据最新凭证切口径。</p>
      ${(ledger.items || []).length ? `<table class="tbl"><thead>
        <tr><th>凭证号</th><th>旧 → 新 KB</th><th>操作人</th><th>说明</th><th>时间</th></tr></thead>
        <tbody>${ledger.items.map(r => `<tr>
          <td><strong>${r.cutover_code}</strong></td>
          <td>${r.old_threshold_kb} → ${r.new_threshold_kb}</td>
          <td>${r.operator || '-'}</td><td>${r.note || '-'}</td><td class="muted">${r.created_at}</td>
        </tr>`).join('')}</tbody></table>`
        : '<p class="muted">暂无切流凭证</p>'}
      <h3 style="margin-top:1rem">旧口径挂入拦截</h3>
      ${(ledger.rejections || []).length ? `<table class="tbl"><thead>
        <tr><th>计划#</th><th>凭证号</th><th>尝试 KB</th><th>现行 KB</th><th>原因</th><th>时间</th></tr></thead>
        <tbody>${ledger.rejections.map(r => `<tr>
          <td>#${r.plan_id}</td><td><strong>${r.cutover_code || '-'}</strong></td>
          <td>${r.attempted_kb ?? '-'}</td><td>${r.current_kb}</td>
          <td class="err">${r.reason}</td><td class="muted">${r.created_at}</td>
        </tr>`).join('')}</tbody></table>`
        : '<p class="muted">暂无拦截记录</p>'}
    </section>`;
  view.querySelectorAll('[data-save]').forEach(btn => {
    btn.onclick = async () => {
      try {
        const key = btn.dataset.save;
        const input = view.querySelector('input[data-key="'+key+'"]');
        const noteEl = view.querySelector('input[data-note="'+key+'"]');
        const r = await api('/api/settings', { method:'POST', body: JSON.stringify({
          key, value: input.value, note: noteEl ? noteEl.value : '',
        })});
        if (r.cutover) {
          setMsg('已切流 ' + r.cutover.cutover_code + ' · ' + r.cutover.old_threshold_kb
            + ' → ' + r.cutover.new_threshold_kb + ' KB · 在途计划 '
            + r.cutover.migrated_plans + ' / 在册碎件 ' + r.cutover.migrated_files);
        } else {
          setMsg('已保存 ' + key);
        }
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
