import { state, safe } from '../state.js?v=20261122b';
import { action, agentHeader, message, portalContent, workflowCard } from '../components/video-common.js?v=20261122b';

// 设计阶段展示需求文档确认与门户首页设计稿。
export function designConversation() {
  if (state.isDemo) return agentHeader(0) + workflowCard('<strong>需求文档与 UI 设计稿已确认</strong><p>门户首页按四个区块展示新闻内容，可在右侧查看已完成的设计产物。</p>', '<button data-tab="需求文档">查看需求文档</button><button data-tab="UI设计稿">查看设计稿</button><button data-phase="1" class="primary">查看技术规划</button>', '已完成');
  if (state.designStep === 0) {
    const editing = state.revisionEditing === 'requirement';
    const body = editing
      ? `<p>需求文档已生成，填写修改意见后提交。</p><textarea id="revision-text" placeholder="输入需求文档修改意见">${safe(state.requirementFeedback)}</textarea>`
      : '<strong>需求文档已生成，已展示在右侧</strong><p>请在右侧查看内容，确认无误后继续规划，也可以先修改。</p><p>✓　需求草稿待确认</p>';
    const buttons = editing
      ? action('取消','revision-cancel') + action('提交','requirement-revise','class="primary"')
      : action('修改','requirement-edit') + action('确认并继续规划','requirement-confirm','class="primary"');
    return agentHeader(0) + message(state.requirementFeedback ? `需求文档修改：${state.requirementFeedback}` : '') + workflowCard(body,buttons);
  }
  const status = state.uiSkipped ? '已跳过' : state.uiGenerated ? '已生成' : '未生成';
  const body = `<div class="notice"><strong>UI 设计稿</strong>　${state.uiGenerated?'1 / 1':'0 / 1'}<p>如需视觉参考可选择模板或换一换生成设计稿，也可以跳过；完成后进入计划阶段。</p></div><div class="flow-row"><span>门户首页　<small>/page/home　${status}</small></span><span>${action('查看设计稿','ui-view')} ${action('选模板','ui-template')} ${action(state.uiGenerated?'换一换':'生成','ui-generate')}</span></div><label for="ui-feedback">设计调整意见</label><textarea id="ui-feedback" placeholder="描述要调整的设计稿，例如：概览页改成卡片布局">${safe(state.uiFeedback||'')}</textarea>${action('提交调整','ui-adjust')}<p class="hint">可为页面选择模板或生成设计稿，也可以直接跳过。</p>`;
  const buttons = action('全部生成','ui-generate') + action('跳过 UI 设计','ui-skip') + action('下一步','design-next',`class="primary" ${state.uiGenerated || state.uiSkipped?'':'disabled'}`);
  return agentHeader(0) + message('需求文档已确认，继续规划。') + workflowCard(body,buttons);
}

// 技术规划允许提出修改意见，确认后进入开发阶段。
export function planningConversation() {
  if (state.isDemo) return agentHeader(1) + message(`技术规划修改：${state.planFeedback}`) + workflowCard('<strong>技术规划已确认</strong><p>新闻资讯实体、查询接口和页面绑定已保存，可在右侧查看。</p>', '<button data-phase="2" class="primary">查看开发产物</button>', '已完成');
  const editing = state.revisionEditing === 'plan';
  const body = editing
    ? `<p>技术规划已生成，请输入修改意见。</p><textarea id="revision-text" placeholder="输入技术规划修改意见">${safe(state.planFeedback)}</textarea>`
    : '<div class="notice"><strong>技术规划已生成，已展示在右侧</strong><p>请在右侧查看内容，确认无误后保存，也可以先提出修改意见。</p><p>✓　技术规划已生成</p></div>';
  const buttons = editing
    ? action('取消','revision-cancel') + action('提交','plan-revise','class="primary"')
    : action('修改','plan-edit') + action('确认保存','plan-confirm','class="primary"');
  return agentHeader(1) + message(state.planFeedback ? `技术规划修改：${state.planFeedback}` : '') + workflowCard(body,buttons);
}

