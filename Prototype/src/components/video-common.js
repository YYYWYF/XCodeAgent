import { state, safe } from '../state.js?v=20261122b';

// 按教学视频的阶段展示对应 Agent。
export function agentHeader(phase = state.phase) {
  const roles = ['产品','规划','研发','测试','审查','验收'];
  const initials = ['P','PM','RD','QA','CR','验'];
  return `<div class="agent-label"><span class="agent-avatar" aria-hidden="true">${initials[phase]}</span><span>${roles[phase]} Agent</span></div>`;
}

// 显示工作流确认卡，正文与操作由各阶段提供。
export function workflowCard(body, actions = '', status = '待确认') {
  return `<div class="flow-card video-flow"><div class="flow-title"><strong>●　工作流执行</strong><span class="tag">${status}</span></div><div class="flow-body">${body}</div>${actions?`<div class="flow-actions">${actions}</div>`:''}</div>`;
}

export function action(label, name, extra = '') {
  return `<button type="button" data-action="${name}" ${extra}>${label}</button>`;
}

// 用简洁线框表达视频中的新闻门户。
export function portalContent() {
  const categories = [
    ['行情聚焦', [['2024年光伏行业市场深度分析','全球光伏装机规模持续增长，产业链价格变化与技术迭代受到关注。'],['本周大宗商品行情速览','原油、铜、铝主力合约涨跌互现，库存数据成为关键变量。']]],
    ['战略推动', [['总行发布数字化转型三年行动方案','围绕数据治理、场景金融与智能运营推进数字化转型。'],['普惠金融服务持续推进','持续优化服务流程，提升小微企业金融服务质效。']]],
    ['一线扫描', [['分行团队践行客户服务','一线员工围绕客户需求开展协同，持续改善服务体验。'],['西南区销售团队攻坚纪实','团队聚焦重点项目，协同推进交付与客户服务。']]],
    ['文化故事', [['匠心三十载：老技师的坚守与传承','一位老钳工三十年如一日，用双手和智慧守护生产线。'],['志愿者在行动','志愿服务团队持续开展教育帮扶与公益活动。']]]
  ];
  return `<div class="portal-content">${categories.map(([name,items])=>`<section class="portal-section"><h3>${name}</h3>${items.map(([title,summary],i)=>`<article><strong><span class="tag">${i+1}</span> ${title}</strong><p>${summary}</p><p>${summary} 相关团队将持续关注进展，并结合实际情况推进后续工作。</p><small>${name}　发布日期：2024-03-01　来源：数据中心</small></article>`).join('')}</section>`).join('')}</div>`;
}

// 对用户输入做安全显示。
export function message(text) { return text ? `<div class="user-message">${safe(text)}</div>` : ''; }
