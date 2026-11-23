import { state, safe } from '../state.js?v=20261122a';
import { action, agentHeader, message, portalContent, workflowCard } from '../components/video-common.js?v=20261122a';
import { icon } from '../components/icons.js?v=20261123d';

const api = '查询新闻列表';
const page = '门户首页';
const backendTasks = ['配置 MySQL 数据源与公共基础能力','定义新闻列表查询的请求、响应与内部对象','实现 portal_news 新闻列表数据库访问','实现新闻列表字段映射、分页与响应组装','实现 GET /api/news 端点控制器'];
const frontendTasks = [...backendTasks,'实现新闻列表接口前端访问模块','实现门户首页四维度新闻列表展示'];
const returnFields = [['total','业务处理'],['pageSize','接口参数'],['current','接口参数'],['list[].sequenceNumber','id'],['list[].title','title'],['list[].summary','summary'],['list[].dimension','dimension'],['list[].publishDate','publish_date'],['list[].source','source'],['list[].content','content']];

// 显示当前要开发的页面或 API 及主要工作流。
export function developmentConversation() {
  const started = Boolean(state.buildStep);
  if (!started) {
    return `<div class="quick-guide video-quick"><h2>今天想从哪里开始？</h2><p>选择一个页面或接口开始正式任务，也可以直接在下方自由对话。</p><div class="task-groups"><section><strong>页面　1</strong><button class="task-row" data-build="${page}" data-kind="page"><span><b>门户首页</b><br><small>/page/home</small></span><small>${state.completed.includes(page)?'已完成':'未开发'}</small></button></section><section><strong>接口　1</strong><button class="task-row" data-build="${api}" data-kind="api"><span><b>查询新闻列表</b><br><small>GET /api/news</small></span><small>${state.completed.includes(api)?'已完成':'未开发'}</small></button></section></div></div>`;
  }
  const isPage = state.targetKind === 'page';
  const heading = `<div class="current-target"><strong>${safe(state.target)}</strong><small>${isPage?'/page/home':'GET /api/news'}　·　${state.buildStep==='complete'?'已完成':'进行中'}</small>${isPage&&state.completed.includes(page)?action('打开预览','open-preview'):''}</div>`;
  if (state.buildStep === 'mapping') {
    const status = state.mappingConfirmed ? '已配置，待检测' : '待配置';
    const body = `<div class="notice">部分接口配置需要重新确认，请检查并保存后继续。<br><small>可逐项保存字段映射，全部配置完成后点击确认统一检测。</small></div><p>相关接口　1 个接口</p><div class="simple-card"><strong>GET　/api/news</strong>　<span class="tag">${status}</span> ${action(state.mappingConfirmed?'查看映射':'配置映射','open-mapping')}</div>`;
    return heading + message(`开始开发${isPage?'页面：门户首页':'接口：GET /api/news'}`) + agentHeader(2) + workflowCard(body,action('确认并检测','mapping-check',`class="primary" ${state.mappingConfirmed?'':'disabled'}`));
  }
  if (state.buildStep === 'task-ready') {
    return heading + agentHeader(2) + `<div class="process-list"><p>✓　已完成 API 设计前置检查</p><p>✓　已执行扫描工作区代码</p><p>●　等待确认生成开发任务计划</p></div>` + workflowCard('<strong>任务生成完成</strong><p>任务计划已准备好，请确认后继续开发。</p>',action('去确认','view-task-plan','class="primary"'));
  }
  if (state.buildStep === 'running') {
    const tasks = isPage ? frontendTasks : backendTasks;
    return heading + agentHeader(2) + `<div class="process-list"><p>✓　生成开发任务计划</p><p>●　代码生成与构建</p></div>` + workflowCard(`<h3>构建执行</h3><p>执行进度　${tasks.length} / ${tasks.length}</p><div class="video-progress"><span></span></div><div class="stats-row"><span>待执行 0</span><span>执行中 0</span><span>已完成 ${tasks.length}</span><span>失败 0</span></div>${tasks.slice(0,3).map(t=>`<div class="list-row"><span>✓　${t}</span><small>完成</small></div>`).join('')}`,action('查看执行结果','build-result','class="primary"'),'已完成');
  }
  if (state.buildStep === 'complete') {
    const remaining = [{name:page,kind:'page'},{name:api,kind:'api'}].find(item=>!state.completed.includes(item.name));
    const both = !remaining;
    const files = isPage ? ['frontend/pages/home.tsx','frontend/services/newsApi.ts'] : ['backend/NewsController.java','backend/NewsApplicationService.java','backend/NewsMapper.xml'];
    const body = `<strong>本轮开发已完成</strong><p>已完成 / 全部目标　${state.completed.length} / 2　　应用页面 1　　应用 API 1</p>${both?'<p>页面与接口产物已就绪，可从右侧「页面调试」「接口调试」进入联调，所需应用服务会自动启动。</p>':`<p>推荐下一项：${remaining.name}</p>`}`;
    const next = both ? (state.isDemo ? '<button type="button" class="primary" data-phase="3">查看测试报告</button>' : action('进入测试阶段','go-test','class="primary"')) : `<button type="button" class="primary" data-build="${remaining.name}" data-kind="${remaining.kind}">开始开发 →</button>`;
    return heading + agentHeader(2) + `<div class="simple-card"><div class="row between"><strong>文件改动　${files.length} 个文件已变更</strong><div>${action('查看变更','review-changes')}</div></div>${files.map(f=>`<p>${f}</p>`).join('')}</div>` + workflowCard(body,next,'已完成');
  }
  return heading;
}

