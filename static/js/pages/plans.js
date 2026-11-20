import { api } from '../api.js';

function planChip(p) {
  const ok = p.threshold_in_sync;
  const code = p.cutover_code || '无';
  return `<span class="chip ${ok ? 'ok' : 'stale'}">切流 ${code} · ${p.threshold_kb} KB</span>`
    + (ok ? '' : `<span class="chip stale">口径偏离：现行 ${p.current_threshold_kb} KB</span>`);
}

export async function renderPlans(view, setMsg) {
  const j = await api('/api/plans');
  view.innerHTML = `
    <section class="card">
      <h2>压实计划</h2>
      <div class="row">
        <label>编号<input id="code" value="CP-NEW-1" /></label>
        <label>标题<input id="title" value="新压实计划" /></label>
        <label>目标表<input id="table" value="dw_payments" /></label>
        <button class="primary" id="create" type="button">创建计划</button>
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(j.items || []).map(p => `<article class="card">
        <h3>${p.plan_code}</h3>
        <span class="tag">${p.status}</span>
        ${planChip(p)}
        <p>${p.title}</p>
        <p class="muted">${p.target_table} · ${p.item_count} 文件 · ${p.plan_bytes} 字节</p>
        <div class="row">
          <button data-attach="${p.id}" data-kb="${p.current_threshold_kb}" type="button">挂入碎文件</button>
          <button data-pub="${p.id}" class="primary" type="button">发布</button>
          <button data-detail="${p.id}" type="button">详情</button>
        </div>
        <div class="muted" id="d${p.id}"></div>
      </article>`).join('') || '<p class="muted">暂无计划</p>'}
    </section>`;
  document.getElementById('create').onclick = async () => {
    try {
      await api('/api/plans', { method:'POST', body: JSON.stringify({
        plan_code: document.getElementById('code').value,
        title: document.getElementById('title').value,
        target_table: document.getElementById('table').value,
      })});
      setMsg('计划已创建');
      await renderPlans(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-attach]').forEach(btn => {
    btn.onclick = async () => {
      try {
        const r = await api('/api/plans/' + btn.dataset.attach + '/attach', {
          method:'POST',
          body: JSON.stringify({ threshold_kb: Number(btn.dataset.kb) }),
        });
        setMsg('挂入 ' + r.attached + ' 个碎文件');
        await renderPlans(view, setMsg);
      } catch (e) { setMsg(e.message, true); await renderPlans(view, setMsg); }
    };
  });
  view.querySelectorAll('[data-pub]').forEach(btn => {
    btn.onclick = async () => {
      try {
        await api('/api/plans/' + btn.dataset.pub + '/publish', { method:'POST', body:'{}' });
        setMsg('已发布');
        await renderPlans(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-detail]').forEach(btn => {
    btn.onclick = async () => {
      try {
        const r = await api('/api/plans/' + btn.dataset.detail);
        const p = r.plan;
        const ok = p.threshold_in_sync;
        const last = r.latest_cutover;
        document.getElementById('d' + btn.dataset.detail).innerHTML = `
          <span class="chip ${ok ? 'ok' : 'stale'}">在途单口径 ${p.threshold_kb} KB</span>
          <span class="chip ${ok ? 'ok' : 'stale'}">切流 ${p.cutover_code || '无'}</span>
          <span class="chip ${ok ? 'ok' : 'stale'}">现行阈值 ${p.current_threshold_kb} KB</span>
          ${ok ? '<span class="chip ok">口径对齐</span>' : '<span class="chip stale">口径偏离 · 挂入会被拦</span>'}
          ${last ? `<p class="muted">现行凭证 ${last.cutover_code} · ${last.old_threshold_kb} → ${last.new_threshold_kb} KB</p>` : ''}
          <p class="muted">${(r.items || []).length} 个碎件：
            ${(r.items || []).map(f => `${f.path}（${f.size_bytes}）`).join('、') || '无'}</p>`;
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
