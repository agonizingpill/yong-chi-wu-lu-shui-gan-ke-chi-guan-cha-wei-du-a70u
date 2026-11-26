import {empty,headline,pill} from '../components/desk.js';
import {formData,handle,inlineError} from '../form.js';
import {esc} from '../format.js';
import {actor,api} from '../store.js';
import {formStates,trialStates} from '../types.js';

function trialStatus(report,itemId) {
  const trial = (report?.trials || []).find(row=>row.item_id===itemId);
  if (!trial) return {label:'本批未估计', enabled:false, note:'签发前没有存下结果'};
  if (trial.state === 'eligible' && trial.strong) {
    return {label:trialStates.eligible, enabled:true, note:`因子 ${trial.primary} · ${trial.count} 份有效作答`};
  }
  if (trial.state === 'skipped') return {label:trialStates.skipped, enabled:false, note:trial.reason || '未达门槛'};
  return {label:trialStates.pending, enabled:false, note:'试填结果不足'};
}

function publishForm(report,form) {
  return `<form id="publish-form" class="pad">
    <h2>拟定下轮观察表</h2><p>依据 ${esc(report.code)}。正式因子只认签发前存下的结果；达到载荷门槛的试题才能选为正式题，未选中的试题自动随新表继续试填。</p>
    <div class="two"><label>版本编号<input name="edition" required value="LW-02"></label>
    <label>新版名称<input name="title" required value="课池观察表 · 新一轮"></label>
    <label>下一轮编号<input name="next_code" required value="POND-1201"></label>
    <label>下一轮名称<input name="next_title" required value="12 月课池体验复采"></label></div>
    <fieldset><legend>选入新版的量表题（正式题与达标试题）</legend>${form.items.map(item=>{
      if (item.is_trial) {
        const status = trialStatus(report,item.id);
        return `<label class="check trial-choice ${status.enabled?'':'disabled'}">
          <input type="checkbox" name="item_id" value="${item.id}" ${status.enabled?'':'disabled'}>
          ${esc(item.code)} · ${esc(item.prompt)} ${pill('试填题')} ${pill(status.label)}
          <small class="reason">${esc(status.note ?? '')}</small></label>`;
      }
      const factor=report.findings.items.find(row=>row.item_id===item.id);
      return `<label class="check"><input type="checkbox" name="item_id" value="${item.id}" checked ${item.pinned?'disabled':''}> ${esc(item.code)} · ${esc(item.prompt)}
       ${item.pinned?pill('固定保留'):''} ${factor?.strong?pill(`因子 ${factor.primary}`):''}</label>`;
    }).join('')}</fieldset>
    <fieldset class="new-trials"><legend>新增试题（随新表试填，可留空作答）</legend>
      <div class="trial-add" data-index="0">
        <label>试题编号<input name="trial_code" pattern="[A-Z]{2}-[0-9]{2}" placeholder="TW-03"></label>
        <label>试题表述<input name="trial_prompt" minlength="6" maxlength="120" placeholder="例如：学员入水后能主动放松肩部。"></label>
        <label class="check"><input type="checkbox" name="trial_reversed"> 反向题</label>
      </div>
      <button type="button" id="add-trial-row" class="ghost">再加一道试题</button>
    </fieldset>
    <button type="submit">发布并开放下一轮</button>${inlineError}</form>`;
}

function appendTrialForm(form) {
  return `<form id="trial-form" class="pad">
    <h2>向现行表追加试题</h2><p>试题挂在 ${esc(form.edition)} 上随课试填，答卷可留空，不影响正式因子。</p>
    <div class="two">
      <label>试题编号<input name="code" required pattern="[A-Z]{2}-[0-9]{2}" placeholder="TW-03"></label>
      <label>试题表述<input name="prompt" required minlength="6" maxlength="120"></label>
      <label class="check"><input type="checkbox" name="reversed"> 反向题</label>
    </div>
    <input type="hidden" name="form_id" value="${form.id}">
    <button type="submit">追加试题</button>${inlineError}</form>`;
}