// 读取指定服务当前的状态、端口和日志行。
function serviceInfo(kind) {
  const frontend=kind==='frontend';
  const title=frontend?'前端服务':'后端服务';
  const running=frontend?state.frontendServiceRunning:state.backendServiceRunning;
  const starting=Boolean(state.serviceStarting?.[kind]);
  const failed=state.serviceRepairStep==='plan'&&state.serviceFailure.startsWith(frontend?'前端':'后端');
  const fallbackLogs=frontend
    ? ['[安装 · stdout] frontend-install.stdout.log\n依赖安装完成','[启动 · stdout] frontend.stdout.log\nVite 服务已就绪']
    : ['[构建 · stdout] backend-build.stdout.log\n后端构建完成','[启动 · stdout] backend.stdout.log\nAPI 服务已就绪'];
  const storedLogs=state.serviceLogs?.[kind]||[];
  return {frontend,title,running,starting,failed,status:failed?'启动失败':starting?'启动中':running?'运行中':'未启动',port:frontend?'5173':'8000',logs:failed?[state.serviceFailure]:(storedLogs.length?storedLogs:running?fallbackLogs:[]),following:state.serviceLogFollowing?.[kind]!==false};
}

// 在地址栏状态区显示运行状态，并从浮层管理对应服务。
function debugServiceToolbar(kind) {
  const info=serviceInfo(kind);
  return `<div class="debug-address-services"><div class="debug-service-wrap"><button class="debug-service-status-trigger" data-action="toggle-debug-service-menu" aria-label="${info.title}状态" title="${info.title}维护与日志" aria-expanded="${state.debugServiceMenuOpen}"><span class="debug-service-name">${info.title}</span><span class="tag">${info.status}</span><span class="dd-chevron" aria-hidden="true"></span></button>${state.debugServiceMenuOpen?serviceManagementPanel(kind):''}</div><button class="debug-log-toggle ${state.debugLogOpen?'selected':''}" data-action="toggle-debug-log" aria-pressed="${state.debugLogOpen}">日志</button></div>`;
}

