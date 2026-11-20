import { state, safe } from '../state.js?v=20261122a';
import { exampleApi } from '../data/demo-case.js?v=20261122a';
import { icon } from './icons.js?v=20261121a';

const labels = {
  history: ['对话管理', '按需拆分上下文，当前阶段仅一条对话可继续推进'],
  async: ['异步任务', '常规算力队列，后台执行，消耗码豆。'],
  tide: ['潮汐任务', '闲时算力队列，低优先级执行，不消耗码豆。'],
  data: ['数据源', '已接入的数据库连接与数据表，按字段粒度绑定'],
  external: ['外部 API', '已登记的接口域与外部接口，按出入参粒度绑定'],
  files: ['文件', '管理 Agent 的角色与工作方式'],
  skills: ['技能', '查看用户和内置技能'],
  settings: ['应用设置', '应用基础能力与环境配置']
};
const taskNames = [];

// 渲染跨阶段使用的侧边抽屉内容。
function drawerContent() {
  if (state.pane === 'history') return `<div class="aux-tabs"><button data-aux="session-regular" class="${state.sessionTab==='常规对话'?'active':''}">常规对话 ${state.sessions.length}</button><button data-aux="session-temporary" class="${state.sessionTab==='临时对话'?'active':''}">临时对话 0</button><button data-aux="new-session">＋ 新建对话</button></div><p class="hint">仅「当前推进」的对话可执行工作流，其余对话仅供查看。</p>${state.sessionTab==='常规对话'?state.sessions.map((name,i)=>`<button class="aux-row ${i===state.sessionSelected?'selected':''}" data-session="${i}"><strong>${safe(name)}</strong><span>${i===state.sessions.length-1?'当前推进':'仅查看'}</span></button>`).join(''):'<p class="aux-empty">暂无临时对话</p>'}`;
  if (state.pane === 'async') return `<div class="aux-tabs"><button data-aux="queue-active" class="${state.queueTab==='进行中'?'active':''}">进行中 0</button><button data-aux="queue-done" class="${state.queueTab==='已完成'?'active':''}">已完成 ${taskNames.length}</button></div><p class="aux-empty">当前没有${state.queueTab}的任务。</p>`;
  if (state.pane === 'tide') return '<p class="aux-empty">当前没有潮汐任务。</p>';
  if (state.pane === 'data') return state.databases.length ? `<div class="aux-tabs">${state.databases.map(n=>`<button data-database="${safe(n)}" class="${state.database===n?'active':''}">${safe(n)}</button>`).join('')}<button data-aux="database-new">＋ 新建数据库连接</button></div><p class="hint">数据表按字段粒度参与应用 API 绑定。</p><div class="aux-actions"><button data-aux="database-settings">连接设置</button><button data-aux="tables-add">添加数据表</button></div>${(state.databaseTables[state.database]||[]).map(t=>`<button class="aux-row" data-table="${safe(t)}"><span>${icon('data')}　${safe(t)}　›</span></button>`).join('') || '<p class="aux-empty">尚未添加数据表。</p>'}` : '<div class="aux-empty"><p>暂无数据库连接</p><p>创建连接后，可选择数据库表并添加用于字段绑定的数据表。</p><button data-aux="database-new">＋ 新增数据库连接</button></div>';
  if (state.pane === 'external') return `<div class="aux-tabs">${state.domains.map(n=>`<button data-domain="${safe(n)}" class="${state.domain===n?'active':''}">${safe(n)}</button>`).join('')}<button data-aux="domain-new">＋ 新增域</button></div><p class="hint">接口按出入参粒度参与应用 API 适配。</p><div class="aux-actions"><button data-aux="domain-settings">域设置</button><button data-aux="api-new">新增接口</button></div>${state.domainApis[state.domain].map((item,i)=>`<button class="aux-row" data-api="${i}">${safe(item.method)}　${safe(item.name)}　›</button>`).join('') || '<p class="aux-empty">尚未添加接口。新增时已准备示例数据。</p>'}`;
  if (state.pane === 'files') return `<div class="aux-file-layout"><aside><strong>核心文件</strong><button class="aux-row">AGENTS.md</button></aside><div><div class="aux-actions"><strong>AGENTS.md</strong><button data-aux="save-file">保存</button></div><p class="hint">环境文件</p><label for="agent-file">内容</label><textarea id="agent-file" rows="16">${safe(state.agentFile)}</textarea></div></div>`;
  if (state.pane === 'skills') return `<div class="aux-tabs"><button data-aux="skills-user" class="${state.skillTab==='用户'?'active':''}">用户</button><button data-aux="skills-built-in" class="${state.skillTab==='内置'?'active':''}">内置</button></div><label for="skill-search">搜索技能</label><input id="skill-search" placeholder="搜索技能"><p class="aux-empty">${state.skillTab==='用户'?'暂无用户技能':'内置技能将在此展示'}</p>`;
  if (state.pane === 'settings') return `<form id="settings-form"><div class="aux-settings-nav">基础信息　 /　导航模式　 /　认证　 /　页面埋点　 /　接口埋点　 /　环境变量</div><h3>基础信息</h3><label for="settings-name">应用名称</label><input id="settings-name" required value="${safe(state.name)}"><label for="settings-desc">应用场景</label><textarea id="settings-desc">${safe(state.description)}</textarea><h3>导航模式</h3><div class="inline-options"><label><input type="radio" name="layout" value="左侧导航" ${state.layout==='左侧导航'?'checked':''}>左侧导航</label><label><input type="radio" name="layout" value="顶部导航" ${state.layout==='顶部导航'?'checked':''}>顶部导航</label><label><input type="radio" name="layout" value="混合导航" ${state.layout==='混合导航'?'checked':''}>混合导航</label></div><label class="check"><input type="checkbox" name="header" ${state.useHeader?'checked':''}>开启头部</label><label class="check"><input type="checkbox" name="footer" ${state.useFooter?'checked':''}>开启底部</label><h3>认证与埋点</h3><label class="check"><input type="checkbox" name="auth" ${state.authEnabled?'checked':''}>启用认证</label><label class="check"><input type="checkbox" name="pageTracking" ${state.pageTracking?'checked':''}>页面埋点</label><label class="check"><input type="checkbox" name="apiTracking" ${state.apiTracking?'checked':''}>接口埋点</label><h3>环境变量</h3><button type="button" data-aux="environment-add">＋ 新增环境变量</button><div id="environment-fields"></div><div class="aux-actions"><button class="primary">保存设置</button></div></form>`;
  return '';
}