export async function formsPage(root,ctx) {
  const [forms,waves,reports] = await Promise.all([api().forms(),api().waves(),api().reports()]);
  const selected = forms.find(f=>f.id===Number(ctx.query.get('id'))) || forms[0];
  const source = reports.find(r=>r.state==='approved' && waves.some(w=>w.id===r.wave_id && w.state==='review' && w.form_id===selected?.id));
  const isSupervisor = actor().role==='supervisor';
  const canWrite = source && selected?.state==='active' && isSupervisor;
  const trialInForm = (selected?.items || []).filter(item=>item.is_trial);
  root.innerHTML = `${headline('观察表版次','现行表连接下一轮匿名回收，历史答卷继续按原版题目身份解释。')}
  <div class="form-layout"><section>
  <nav class="edition-nav" aria-label="版次">${forms.map(f=>`<a href="#editions?id=${f.id}" ${selected===f?'aria-current="true"':''}>${esc(f.edition)} ${pill(formStates[f.state])}</a>`).join('')}</nav>
  ${selected ? `<article class="edition-sheet" data-edition="${esc(selected.edition)}"><header><h2>${esc(selected.title)}</h2>
   <small>${esc(selected.signed_by)} 签发</small></header><ol>${selected.items.map(item=>`<li class="${item.is_trial?'is-trial':''}"><small>${esc(item.code)}</small> ${esc(item.prompt)}
     ${item.is_trial ? pill('试填题') : (item.factor ? pill(`因子 ${item.factor}`) : '')} ${item.reversed ? pill('反向量表') : ''}</li>`).join('')}</ol>
   ${trialInForm.length ? `<p class="hint">本表含 ${trialInForm.length} 道试题，随本轮观察试填。</p>` : ''}</article>` : empty('目前没有观察表。')}
  </section>${canWrite?publishForm(source,selected):''}
  ${isSupervisor && selected?.state==='active' && !source ? appendTrialForm(selected) : ''}</div>`;

  const addRowButton = root.querySelector('#add-trial-row');
  if (addRowButton) {
    addRowButton.onclick = () => {
      const wrap = root.querySelector('.new-trials');
      const index = wrap.querySelectorAll('.trial-add').length;
      const row = document.createElement('div');
      row.className = 'trial-add';
      row.dataset.index = index;
      row.innerHTML = `<label>试题编号<input name="trial_code" pattern="[A-Z]{2}-[0-9]{2}" placeholder="TW-0${index + 3}"></label>
        <label>试题表述<input name="trial_prompt" minlength="6" maxlength="120"></label>
        <label class="check"><input type="checkbox" name="trial_reversed"> 反向题</label>`;
      wrap.insertBefore(row, addRowButton);
    };
  }

  handle(root.querySelector('#publish-form'),async formElement=>{
    const formDataRaw = new FormData(formElement);
    const mutable = formDataRaw.getAll('item_id').map(Number);
    const trial_items = [...root.querySelectorAll('.trial-add')].map(row=>({
      code: row.querySelector('[name="trial_code"]').value.trim(),
      prompt: row.querySelector('[name="trial_prompt"]').value.trim(),
      reversed: row.querySelector('[name="trial_reversed"]').checked,
    })).filter(spec=>spec.code && spec.prompt.length >= 6);
    const ids = selected.items.filter(item=>item.pinned || mutable.includes(item.id)).map(item=>item.id);
    const output = await api().publish({
      report_id: source.id,
      edition: formElement.elements.edition.value,
      title: formElement.elements.title.value,
      next_code: formElement.elements.next_code.value,
      next_title: formElement.elements.next_title.value,
      item_ids: ids, trial_items,
    });
    ctx.flash(`${output.form.edition} 已发布，下一轮已可以收集；未选中的试题已随新表继续试填。`);
    location.hash=`editions?id=${output.form.id}`;
  });

  handle(root.querySelector('#trial-form'),async formElement=>{
    const data = formData(formElement,['form_id']);
    data.reversed = formElement.elements.reversed.checked;
    const output = await api().appendTrial(data);
    ctx.flash(`${data.code} 已作为试题追加到现行表。`);
    location.hash=`editions?id=${output.id}`;
  });
}