// 在地址栏日志按钮对应的底部面板中显示当前服务的完整日志。
function serviceLogPanel(kind) {
  const info=serviceInfo(kind);
  const logRows=info.logs.map(line=>`<span data-service-log-line>${safe(line)}</span>`).join('\n');
  return `<section class="debug-log-dock"><div class="debug-log-dock-head"><strong>${info.title}日志</strong><div class="service-log-toolbar-actions"><button data-action="service-copy-all-${kind}" ${info.logs.length?'':'disabled'}>复制日志</button><button data-action="service-follow-${kind}">${info.following?'暂停跟随':'恢复跟随'}</button><button data-action="close-debug-log" aria-label="关闭日志面板">×</button></div></div><pre class="service-log-content" data-service-log-panel="${kind}" aria-label="${info.title}日志">${logRows || '本次服务暂无日志'}</pre></section>`;
}

// 在地址栏内提供已完成页面或接口产物的树形快捷选择；未完成的产物不出现在下拉中。
function debugArtifactPicker(kind) {
  const pageTarget=kind==='page';
  const label=pageTarget?page:api;
  if (!state.completed.includes(label)) return `<details class="debug-target-picker"><summary>暂无可调试${pageTarget?'页面':'接口'}<span class="dd-chevron" aria-hidden="true"></span></summary><div class="debug-target-tree" role="tree"><button type="button" class="debug-target-item" disabled>暂无可调试${pageTarget?'页面':'接口'}</button></div></details>`;
  return `<details class="debug-target-picker"><summary>${label}<span class="dd-chevron" aria-hidden="true"></span></summary><div class="debug-target-tree" role="tree"><div class="debug-target-group">${pageTarget?'页面':'接口'}</div><button class="debug-target-item ${state.selectedArtifact===(pageTarget?'page':'api')?'selected':''}" data-select-debug-artifact="${pageTarget?'page':'api'}" data-debug-url="${pageTarget?'http://localhost:5173/page/home':'http://localhost:8000/api/news'}" role="treeitem">${label}</button></div></details>`;
}

// 产物未完成时调试面板仍可进入：地址栏保留但目标下拉无可选项，内容区提示先完成开发。
function debugEmptyPanel(kind) {
  const isPage=kind==='page';
  const artifact=isPage?page:api;
  const addressRow=isPage
    ? `<span class="debug-preview-nav">‹　›　↻</span><div class="debug-address-field-group">${debugArtifactPicker('page')}<input id="page-debug-url" aria-label="预览地址" value="about:blank"></div><button data-action="preview-visit">访问</button>`
    : `${debugArtifactPicker('api')}<details class="debug-target-picker debug-method-picker"><summary>${state.apiDebugMethod}<span class="dd-chevron" aria-hidden="true"></span></summary><div class="debug-target-tree" role="tree">${['GET','POST','PUT','PATCH','DELETE'].map(method=>`<button class="debug-target-item ${state.apiDebugMethod===method?'selected':''}" data-api-method="${method}" role="treeitem">${method}</button>`).join('')}</div></details><div class="debug-address-field-group"><input id="api-debug-url" aria-label="请求地址" value="about:blank"></div><button data-action="api-call">发送</button>`;
  return `<div class="${isPage?'page-debugger':'api-debugger'}"><div class="debug-header-stack"><div class="api-request-row debug-address-row">${addressRow}</div></div><div class="${isPage?'page-debug-content':'api-debug-content'}"><div class="panel-empty">完成「${artifact}」开发后即可在此${isPage?'选择页面并预览调试':'选择接口并发送调试请求'}。</div></div></div>`;
}