// 数据源和 API 的详情展示在抽屉旁，不替换主工作区。
function detail() {
  if (!state.auxDetail) return '';
  const apiNew = state.auxDetail === 'api-new';
  const api = state.auxDetail.startsWith('api:');
  const item = api ? state.domainApis[state.domain][Number(state.auxDetail.slice(4))] : null;
  const title = apiNew ? '新增外部 API' : api ? (item?.name || '外部 API') : state.auxDetail.slice(6);
  const fields = title === 'portal_news' ? [['id','数字','序号'],['title','文本','标题'],['summary','文本','摘要'],['dimension','文本','维度'],['publish_date','日期','发布日期'],['source','文本','来源'],['content','长文本','内容']] : [['id','字符串','记录标识'],['status','字符串','处理状态'],['created_at','日期','创建时间']];
  const tableBody = `<div class="table-source"><strong>${safe(state.database)}</strong>　<span class="tag">数据库直连</span><p class="hint">表结构只读，绑定映射按字段粒度引用。</p></div><h3>字段（${fields.length}）</h3><table class="video-table source-field-table"><thead><tr><th>字段名</th><th>类型</th><th>业务说明</th></tr></thead><tbody>${fields.map(row=>`<tr>${row.map(value=>`<td>${safe(value)}</td>`).join('')}</tr>`).join('')}</tbody></table><div class="aux-actions"><button type="button" data-aux="detail-close">返回</button></div>`;
  const body = apiNew ? `<form id="api-form"><p class="hint">已填入演示数据，可直接保存或修改。</p><label>接口名称</label><input name="name" value="${safe(exampleApi.name)}" required><label>接口说明</label><input name="description" value="${safe(exampleApi.description)}"><label>请求方式和地址</label><div class="row"><select name="method"><option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option></select><input name="url" value="${safe(exampleApi.url)}" required aria-label="接口地址"></div><h3>Headers</h3><p class="hint">无</p><h3>入参</h3><input name="request" aria-label="入参" value="${safe(exampleApi.request)}"><h3>出参（Response）</h3><input name="response" aria-label="出参" value="${safe(exampleApi.response)}"><div class="aux-actions"><button type="button" data-aux="detail-close">取消</button><button class="primary">保存</button></div></form>` : api && item ? `<h3>接口定义</h3><p>${safe(item.description)}</p><p><strong>${safe(item.method)}</strong>　${safe(item.url)}</p><h3>入参</h3><p>${safe(item.request)}</p><h3>出参（Response）</h3><p>${safe(item.response)}</p>` : tableBody;
  return `<section class="aux-detail" aria-label="数据来源详情"><header><div><strong>${safe(title)}</strong><small>查看和维护数据来源</small></div><button data-aux="detail-close" aria-label="关闭详情">×</button></header><div class="aux-body">${body}</div></section>`;
}

