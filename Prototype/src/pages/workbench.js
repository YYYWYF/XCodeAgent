import { state, safe } from '../state.js?v=20261122a';
import { phases } from '../data/mock-data.js?v=20261118o';
import { auxiliaryDrawer } from '../components/auxiliary.js?v=20261122a';
import { icon } from '../components/icons.js?v=20261121a';
import { action, message } from '../components/video-common.js?v=20261122a';
import { designConversation, planningConversation, requirementPanel, uiPanel, technicalPanel } from './video-design.js?v=20261122a';
import { developmentConversation, artifactsPanel, applicationFilesPanel, pageDebugPanel, apiDebugPanel, mappingPanel, taskPlanPanel, previewPanel } from './video-development.js?v=20261122a';
import { testConversation, testReport, reviewConversation, reviewReport, acceptanceConversation, acceptanceBar } from './video-verification.js?v=20261122a';

// 保留跨阶段的公共功能入口，图标按功能语义统一使用线框 SVG。
function rail() {
  const main = [['history','对话管理'],['data','数据源'],['external','外部 API'],['async','异步任务'],['tide','潮汐任务']];
  const bottom = [['files','文件'],['skills','技能'],['settings','设置']];
  const buttons = items => items.map(([id,title])=>`<button data-pane="${id}" class="${state.pane===id?'active':''}" title="${title}" aria-label="${title}">${icon(id)}</button>`).join('');
  return `<aside class="session-rail" aria-label="工作台功能导航"><div class="rail-primary">${buttons(main)}</div><span class="rail-spacer"></span><div class="rail-secondary">${buttons(bottom)}<span class="rail-user">S</span></div></aside>`;
}

// 左侧始终承载当前阶段的对话与确认卡。
function conversation() {
  if (state.sessionSelected < state.sessions.length - 1) {
    return `<main class="assistant-area"><header class="chat-title">${safe(state.sessions[state.sessionSelected])} · 仅查看</header><div class="assistant-scroll"><div class="conversation-content"><p>这是一条历史对话的示意。请在对话管理中选择当前推进的对话，继续操作工作流。</p></div></div><form class="composer"><textarea disabled placeholder="历史对话仅供查看"></textarea><div class="row between"><span>＋　${icon('history')}</span><button disabled aria-label="发送">➤</button></div></form></main>`;
  }
  const content = [designConversation,planningConversation,developmentConversation,testConversation,reviewConversation,acceptanceConversation][state.phase]();
  const title = ['产品设计','技术规划','应用开发','应用测试','代码审查','版本验收'][state.phase];
  return `<main class="assistant-area"><header class="chat-title">${title}</header><div class="assistant-scroll"><div class="conversation-content">${content}${state.phase>=2&&state.lastMessage?message(state.lastMessage):''}</div></div><form id="chat-form" class="composer"><textarea id="message" placeholder="${state.phase===0?'告诉产品 Agent 你想调整的需求、页面或 UI，也可以直接提问…':state.phase===1?'可以询问技术规划或描述修改意见…':'描述你想微调的页面或 API，例如修改文案、样式或接口逻辑…'}"></textarea><div class="row between"><span>＋　${icon('history')}</span><button aria-label="发送">➤</button></div></form></main>`;
}

function panelAvailable(name) {
  if (name==='页面调试') return state.completed.includes('门户首页');
  if (name==='接口调试') return state.completed.includes('查询新闻列表');
  return !(name==='UI设计稿' && state.phase===0 && !state.designStep && !state.uiGenerated) && !(name==='测试报告' && !state.testStep) && !(name==='审查报告' && !state.reviewStep);
}

// 待验收的预览自动铺满；关闭预览后恢复对话分栏。
function acceptanceFocus() { return state.phase===5 && !state.accepted && state.tab==='应用预览' && state.panelLayout==='full'; }

// 三档控制贴在中间分栏线；隐藏或全宽时贴到工作区右边缘。
function panelLayoutControl(full) {
  const options = [['hidden','隐藏'],['split','分栏'],['full','全宽']];
  const docked=state.panelLayout==='split'&&!full;
  const drawerEdge=state.auxDetail?1020:state.pane==='settings'?760:null;
  const dockedClass=docked&&drawerEdge?' drawer-edge':'';
  const edgeStyle=docked&&drawerEdge?`style="left:${drawerEdge}px"`:'';
  return `<div class="panel-layout-control ${docked?'docked':'floating'}${dockedClass}" ${edgeStyle} role="group" aria-label="右侧面板布局"><span class="panel-layout-caption" aria-hidden="true">右侧面板布局</span>${options.map(([value,label])=>`<button type="button" data-panel-layout="${value}" aria-pressed="${state.panelLayout===value}" class="${state.panelLayout===value?'active':''}">${label}</button>`).join('')}</div>`;
}