// 按 Postman 的基本请求与响应区域展示当前 API 契约调试入口；接口产物未完成时只提供空目标下拉。
export function apiDebugPanel() {
  if (!state.completed.includes(api)) return debugEmptyPanel('api');
  const params=state.apiDebugParams;
  const requestTabs=['Params','Authorization','Headers','Body'];
  const responseTabs=['Body','Headers','Test Results'];
  const parameterRows=[['dimension','新闻维度'],['current','当前页码'],['pageSize','每页条数']]
    .map(([key,description])=>`<tr><td><input aria-label="启用 ${key}" type="checkbox" data-api-param-enabled="${key}" ${state.apiDebugEnabledParams[key]?'checked':''}></td><td>${key}</td><td><input aria-label="${key} 参数" data-api-param="${key}" value="${safe(params[key])}"></td><td>${description}</td></tr>`)
    .join('');
  const requestContent=state.apiRequestTab==='Params'
    ? `<table class="video-table"><thead><tr><th>启用</th><th>Key</th><th>Value</th><th>Description</th></tr></thead><tbody>${parameterRows}</tbody></table>`
    : state.apiRequestTab==='Authorization'
      ? `<div class="simple-card"><strong>${safe(state.apiDebugAuth)}</strong><p>当前 API 契约无需鉴权。</p></div>`
      : state.apiRequestTab==='Headers'
        ? `<table class="video-table"><thead><tr><th>启用</th><th>Key</th><th>Value</th></tr></thead><tbody><tr><td><input aria-label="启用 Accept Header" type="checkbox" data-api-header-enabled="Accept" ${state.apiDebugHeaderEnabled.Accept?'checked':''}></td><td>Accept</td><td><input aria-label="Accept Header 值" data-api-header="Accept" value="${safe(state.apiDebugHeaders.Accept)}"></td></tr></tbody></table>`
        : state.apiDebugMethod==='GET'
          ? '<div class="simple-card"><strong>Request Body</strong><p>GET 方法无需请求体。</p></div>'
          : `<textarea id="api-debug-body" aria-label="请求体" rows="5">${safe(state.apiDebugBody)}</textarea>`;
  const responseStatus=state.apiCallStatus==='success'
    ? '<span class="api-response-status">200 OK</span>'
    : state.apiCallStatus==='connection-error'
      ? '<span class="api-response-status">连接失败</span>'
      : state.apiCallStatus==='method-error'
        ? '<span class="api-response-status">405 Method Not Allowed</span>'
        : '<span class="api-response-status">尚未发送</span>';
  const responseObject={
    total:2,
    current:Number(params.current)||1,
    pageSize:Number(params.pageSize)||10,
    list:[{title:'新闻标题',dimension:String(params.dimension||'')}]
  };
  const responseContent=!state.apiCalled
    ? '<div class="api-response-empty">发送请求后显示响应状态与内容。</div>'
    : state.apiCallStatus==='connection-error' || state.apiCallStatus==='method-error'
      ? `<div class="notice">${safe(state.apiCallError)}</div>`
      : state.apiResponseTab==='Body'
        ? `<div class="api-response"><pre>${safe(JSON.stringify(responseObject,null,2))}</pre></div>`
        : state.apiResponseTab==='Headers'
          ? '<table class="video-table"><thead><tr><th>Key</th><th>Value</th></tr></thead><tbody><tr><td>Content-Type</td><td>application/json</td></tr></tbody></table>'
          : '<div class="simple-card"><p>✓　状态码为 200</p><p>✓　响应包含新闻列表。</p></div>';
  return `<div class="api-debugger"><div class="debug-header-stack">
    <div class="api-request-row debug-address-row">${debugArtifactPicker('api')}<details class="debug-target-picker debug-method-picker"><summary>${state.apiDebugMethod}<span class="dd-chevron" aria-hidden="true"></span></summary><div class="debug-target-tree" role="tree">${['GET','POST','PUT','PATCH','DELETE'].map(method=>`<button class="debug-target-item ${state.apiDebugMethod===method?'selected':''}" data-api-method="${method}" role="treeitem">${method}</button>`).join('')}</div></details><div class="debug-address-field-group"><input id="api-debug-url" aria-label="请求地址" value="${safe(state.apiDebugUrl)}"></div><button data-action="api-call">发送</button>${debugServiceToolbar('backend')}</div>
  </div><div class="api-debug-content"><p class="hint">API 契约预填请求参数。</p>
    <div class="subtabs">${requestTabs.map(tab=>`<button data-api-request-tab="${tab}" class="${state.apiRequestTab===tab?'selected':''}">${tab}${tab==='Params'?' ('+Object.values(state.apiDebugEnabledParams).filter(Boolean).length+')':''}</button>`).join('')}</div>
    ${requestContent}
    <section class="api-response-section"><div class="row between"><strong>Response</strong>${responseStatus}</div>${state.apiCalled?`<p class="hint">${state.apiCallStatus==='success'?'24 ms · 1.03 KB':'0 ms · 0 B'}</p>`:''}<div class="subtabs">${responseTabs.map(tab=>`<button data-api-response-tab="${tab}" class="${state.apiResponseTab===tab?'selected':''}">${tab}</button>`).join('')}</div>${responseContent}</section>
  </div>${state.debugLogOpen?serviceLogPanel('backend'):''}</div>`;
}