// 展示视频中的需求草稿结构和关键业务信息。
export function requirementPanel() {
  const section = state.section || '概览';
  const tabs = ['概览','页面','业务流程','页面概览','角色与权限'];
  const views = {
    '概览':`<h3>应用概览</h3><div class="simple-card"><strong>${safe(state.name)}</strong><p>${safe(state.description)}</p></div><h3>功能模块</h3><div class="simple-card"><strong>新闻资讯展示</strong><p>按行情聚焦、战略推动、一线扫描、文化故事四个区块展示新闻资讯。</p></div>`,
    '页面':'<h3>门户首页　/page/home</h3><div class="simple-card"><strong>页面目标</strong><p>面向分行员工与访客，按四个区块浏览门户新闻。</p><strong>业务信息</strong><p>序号、标题、摘要、维度、发布日期、来源与内容。</p></div>',
    '业务流程':`<h3>业务流程　1</h3><div class="simple-card"><strong>按维度浏览门户新闻</strong><p>1. 用户打开苏州分行门户首页。</p><p>2. 首页按四个区块展示新闻列表。</p><p>3. 用户浏览新闻的标题、摘要、发布日期和来源。${state.requirementFeedback?'新闻内容在首页直接呈现。':'可查看新闻完整内容。'}</p></div>`,
    '页面概览':'<h3>页面概览</h3><div class="simple-card"><strong>门户首页</strong><p>行情聚焦　｜　战略推动　｜　一线扫描　｜　文化故事</p><p>数据来自查询新闻列表接口。</p></div>',
    '角色与权限':'<h3>角色与权限</h3><div class="simple-card"><p>分行员工、访客：浏览门户新闻。</p><p>本示例无需登录即可查看。</p></div>'
  };
  return `<div class="document-content"><div class="artifact-heading"><div><strong>需求文档</strong><small>requirement-spec.md · ${state.designStep===0?'草稿':'已确认'}</small></div><span class="tag">${state.designStep===0?'待确认':'已确认'}</span></div><div class="subtabs">${tabs.map(s=>`<button data-section="${s}" class="${section===s?'selected':''}">${s}</button>`).join('')}</div>${views[section]||views['概览']}</div>`;
}

// 展示门户首页设计稿及其生成前空态。
export function uiPanel() {
  return `<div class="document-content ui-panel"><div class="subtabs"><button class="selected">1　门户首页</button></div>${state.uiSkipped?'<div class="panel-empty">已跳过 UI 设计</div>':state.uiGenerated?`<div class="ui-preview-toolbar">设计稿第 ${state.uiVariant} 版　｜　成功　 /　加载中　 /　空数据　 /　加载失败</div>${portalContent()}`:'<div class="panel-empty">本页尚未生成设计稿<br>在左侧点击“生成”或“选模板”。</div>'}</div>`;
}

// 展示技术规划的架构、实体、接口和页面绑定。
export function technicalPanel() {
  const section = state.section || '架构';
  const tabs = ['架构','实体','API 契约','页面绑定','接口权限绑定'];
  const rows = [['序号','sequence_number','number'],['标题','title','text'],['摘要','summary','text'],['维度','dimension',state.planFeedback?'text':'enum'],['发布日期','publish_date','date'],['来源','source','text'],['内容','content','long_text']];
  const views = {
    '架构':'<h3>系统架构</h3><div class="simple-card"><p>PC 端门户页面 → 应用 API → 新闻数据库。</p><p>前端展示新闻资讯，后端提供按维度查询的接口。</p></div>',
    '实体':`<h3>实体模型</h3><div class="simple-card"><strong>新闻资讯　News</strong><p>记录首页展示的新闻资讯，包含序号、标题、摘要、维度、发布日期、来源与内容。</p><table class="video-table"><thead><tr><th>字段说明</th><th>字段名</th><th>类型</th><th>必填</th></tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}<td>必填</td></tr>`).join('')}</tbody></table></div>`,
    'API 契约':'<h3>应用 API</h3><div class="simple-card"><strong>查询新闻列表</strong><p>GET　/api/news</p><p>请求参数：dimension、current、pageSize</p><p>返回：total、pageSize、current、新闻列表</p><p>每条新闻包含序号、标题、摘要、维度、发布日期、来源与内容。</p></div>',
    '页面绑定':'<h3>页面绑定</h3><div class="simple-card"><strong>门户首页　/page/home</strong><p>调用 GET /api/news，按维度加载新闻资讯。</p></div>',
    '接口权限绑定':'<h3>接口权限绑定</h3><div class="simple-card"><p>查询新闻列表：无需鉴权。</p></div>'
  };
  return `<div class="document-content"><div class="artifact-heading"><div><strong>技术规划</strong><small>technical-plan.json</small></div><span class="tag">${state.reached>=2?'已确认':'待确认'}</span></div><div class="subtabs">${tabs.map(s=>`<button data-section="${s}" class="${section===s?'selected':''}">${s}</button>`).join('')}</div>${views[section]||views['架构']}</div>`;
}