function panelTabs(names, selected) {
  const control = acceptanceFocus() ? '<button data-action="close-preview" title="关闭预览" aria-label="关闭预览">×</button>' : '';
  const disabledTitle = name => name==='页面调试'?'完成「门户首页」开发后可调试':name==='接口调试'?'完成「查询新闻列表」开发后可调试':'';
  return `<div class="workspace-tabs">${names.map(name=>`<button data-tab="${name}" class="${name===selected?'selected':''}" ${panelAvailable(name)?'':`disabled${disabledTitle(name)?` title="${disabledTitle(name)}"`:''}`}>${name}</button>`).join('')}<span class="panel-controls">${control}</span></div>`;
}

// 右侧内容按阶段和所选标签切换。
function workspacePanel() {
  let names;
  if (state.phase===0) names = ['需求文档','UI设计稿'];
  else if (state.phase===1) names = ['技术规划'];
  else {
    names = state.phase===5 ? ['开发产物','应用预览','页面调试','接口调试','应用文件','字段映射'] : ['开发产物','页面调试','接口调试','应用文件','字段映射'];
    if (state.phase===2 && state.buildStep==='task-ready') names.push('待确认任务');
    if (state.phase===3) names.push('测试报告');
    if (state.phase===4) names.push('审查报告');
  }
  const selected = names.includes(state.tab) && panelAvailable(state.tab)?state.tab:names[0];
  let body = '';
  if (selected==='需求文档') body = requirementPanel();
  else if (selected==='UI设计稿') body = uiPanel();
  else if (selected==='技术规划') body = technicalPanel();
  else if (selected==='开发产物') body = artifactsPanel();
  else if (selected==='页面调试') body = pageDebugPanel();
  else if (selected==='接口调试') body = apiDebugPanel();
  else if (selected==='字段映射') body = mappingPanel();
  else if (selected==='待确认任务') body = taskPlanPanel();
  else if (selected==='测试报告') body = testReport();
  else if (selected==='审查报告') body = reviewReport();
  else if (selected==='应用预览') body = previewPanel();
  else body = applicationFilesPanel();
  return `<section class="workspace-panel">${panelTabs(names,selected)}${body}${state.phase===5&&!state.accepted&&selected==='应用预览'?acceptanceBar():''}</section>`;
}