// 按培训视频保留页面、接口与实体的产物目录和查看内容。
export function artifactsPanel() {
  const status = name => state.completed.includes(name) ? '已完成' : state.target===name && state.buildStep ? '进行中' : '未开始';
  const selectedArtifact = state.selectedArtifact || 'page';
  const pageDetail = `<div class="artifact-heading"><div><strong>门户首页</strong><small>页面产物　·　/page/home　·　${status(page)}</small></div>${state.completed.includes(page)?action('进入页面调试','open-debug-preview'):'<span class="tag">完成开发后可调试</span>'}</div><p>面向苏州分行员工与访客，按行情聚焦、战略推动、一线扫描、文化故事展示新闻资讯。</p><section class="simple-card"><strong>页面信息</strong><p>页面路由：/page/home</p><p>数据来源：查询新闻列表　GET /api/news</p></section>`;
  const apiDetail = `<div class="artifact-heading"><div><strong>查询新闻列表</strong><small>接口产物　·　GET /api/news　·　${status(api)}</small></div>${state.completed.includes(api)?action('进入接口调试','open-api-debug'):'<span class="tag">完成开发后可调试</span>'}</div><p>按新闻维度分页查询门户新闻，供门户首页各资讯区块展示。</p><section class="simple-card"><strong>请求参数</strong><table class="video-table"><thead><tr><th>参数</th><th>类型</th><th>说明</th></tr></thead><tbody><tr><td>dimension</td><td>文本</td><td>新闻维度</td></tr><tr><td>current</td><td>数字</td><td>当前页码</td></tr><tr><td>pageSize</td><td>数字</td><td>每页条数</td></tr></tbody></table></section><section class="simple-card"><strong>响应字段</strong><p>total、current、pageSize、list</p><p>list：标题、摘要、维度、发布日期、来源与内容</p></section>`;
  const detail = selectedArtifact==='entity'
    ? '<div class="artifact-heading"><div><strong>新闻资讯</strong><small>实体　·　开发中</small></div></div><section class="simple-card"><strong>敬请期待</strong><p>实体功能还在开发中，当前版本暂未开放。</p></section>'
    : selectedArtifact==='api' ? apiDetail : pageDetail;
  return `<div class="artifact-layout"><aside class="artifact-tree"><input aria-label="搜索产物" placeholder="搜索页面、接口或实体"><p>⌄　页面　${state.completed.includes(page)?'1':'0'} / 1</p><button data-artifact="page" class="${selectedArtifact==='page'?'selected':''}">　${icon('page')}　门户首页 <small>${status(page)}</small></button><p>⌄　接口　${state.completed.includes(api)?'1':'0'} / 1</p><button data-artifact="api" class="${selectedArtifact==='api'?'selected':''}">　${icon('api')}　查询新闻列表 <small>${status(api)}</small></button><p>⌄　实体　1　<small>开发中</small></p><button data-artifact="entity" class="${selectedArtifact==='entity'?'selected':''}">　${icon('data')}　新闻资讯 <small>敬请期待</small></button></aside><div class="artifact-detail">${detail}</div></div>`;
}

