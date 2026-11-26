import {esc} from '../format.js';
import {pill} from './desk.js';
import {trialStates} from '../types.js';

const statePill = trial => trial.state === 'eligible'
  ? pill(trialStates.eligible)
  : trial.state === 'skipped' ? pill(trialStates.skipped) : pill(trialStates.pending);

export function trialTable(trials, items) {
  if (!trials || trials.length === 0) {
    return '<p class="empty">本轮观察表没有试填题。</p>';
  }
  return `<div class="load-table trial-table"><table>
    <caption>试题试填载荷 · 基于本批固定因子分数回归估计；只认签发前已存下的结果</caption>
    <thead><tr><th>试题</th><th>因子一</th><th>因子二</th><th>有效作答</th><th>门槛状态</th></tr></thead>
    <tbody>${trials.map(trial => {
      const item = items.find(row => row.id === trial.item_id);
      const cells = trial.loadings
        ? trial.loadings.map(value => `<td class="${value < 0 ? 'negative' : 'positive'}"><span>${value.toFixed(3)}</span>
            <meter min="0" max="1" value="${Math.min(1, Math.abs(value))}"></meter></td>`).join('')
        : '<td colspan="2" class="muted">未估计</td>';
      return `<tr><th scope="row"><small>${esc(trial.code)}</small> ${esc(item?.prompt ?? '')}
          ${trial.reverse ? pill('反向已校正') : ''}</th>
        ${cells}
        <td>${trial.count === null ? '—' : esc(trial.count)}</td>
        <td>${statePill(trial)}${trial.reason ? `<small class="reason">${esc(trial.reason)}</small>` : ''}</td></tr>`;
    }).join('')}</tbody></table>
    <p class="hint">每组至少 8 份、整批至少 24 份该题有效作答才估计载荷；主要载荷绝对值达到 0.45 才能选为正式题。未选中的试题随新表继续试填。</p></div>`;
}