// 公共表单弹框覆盖工作台，关闭后保留当前抽屉。
function auxModal() {
  if (!state.auxModal) return '';
  const database = state.auxModal === 'database-new' || state.auxModal === 'database-settings';
  const domain = state.auxModal === 'domain-new' || state.auxModal === 'domain-settings';
  const title = database ? (state.auxModal==='database-new'?'新建数据库连接':'连接设置') : domain ? (state.auxModal==='domain-new'?'新增域':'域设置') : '添加数据表';
  const config = state.auxModal==='database-settings' ? state.databaseConfigs[state.database] || {} : {};
  const dbid = config.mode==='DBID';
  const content = database ? `<label>连接名称 *</label><input name="name" required value="${state.auxModal==='database-settings'?safe(state.database):'新闻数据库'}"><label>连接方式</label><div class="inline-options"><label><input type="radio" name="mode" value="本地直连" ${dbid?'':'checked'}>数据库直连</label><label><input type="radio" name="mode" value="DBID" ${dbid?'checked':''}>DBID</label></div><div data-connection="DBID" ${dbid?'':'hidden'}><label>DBID *</label><input name="dbid" placeholder="请输入数据库标识" value="${safe(config.dbid||'')}" required ${dbid?'':'disabled'}></div><div data-connection="本地直连" ${dbid?'hidden':''}><label>数据库地址 *</label><input name="host" placeholder="主机地址或 IP" value="${safe(config.host||'127.0.0.1')}" required ${dbid?'disabled':''}><label>端口 *</label><input name="port" value="${safe(config.port||'3306')}" required ${dbid?'disabled':''}><label>Schema *</label><input name="schema" value="${safe(config.schema||'portal')}" required ${dbid?'disabled':''}><label>用户名 *</label><input name="user" value="${safe(config.user||'demo')}" required ${dbid?'disabled':''}><label>密码 ${state.auxModal==='database-new'?'*':'（留空表示不修改）'}</label><input name="password" type="password" value="${state.auxModal==='database-new'?'demo':''}" ${state.auxModal==='database-new'?'required':''} ${dbid?'disabled':''}></div>` : domain ? `<label>域名称</label><input name="name" required value="${state.auxModal==='domain-settings'?safe(state.domain):''}"><label>域名地址</label><input name="url" placeholder="https://example.com"><label>说明</label><input name="description">` : `<p>勾选「${safe(state.database)}」中要添加的数据表</p><label>筛选数据表</label><input placeholder="筛选表名或说明"><label class="check"><input type="checkbox" name="tables" value="market_news">market_news　新闻表 · 9 字段</label><label class="check"><input type="checkbox" name="tables" value="personnel_info">personnel_info　人员信息表 · 9 字段</label><label class="check"><input type="checkbox" name="tables" value="portal_news" checked>portal_news　新闻资讯 · 7 字段</label>`;
  return `<div class="overlay aux-overlay"><form class="dialog small-dialog" id="aux-form"><div class="dialog-head"><h2>${title}</h2><button type="button" data-aux="modal-close" aria-label="关闭">×</button></div><div class="dialog-body">${content}</div><div class="dialog-foot"><button type="button" data-aux="modal-close">取消</button>${database?'<button type="button" data-aux="connection-test">检测连接</button>':''}<button class="primary">${database||domain?'保存':'添加数据表'}</button></div></form></div>`;
}

export function auxiliaryDrawer() {
  if (!state.pane || !labels[state.pane]) return '';
  const [title, subtitle] = labels[state.pane];
  return `<section class="aux-drawer ${state.pane==='settings'?'aux-drawer-wide':''}" aria-label="${title}"><header><div><strong>${title}</strong><small>${subtitle}</small></div><button data-aux="close" aria-label="关闭辅助抽屉">×</button></header><div class="aux-body">${drawerContent()}</div></section>${detail()}${auxModal()}`;
}