// 在地址栏服务状态下拉区展示对应服务的启停、维护和修复操作。
function serviceManagementPanel(kind) {
  const info=serviceInfo(kind);
  const mainAction=info.running?action('停止服务',`service-stop-${kind}`):info.starting?'<button type="button" disabled>启动中…</button>':action('启动服务',`service-start-${kind}`,'class="primary"');
  const restartAction=info.running?action('重启服务',`service-restart-${kind}`):'';
  return `<section class="debug-service-menu"><div class="row between"><strong>${info.title}维护</strong><span class="tag">${info.status} · 端口 ${info.port}</span></div><div class="service-actions">${mainAction}${restartAction}${action('诊断并修复','service-diagnose')}</div>${info.failed?`<div class="service-failure">${safe(state.serviceFailure)}</div><section class="simple-card service-repair-plan"><div class="row between"><strong>启动失败修复计划</strong><span class="tag">待确认</span></div><p>检查${info.title}启动配置，修复后重新启动当前服务。</p>${action('确认并修复','service-repair-confirm','class="primary"')}</section>`:''}</section>`;
}

// 数据来源、查询条件、返回字段构成配置表单；AI 生成映射一键落表，仅在匹配异常的字段旁标注星光警告。
export function mappingPanel() {
  const sourceAvailable = (state.databaseTables[state.database]||[]).includes('portal_news');
  const sourceReady = sourceAvailable && state.mappingSourceSelected;
  const hints = state.mappingHints||{};
  const aiButton = state.mappingGenerating ? '<button type="button" disabled>生成中…</button>' : action('AI 生成映射','mapping-generate');
  const rows = returnFields.map(([f])=>{
    const selected=state.fieldMappings[f]||'';
    const hint=hints[f];
    const hintIcon=hint?`<span class="mapping-ai-hint ${hint.level==='warn'?'warn':'info'}" data-tip="${safe(hint.text)}" aria-label="${safe(hint.text)}">${icon('ai')}</span>`:'';
    const options=['','业务处理','pageSize','current','id','title','summary','dimension','publish_date','source','content'];
    return `<tr><td>${f}</td><td>${selected?'数据来源 / 接口参数':'待配置'}</td><td><span class="mapping-field-cell${hint?` hint-${hint.level==='warn'?'warn':'info'}`:''}"><select name="mapping-field" data-field="${f}" aria-label="${f} 的取值内容">${options.map(v=>`<option value="${v}" ${v===selected?'selected':''}>${v||'请选择'}</option>`).join('')}</select>${hintIcon}</span></td></tr>`;
  }).join('');
  const mappingReady = returnFields.every(([f])=>Boolean(state.fieldMappings[f]));
  const sourceChoice = `<div class="source-types"><button type="button" class="selected">数据库</button><button type="button" data-action="source-external">外部 API</button><button type="button" data-action="source-static">静态数据</button></div><label for="mapping-source-table">选择已添加的数据表</label><select id="mapping-source-table" name="mapping-source-table"><option value="">请选择数据表</option>${sourceAvailable?`<option value="portal_news">${safe(state.database)} · portal_news</option>`:''}</select>${sourceAvailable?'':'<p class="hint">尚未添加 portal_news，请先在数据源中添加。</p>'}`;
  // 上半区压缩：来源/操作/查询条件合并为一行元信息，说明压成两行，减少返回字段表的滚动距离。
  const sourceCard = sourceReady
    ? `<section class="simple-card"><div class="row between"><span class="mapping-meta-line"><span><strong>数据来源</strong>　${safe(state.database)} · portal_news</span><span><strong>数据库操作</strong>　查询</span></span>${action('更换数据源','mapping-source')}</div><label class="mapping-desc-label" for="mapping-description">接口映射说明</label><textarea id="mapping-description" rows="2" aria-label="接口映射说明">${safe(state.mappingDescription)}</textarea></section><section class="simple-card"><strong>查询条件</strong><div class="grid"><span>dimension　等于</span><span>接口参数　dimension</span></div></section>`
    : `<section class="simple-card"><strong>数据来源</strong>${sourceChoice}</section>`;
  return `<div class="artifact-layout mapping-layout"><aside class="artifact-tree"><strong>应用 API</strong><p>⌄　门户新闻查询</p><button class="selected">　${icon('api')}　查询新闻列表</button></aside><div class="mapping-content"><div class="row between"><div><p class="hint">门户新闻查询</p><h2>查询新闻列表</h2><p>GET　/api/news</p></div><div class="actions">${sourceReady?`${aiButton} `:''}${action('暂存','mapping-draft')} ${action('保存并确认','mapping-save',`class="primary" ${sourceReady && mappingReady?'':'disabled'}`)}</div></div>${sourceCard}${sourceReady?`<section class="simple-card"><strong>返回字段</strong>${Object.keys(hints).length?'<p class="hint">悬停警告图标查看 AI 分析与建议。</p>':''}<table class="video-table"><thead><tr><th>返回字段</th><th>值来源</th><th>取值内容</th></tr></thead><tbody>${rows}</tbody></table></section>`:''}</div></div>`;
}