// 需要确认的操作使用页面内弹窗呈现。
function modal() {
  if (!state.modal) return '';
  let title='', titleDescription='', body='', buttons='';
  if (state.modal==='ui-template') {
    title='选择页面模板'; body='<p>为门户首页选择版式模板。</p><label class="check"><input type="radio" name="template" value="资讯列表" checked>资讯列表</label><label class="check"><input type="radio" name="template" value="分区门户">分区门户</label><label class="check"><input type="radio" name="template" value="卡片列表">卡片列表</label>'; buttons=action('取消','close-modal')+action('使用此模板并生成','ui-template-use','class="primary"');
  } else if (state.modal==='mapping-source') {
    const available = (state.databaseTables[state.database]||[]).includes('portal_news');
    title='选择数据来源'; body=available?`<p>请选择用于接口映射的数据表。</p><label class="check"><input type="radio" name="source" checked>${safe(state.database)} · portal_news</label>`:'<p>尚未添加 portal_news 数据表。请先从左侧“数据源”新增数据库连接并添加数据表。</p>';
    buttons=action('取消','close-modal')+(available?action('确认选择','source-confirm','class="primary"'):action('打开数据源','open-data','class="primary"'));
  } else if (state.modal==='switch-phase') {
    title=`切换到${phases[state.pendingPhase]}阶段？`; body=`<p>这将返回上游继续调整。确认后进入${phases[state.pendingPhase]}阶段。</p>`; buttons=action('取消','close-modal')+action('确认切换','confirm-phase','class="primary"');
  } else if (state.modal==='accept-reject') {
    title='验收未通过'; body='<p>请输入需要调整的问题，提交后回到对话。</p><textarea id="accept-feedback" placeholder="描述需要修改的问题"></textarea>'; buttons=action('取消','close-modal')+action('提交意见','accept-feedback','class="primary"');
  } else if (state.modal==='checkpoint') {
    title='审阅并提交'; body='<p>为本次验收前的代码创建版本。</p><label for="checkpoint-note">变更说明 *</label><textarea id="checkpoint-note">完成门户首页及新闻列表接口。</textarea>'; buttons=action('取消','close-modal')+action('确认提交','checkpoint-confirm','class="primary"');
  } else if (state.modal==='release') {
    title='生成版本';
    titleDescription='基于本次改动生成版本 · 推送到 v1_0';
    const releaseNote = state.releaseNote || '门户首页已接入新闻资讯数据，按行情聚焦、战略推动、一线扫描、文化故事四个区块分类展示新闻列表，每条新闻直接呈现序号、标题、概要、维度、发布日期、来源与内容。';
    const instructions = ['打包本次全部改动（页面 / 接口 / 数据源 / 配置）','提交到本地仓库并推送到版本 v1_0','版本号对应远端仓库中的一个分支名，每个版本各自占一个分支','推送后该版本仍可继续开发，再次提交','下方填写的变更说明会作为本次的业务变更记录写入提交历史，后续可在该版本内追溯'];
    body=`<div class="notice"><strong>生成版本将执行以下操作：</strong><ul>${instructions.map(item=>`<li>${safe(item)}</li>`).join('')}</ul></div><label for="release-note">变更说明 *</label><textarea id="release-note" rows="3" maxlength="200" placeholder="请简要描述本次的主要变更内容">${safe(releaseNote)}</textarea><p class="hint"><span id="release-note-count">${[...releaseNote].length}</span> / 200</p><div class="grid"><div class="row"><strong>开发耗时（人天）：</strong></div><input id="studio-effort" type="number" min="0" step="0.5" aria-label="开发耗时（人天）" placeholder="人天" value="${safe(state.studioEffort)}"></div><div class="grid"><div class="row"><strong>以往开发耗时（人天）：</strong></div><div class="row productivity-effort-row"><select id="productivity-method" aria-label="以往开发方式"><option value="">请选择开发方式</option><option value="传统编程" ${state.productivityMethod==='传统编程'?'selected':''}>传统编程</option><option value="低代码" ${state.productivityMethod==='低代码'?'selected':''}>低代码</option></select><input id="previous-effort" type="number" min="0" step="0.5" aria-label="以往开发耗时（人天）" placeholder="人天" value="${safe(state.previousEffort)}"></div></div>`;
    buttons=action('取消','close-modal')+action('生成版本','release-confirm','class="primary"');
  }
  return `<div class="overlay"><div class="dialog small-dialog"><div class="dialog-head"><div><h2>${title}</h2>${titleDescription?`<p class="muted">${safe(titleDescription)}</p>`:''}</div><button data-action="close-modal" aria-label="关闭">×</button></div><div class="dialog-body">${body}</div><div class="dialog-foot">${buttons}</div></div></div>`;
}

// 顶部阶段、左侧对话和右侧产物共享同一工作台。
export function workbench() {
  const version = state.versionMenu?'<div class="version-menu"><button data-action="version-menu">v1_0　当前版本</button></div>':'';
  const full = state.panelLayout==='full' || acceptanceFocus();
  const hidden = state.panelLayout==='hidden'&&!full;
  return `<div class="wb-shell"><header class="wb-top"><a href="#home" class="wb-home-link" title="返回欢迎页"><span class="wb-brand">D</span><strong>DevAgent Studio</strong></a><span class="wb-separator"></span><strong class="wb-app-name">${safe(state.name)}</strong><div class="version-wrap"><button class="version-select" data-action="version-menu">v1_0　⌄</button>${version}</div><nav class="phase-nav" aria-label="工作阶段">${phases.map((p,i)=>`<span class="phase-cell"><button data-phase="${i}" class="${state.phase===i?'active':''}" ${i>state.reached?'disabled':''}>● ${p}阶段${i===2&&state.reached>=2?` <small class="phase-count">${state.completed.length}/2</small>`:''}</button>${i<5?'<span class="phase-arrow">→</span>':''}</span>`).join('')}</nav><button class="release-action" data-action="release" ${state.accepted&&!state.released?'':'disabled'}>${state.released?'版本已生成':'生成版本'}</button></header><div class="wb-layout has-workspace-panel ${full?'is-full':''} ${hidden?'is-hidden':''}">${full?'':rail()}${full?'':conversation()}${hidden?'':workspacePanel()}${panelLayoutControl(full)}</div>${auxiliaryDrawer()}${modal()}</div>`;
}