// 用户确认开发任务计划后，才继续模拟执行。
export function taskPlanPanel() {
  const isPage = state.targetKind==='page';
  const tasks = isPage?frontendTasks:backendTasks;
  return `<div class="document-content task-plan"><div class="row between"><div><h2>任务计划待确认</h2><p class="muted">请核对本次开发目标、修改范围和验收标准，确认后执行任务。</p></div><span class="tag">${tasks.length} 个任务</span></div><h3>本次开发目标与范围</h3><div class="simple-card"><strong>${isPage?'门户首页　/page/home':'查询新闻列表　GET /api/news'}</strong><p>${isPage?'按行情聚焦、战略推动、一线扫描、文化故事四个区块展示新闻。':'按维度查询门户首页新闻列表，返回新闻的序号、标题、摘要、维度、发布日期、来源与内容。'}</p></div><h3>本次开发任务</h3>${tasks.map((t,i)=>`<details class="task-detail"><summary>${i+1}.　${t}　<span class="hint">${isPage&&i<5&&state.completed.includes(api)?'复用':'新增'}</span></summary><p>范围：${t}；完成后进行构建检查。</p></details>`).join('')}<div class="task-plan-actions">${action('放弃当前规划','task-discard')} ${action('重新生成','task-regenerate')} ${action('确认并执行任务','task-confirm','class="primary"')}</div></div>`;
}

// 展示应用文档与主要源码文件，延续培训视频中的项目文件浏览入口。
export function applicationFilesPanel() {
  const files = [
    ['docs/requirement-spec.md','需求规格说明书','面向苏州分行员工与访客的门户，展示四类新闻资讯。'],
    ['docs/product-plan.md','产品计划','门户首页包含行情聚焦、战略推动、一线扫描、文化故事四个资讯区块。'],
    ['docs/ui-designs.json','UI 设计稿','页面采用顶部应用标识、首页菜单与分区新闻列表布局。'],
    ['docs/technical-plan.md','技术规划方案','前端页面通过 GET /api/news 获取新闻；后端从 portal_news 数据表读取数据。'],
    ['docs/project-plan.md','项目计划','完成门户首页、新闻列表查询接口、数据映射和构建检查。'],
    ['README.md','项目说明','苏州分行门户示例项目，记录应用用途与本地开发入口。'],
    ['frontend/pages/home.tsx','门户首页源码','前端页面呈现四个新闻区块，按 dimension 参数请求新闻列表。'],
    ['backend/news-controller.java','新闻接口源码','提供 GET /api/news，支持 dimension、current、pageSize 参数。']
  ];
  const selectedPath = state.selectedFilePath || files[0][0];
  const selected = files.find(([path])=>path===selectedPath) || files[0];
  const fileButton = ([path,label])=>`<button data-file="${path}" class="${path===selected[0]?'selected':''}">${safe(label)} <small>${path}</small></button>`;
  const docs = files.filter(([path])=>path.startsWith('docs/')||path==='README.md').map(fileButton).join('');
  const frontend = files.filter(([path])=>path.startsWith('frontend/')).map(fileButton).join('');
  const backend = files.filter(([path])=>path.startsWith('backend/')).map(fileButton).join('');
  return `<div class="artifact-layout"><aside class="artifact-tree"><strong>项目文件</strong><p>⌄　文档</p>${docs}<p>⌄　frontend</p>${frontend}<p>⌄　backend</p>${backend}</aside><div class="artifact-detail"><div class="artifact-heading"><div><strong>${safe(selected[1])}</strong><small>${safe(selected[0])}</small></div><span>只读</span></div><section class="simple-card"><strong>文件内容摘要</strong><p>${safe(selected[2])}</p></section></div></div>`;
}

// 渲染应用页面；后端联调入口只在接口产物完成后开放，服务启动中给出过渡提示。
function pagePreviewContent(showServiceLinks) {
  const frontReady = state.frontendServiceRunning;
  const frontStarting = Boolean(state.serviceStarting?.frontend);
  const backReady = state.backendServiceRunning;
  const backStarting = Boolean(state.serviceStarting?.backend);
  const apiDone = state.completed.includes(api);
  if (!frontReady) return `<div class="panel-empty">${frontStarting?'前端服务启动中，页面预览即将就绪。':'页面预览需要前端服务。'}${!frontStarting&&showServiceLinks?action('前往前端服务','service-open-frontend'):''}</div>`;
  const backendNotice = backReady ? portalContent() : `<div class="notice">${backStarting?'后端服务启动中，新闻数据接口即将可用。':'前端页面可预览；后端服务未启动，新闻数据接口不可用。'}${apiDone?action('前往后端服务','service-open-backend'):'<p class="hint">完成「查询新闻列表」接口开发后即可联调数据。</p>'}</div>`;
  return `<div class="portal-shell"><aside><strong>${safe(state.name)}</strong><p>首页</p></aside><div class="preview-page-content">${backendNotice}</div></div>`;
}

// 验收阶段仅保留常规预览工具栏和页面内容。
export function previewPanel() {
  const address = state.frontendServiceRunning?safe(state.pageDebugUrl || 'http://localhost:5173'):'about:blank';
  return `<div class="video-preview"><div class="preview-toolbar row"><span>‹　›　↻</span><input aria-label="预览地址" value="${address}"><button data-action="preview-visit">访问</button></div>${pagePreviewContent(true)}</div>`;
}

// 页面调试将前端服务状态、维护操作和页面预览放在同一面板；页面产物未完成时只提供空目标下拉。
export function pageDebugPanel() {
  if (!state.completed.includes(page)) return debugEmptyPanel('page');
  const address=state.frontendServiceRunning?safe(state.pageDebugUrl || 'http://localhost:5173'):'about:blank';
  return `<div class="page-debugger"><div class="debug-header-stack"><div class="api-request-row debug-address-row"><span class="debug-preview-nav">‹　›　↻</span><div class="debug-address-field-group">${debugArtifactPicker('page')}<input id="page-debug-url" aria-label="预览地址" value="${address}"></div><button data-action="preview-visit">访问</button>${debugServiceToolbar('frontend')}</div></div><div class="page-debug-content">${pagePreviewContent(false)}</div>${state.debugLogOpen?serviceLogPanel('frontend'):''}</div>`;
}
