import { state, projectMemory, buildApiDebugUrl } from './state.js?v=20261122a';
import { completedDemo, aiMappingHints } from './data/demo-case.js?v=20261123l';
import { toast } from './components/feedback.js?v=20261118o';
import { home } from './pages/home.js?v=20261123d';
import { create } from './pages/create-application.js?v=20261123d';
import { openWorkspace } from './pages/open-workspace.js?v=20261123d';
import { workbench } from './pages/workbench.js?v=20261123l';
import { currentRoute } from './router.js?v=20261118o';
import { updateAnnotations } from './components/annotations.js?v=20261118o';

const app = document.querySelector('#app');
// 根据地址显示欢迎页、项目弹窗或工作台。
function render() {
  const route = currentRoute();
  document.body.classList.toggle('workbench-page', route === 'workbench' || route === 'planning');
  document.body.classList.toggle('welcome-active', ['home','create','open'].includes(route));
  app.innerHTML = route==='create'?create():route==='open'?openWorkspace():route==='workbench'||route==='planning'?workbench():home();
  const diagnoseButton=app.querySelector('[data-action="service-diagnose"]');
  if (diagnoseButton) {
    const selectedService=state.tab==='接口调试'?'service-backend':'service-frontend';
    const failureMatches=state.serviceFailure.startsWith(selectedService==='service-frontend'?'前端':'后端');
    diagnoseButton.disabled=state.serviceRepairStep!=='plan'||!failureMatches;
    diagnoseButton.title=diagnoseButton.disabled?'仅当前服务启动失败时可用':'';
  }
  updateAnnotations(route);
}

// 切换面板宽度后将固定画布归回左边缘，避免横向滚动让分隔线控件看起来漂移。
function resetCanvasHorizontalScroll() {
  const canvas=document.querySelector('.prototype-canvas');
  if (canvas) canvas.scrollLeft=0;
}

// 进入阶段时设置该阶段默认展示的产物。
function enterPhase(phase) {
  if (state.acceptancePreflightStatus==='checking' && phase!==state.phase) {
    state.acceptancePreflightRun++;
    state.acceptancePreflightStatus='idle';
  }
  state.phase = phase;
  state.reached = Math.max(state.reached, phase);
  state.pane = ''; state.auxDetail = ''; state.modal = ''; state.previewStatus = false;
  state.section = phase===1?'架构':'概览';
  state.tab = phase===0?(state.designStep?'UI设计稿':'需求文档'):phase===1?'技术规划':phase===3&&state.testStep?'测试报告':phase===4&&state.reviewStep?'审查报告':phase===5?'应用预览':'开发产物';
  if (phase===5) state.panelLayout='full';
  else state.panelLayout='split';
  render();
  resetCanvasHorizontalScroll();
}

// 页面和接口共用任务计划，接口依赖的映射需要先确认。
function beginBuild(name, kind) {
  if (state.buildStep && !['complete'].includes(state.buildStep) && state.target !== name) {
    toast('请先完成当前开发任务。'); return;
  }
  state.target = name; state.targetKind = kind;
  state.selectedArtifact = kind==='api'?'api':'page'; state.pane = ''; state.tab = '开发产物';
  state.buildStep = state.mappingConfirmed ? 'task-ready' : 'mapping';
  if (state.buildStep==='task-ready') state.tab = '待确认任务';
  render();
}

// 把某一层服务更新为就绪，并记录本次启动日志。
function markServiceReady(kind) {
  const layerName=kind==='backend'?'后端':'前端';
  if (kind==='backend') state.backendServiceRunning=true;
  else state.frontendServiceRunning=true;
  state.previewRunning=state.frontendServiceRunning&&state.backendServiceRunning;
  if (state.serviceStarting) state.serviceStarting[kind]=false;
  state.serviceLogs[kind]=kind==='backend'
    ? ['[验收前检查 · stdout] 后端构建完成','[启动 · stdout] API 服务已就绪','[就绪检测 · info] 后端服务健康检查通过']
    : ['[验收前检查 · stdout] 前端依赖检查完成','[启动 · stdout] Vite 服务已就绪','[就绪检测 · info] 前端预览地址可访问'];
  state.serviceRepairStep='';
  if (state.serviceFailure.startsWith(layerName)) state.serviceFailure='';
}

// 调试或预览首次进入时自动拉起对应服务；存在待修复故障时交给用户手动处理。
function autoStartService(kind) {
  const running=kind==='frontend'?state.frontendServiceRunning:state.backendServiceRunning;
  const label=kind==='frontend'?'前端':'后端';
  if (running || state.serviceStarting[kind]) return;
  if (state.serviceRepairStep==='plan' && state.serviceFailure.startsWith(label)) return;
  state.serviceStarting[kind]=true;
  render();
  setTimeout(()=>{ state.serviceStarting[kind]=false; markServiceReady(kind); render(); },700);
}

// 调试面板随时可进入；对应产物完成后进入时自动拉起所需服务，未完成时由面板提示先开发。
function openDebug(tab) {
  const isPage = tab==='页面调试';
  const artifact = isPage?'门户首页':'查询新闻列表';
  state.tab=tab; state.pane=''; state.previewStatus=false; state.debugServiceMenuOpen=false;
  if (state.phase===5&&!state.accepted) state.panelLayout='split';
  if (state.completed.includes(artifact)) {
    autoStartService(isPage?'frontend':'backend');
    // 页面调试需要接口数据，接口产物已完成时顺带拉起后端服务。
    if (isPage && state.completed.includes('查询新闻列表')) autoStartService('backend');
  }
  return true;
}

// 每次进入验收前读取当前服务状态，按后端、前端顺序补启动未运行的服务。
function beginAcceptancePreflight() {
  if (state.acceptancePreflightStatus==='checking') return;
  const originPhase=state.phase;
  const runId=++state.acceptancePreflightRun;
  const isCurrentRun=()=>runId===state.acceptancePreflightRun;
  state.acceptancePreflightOriginPhase=originPhase;
  state.acceptancePreflightStatus='checking';
  state.acceptancePreflightError='';
  state.acceptancePreflightServices={
    backend:state.backendServiceRunning?'ready':'checking',
    frontend:state.frontendServiceRunning?'ready':'checking'
  };
  render();

  const fail=(kind)=>{
    if (!isCurrentRun()) return;
    const label=kind==='backend'?'后端':'前端';
    state.acceptancePreflightServices[kind]='failed';
    state.acceptancePreflightStatus='failed';
    state.acceptancePreflightError=state.serviceFailure.startsWith(label)
      ? state.serviceFailure
      : `${label}服务未能启动，请查看服务日志后重试。`;
    render();
  };
  const startLayer=(kind,next)=>{
    if (!isCurrentRun()) return;
    const running=kind==='backend'?state.backendServiceRunning:state.frontendServiceRunning;
    if (running) {
      state.acceptancePreflightServices[kind]='ready';
      render();
      setTimeout(()=>{ if (isCurrentRun()) next(); },250);
      return;
    }
    state.acceptancePreflightServices[kind]='starting';
    render();
    setTimeout(()=>{
      if (!isCurrentRun()) return;
      const label=kind==='backend'?'后端':'前端';
      if (state.serviceFailure.startsWith(label)) { fail(kind); return; }
      markServiceReady(kind);
      state.acceptancePreflightServices[kind]='ready';
      render();
      setTimeout(()=>{ if (isCurrentRun()) next(); },250);
    },450);
  };
  const finish=()=>{
    if (!isCurrentRun()) return;
    state.acceptancePreflightServices={backend:'ready',frontend:'ready'};
    state.acceptancePreflightStatus='ready';
    state.previewRunning=true;
    toast('前后端服务已就绪，可以开始验收。');
    if (originPhase===4) enterPhase(5);
    else render();
  };

  // 先展示状态读取，再依赖真实的当前运行布尔值决定是否补启动。
  setTimeout(()=>{
    if (!isCurrentRun()) return;
    state.acceptancePreflightServices={
      backend:state.backendServiceRunning?'ready':'checking',
      frontend:state.frontendServiceRunning?'ready':'checking'
    };
    render();
    startLayer('backend',()=>startLayer('frontend',finish));
  },300);
}

// 复制当前服务的全部启动日志。
async function copyServiceLog(kind) {
  const panel=document.querySelector(`[data-service-log-panel="${kind}"]`);
  const failureText=state.serviceRepairStep==='plan'&&state.serviceFailure.startsWith(kind==='frontend'?'前端':'后端')?state.serviceFailure:'';
  const content=[...(state.serviceLogs[kind]||[]),failureText].filter(Boolean).join('\n')||panel?.innerText||'';
  if (!content.trim()) { toast('当前没有可复制的日志。'); return; }
  try { await navigator.clipboard.writeText(content); toast('日志已复制。'); }
  catch { toast('复制失败，请重试。'); }
}

// 处理视频主旅程中的确认、切换和模拟结果。
function handleAction(action) {
  switch (action) {
    case 'choose-folder': toast('请在项目目录中填写演示路径。'); return;
    case 'requirement-edit': state.revisionEditing='requirement'; break;
    case 'plan-edit': state.revisionEditing='plan'; break;
    case 'revision-cancel': state.revisionEditing=''; break;
    case 'requirement-revise':
    case 'plan-revise': {
      const value = document.querySelector('#revision-text').value.trim();
      if (!value) { toast('请填写修改意见。'); return; }
      if (action==='requirement-revise') state.requirementFeedback=value; else state.planFeedback=value;
      state.revisionEditing=''; break;
    }
    case 'requirement-confirm': state.designStep=1; state.tab='UI设计稿'; break;
    case 'ui-view': state.tab='UI设计稿'; break;
    case 'ui-template': state.modal='ui-template'; break;
    case 'ui-template-use': state.modal=''; state.uiGenerated=true; state.uiSkipped=false; state.uiVariant++; state.tab='UI设计稿'; break;
    case 'ui-generate': state.uiGenerated=true; state.uiSkipped=false; state.uiVariant++; state.tab='UI设计稿'; break;
    case 'ui-adjust': { const value=document.querySelector('#ui-feedback')?.value.trim(); if (!value) { toast('请填写设计调整意见。'); return; } state.uiFeedback=value; state.uiGenerated=true; state.uiSkipped=false; state.uiVariant++; state.tab='UI设计稿'; break; }
    case 'ui-skip': state.uiSkipped=true; state.uiGenerated=false; state.tab='UI设计稿'; break;
    case 'design-next': if (state.uiGenerated || state.uiSkipped) enterPhase(1); return;
    case 'plan-confirm': enterPhase(2); return;
    case 'start-api': beginBuild('查询新闻列表','api'); return;
    case 'start-page': beginBuild('门户首页','page'); return;
    case 'open-mapping': state.tab='字段映射'; break;
    case 'mapping-source': state.mappingSourceSelected=false; state.mappingConfirmed=false; state.mappingStep=''; state.fieldMappings={}; state.mappingHints={}; break;
    case 'source-external': toast('请先添加外部 API。'); return;
    case 'source-static': toast('当前示例使用数据库作为数据来源。'); return;
    case 'source-confirm': state.modal=''; break;
    case 'open-data': state.modal=''; state.pane='data'; break;
    case 'mapping-generate': {
      // AI 生成映射是辅助手段：一键把建议映射落入配置表单，可再次点击重新生成。
      if (!(state.databaseTables[state.database]||[]).includes('portal_news') || !state.mappingSourceSelected) { toast('请先选择 portal_news 数据表。'); return; }
      if (state.mappingGenerating) return;
      state.mappingGenerating=true; render();
      setTimeout(()=>{
        state.mappingGenerating=false;
        // 接口映射说明由 AI 生成映射一并给出，重新生成时同步更新。
        state.mappingDescription='按维度查询新闻列表，返回标题、摘要、发布日期、来源与内容。';
        state.fieldMappings={total:'业务处理',pageSize:'pageSize',current:'current','list[].title':'title','list[].summary':'summary','list[].dimension':'dimension','list[].publishDate':'publish_date','list[].source':'source','list[].content':'content'};
        state.mappingHints=aiMappingHints;
        state.mappingStep='draft'; state.mappingConfirmed=false;
        render(); toast('AI 映射已生成，请检查字段旁的 AI 提示后保存。');
      },600);
      return;
    }
    case 'mapping-draft': toast('映射草稿已暂存。'); return;
    case 'mapping-save':
      if (!state.mappingSourceSelected || !(state.databaseTables[state.database]||[]).includes('portal_news') || Object.keys(state.fieldMappings).length<10 || Object.values(state.fieldMappings).some(value=>!value)) { toast('请先选择数据表并完成字段映射。'); return; }
      state.mappingConfirmed=true; state.mappingStep='confirmed'; toast('映射已保存，等待统一检测。'); break;
    case 'mapping-check': if (!state.mappingConfirmed) { toast('请先保存并确认映射。'); return; } state.buildStep='task-ready'; state.tab='待确认任务'; break;
    case 'view-task-plan': state.tab='待确认任务'; break;
    case 'task-regenerate': toast('任务计划已重新生成，请审阅。'); return;
    case 'task-discard': state.buildStep=''; state.target=''; state.tab='开发产物'; break;
    case 'task-confirm': state.buildStep='running'; state.tab='开发产物'; break;
    case 'build-result':
      if (!state.completed.includes(state.target)) state.completed.push(state.target);
      state.buildStep='complete'; state.tab='开发产物'; break;
    case 'review-changes': state.tab='应用文件'; break;
    case 'open-preview': if (state.phase===5) { state.tab='应用预览'; state.previewStatus=false; } else openDebug('页面调试'); break;
    case 'open-debug-preview': state.selectedArtifact='page'; openDebug('页面调试'); break;
    case 'open-api-debug': state.selectedArtifact='api'; openDebug('接口调试'); break;
    case 'close-preview': state.tab='开发产物'; state.panelLayout='split'; state.previewStatus=false; break;
    case 'preview-visit': if (!state.completed.includes('门户首页')) { toast('完成「门户首页」开发后即可访问页面。'); return; } if (!state.frontendServiceRunning) { autoStartService('frontend'); toast('前端服务正在启动，请稍候。'); } else if (!state.backendServiceRunning) { toast('页面已打开，后端服务未启动，数据暂不可用。'); } else { toast('已访问当前应用预览。'); } return;
    case 'toggle-debug-service-menu': state.debugServiceMenuOpen=!state.debugServiceMenuOpen; break;
    case 'toggle-debug-log': state.debugLogOpen=!state.debugLogOpen; break;
    case 'close-debug-log': state.debugLogOpen=false; break;
    case 'service-start-frontend':
    case 'service-restart-frontend': markServiceReady('frontend'); state.serviceLogs.frontend=['[依赖检查 · stdout] 前端依赖检查完成','[维护 · stdout] Vite 服务启动完成','[就绪检测 · info] http://localhost:5173 已就绪']; toast(action==='service-start-frontend'?'前端服务已启动。':'前端服务已重启。'); break;
    case 'service-start-backend':
    case 'service-restart-backend': markServiceReady('backend'); state.serviceLogs.backend=['[依赖检查 · stdout] 后端依赖检查完成','[维护 · stdout] 应用服务启动完成','[就绪检测 · info] API 服务已就绪']; toast(action==='service-start-backend'?'后端服务已启动。':'后端服务已重启。'); break;
    case 'service-stop-frontend': state.frontendServiceRunning=false; state.previewRunning=false; state.serviceLogs.frontend=[...state.serviceLogs.frontend,'[维护 · info] 前端服务已停止']; toast('前端服务已停止。'); break;
    case 'service-stop-backend': state.backendServiceRunning=false; state.previewRunning=state.frontendServiceRunning&&state.backendServiceRunning; state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError=''; state.serviceLogs.backend=[...state.serviceLogs.backend,'[维护 · info] 后端服务已停止']; toast('后端服务已停止。'); break;
    case 'service-diagnose': {
      const selectedService=state.tab==='接口调试'?'service-backend':'service-frontend';
      const isFrontend=selectedService==='service-frontend';
      state.serviceRepairStep='plan';
      state.serviceFailure=isFrontend?'前端服务启动失败：开发服务未就绪。':'后端服务启动失败：检测到端口占用。';
      state.serviceLogs[isFrontend?'frontend':'backend']=[...(state.serviceLogs[isFrontend?'frontend':'backend']||[]),state.serviceFailure];
      break;
    }
    case 'service-repair-confirm': {
      const isFrontend=state.serviceFailure.startsWith('前端');
      if (isFrontend) { state.frontendServiceRunning=true; state.previewRunning=state.frontendServiceRunning&&state.backendServiceRunning; state.serviceLogs.frontend=['[修复 · stdout] 前端服务启动完成','[就绪检测 · info] http://localhost:5173 已就绪']; }
      else { state.backendServiceRunning=true; state.previewRunning=state.frontendServiceRunning&&state.backendServiceRunning; state.serviceLogs.backend=['[修复 · stdout] 后端服务启动完成','[就绪检测 · info] API 服务已就绪']; }
      state.serviceRepairStep=''; state.serviceFailure=''; toast('当前服务已修复并重新启动。'); break;
    }
    case 'service-copy-all-frontend': void copyServiceLog('frontend'); return;
    case 'service-copy-all-backend': void copyServiceLog('backend'); return;
    case 'service-follow-frontend': state.serviceLogFollowing.frontend=!state.serviceLogFollowing.frontend; break;
    case 'service-follow-backend': state.serviceLogFollowing.backend=!state.serviceLogFollowing.backend; break;
    case 'api-call': {
      if (!state.completed.includes('查询新闻列表')) { toast('完成「查询新闻列表」开发后即可发送调试请求。'); return; }
      if (state.serviceStarting.backend) { toast('后端服务正在启动，请稍候再发送。'); return; }
      state.apiCalled=true;
      state.apiLastRequestUrl=buildApiDebugUrl();
      if (!state.backendServiceRunning) { state.apiCallStatus='connection-error'; state.apiCallError='无法连接后端服务（ERR_CONNECTION_REFUSED）。'; }
      else if (state.apiDebugMethod!=='GET') { state.apiCallStatus='method-error'; state.apiCallError='405 Method Not Allowed：当前 API 契约只支持 GET。'; }
      else { state.apiCallStatus='success'; state.apiCallError=''; }
      break;
    }
    case 'service-open-frontend': openDebug('页面调试'); state.debugServiceMenuOpen=true; state.debugLogOpen=true; break;
    case 'service-open-backend': openDebug('接口调试'); state.debugServiceMenuOpen=true; state.debugLogOpen=true; break;
    case 'panel-full': state.panelLayout=state.panelLayout==='full'?'split':'full'; break;
    case 'go-test': if (state.completed.length<2) { toast('请先完成页面和接口开发。'); return; } state.testStep=0; enterPhase(3); return;
    case 'test-perform': state.testStep=1; state.performanceSkipped=false; state.tab='测试报告'; break;
    case 'test-skip': state.testStep=1; state.performanceSkipped=true; state.tab='测试报告'; break;
    case 'show-test-report': state.tab='测试报告'; break;
    case 'go-review': if (!state.testStep) { toast('请先完成测试。'); return; } enterPhase(4); return;
    case 'run-review': state.reviewStep=1; state.tab='审查报告'; break;
    case 'show-review-report': state.tab='审查报告'; break;
    case 'go-acceptance': if (!state.reviewStep) { toast('请先完成审查。'); return; } beginAcceptancePreflight(); return;
    case 'replay-acceptance': state.accepted=false; state.released=false; state.tab='应用预览'; state.panelLayout='full'; state.acceptanceReminderDismissed=false; break;
    case 'checkpoint-later': state.acceptanceReminderDismissed=true; break;
    case 'checkpoint-review': state.modal='checkpoint'; break;
    case 'checkpoint-confirm': if (!document.querySelector('#checkpoint-note')?.value.trim()) { toast('请填写变更说明。'); return; } state.acceptanceReminderDismissed=true; state.modal=''; toast('已完成版本保存演示。'); break;
    case 'accept':
      if (!state.frontendServiceRunning || !state.backendServiceRunning) { beginAcceptancePreflight(); return; }
      state.accepted=true; state.panelLayout='split'; state.tab='应用预览';
      // 验收通过后自动弹出生成版本弹框，衔接版本提交动作。
      state.releaseNote='门户首页已接入新闻资讯数据，按行情聚焦、战略推动、一线扫描、文化故事四个区块分类展示新闻列表，每条新闻直接呈现序号、标题、概要、维度、发布日期、来源与内容。'; state.modal='release'; break;
    case 'accept-reject': state.modal='accept-reject'; break;
    case 'accept-feedback': {
      const value=document.querySelector('#accept-feedback')?.value.trim();
      if (!value) { toast('请填写验收意见。'); return; }
      state.acceptanceFeedback=value; state.lastMessage=`验收反馈：${value}`; state.isDemo=false; state.accepted=false; state.buildStep='complete'; enterPhase(2); return;
    }
    case 'release': if (state.accepted && !state.released) { state.releaseNote='门户首页已接入新闻资讯数据，按行情聚焦、战略推动、一线扫描、文化故事四个区块分类展示新闻列表，每条新闻直接呈现序号、标题、概要、维度、发布日期、来源与内容。'; state.modal='release'; } break;
    case 'release-confirm': {
      const value=document.querySelector('#release-note')?.value.trim();
      if (!value) { toast('请填写变更说明。'); return; }
      const productivity={method:document.querySelector('#productivity-method')?.value||'',previousEffort:document.querySelector('#previous-effort')?.value||'',studioEffort:document.querySelector('#studio-effort')?.value||'',unit:'人天'};
      state.productivityRecord=productivity.method||productivity.previousEffort||productivity.studioEffort?productivity:null;
      state.releaseNote=value; state.released=true; state.modal=''; toast('版本已生成（演示）。'); break;
    }
    case 'version-menu': state.versionMenu=!state.versionMenu; break;
    case 'confirm-phase': enterPhase(state.pendingPhase); return;
    case 'close-modal': state.modal=''; break;
    default: return;
  }
  render();
  if (['close-preview','panel-full','replay-acceptance','accept'].includes(action)) resetCanvasHorizontalScroll();
}

// 处理工作台内的导航、产物选择与公共抽屉。
document.addEventListener('click', event => {
  const target=event.target instanceof Element?event.target:null;
  const el=target?.closest('button');
  const clickedAuxiliary=Boolean(target?.closest('.aux-drawer,.aux-detail,.aux-overlay'));
  const dismissedAuxiliary=Boolean(state.pane&&!clickedAuxiliary);
  // 保留侧栏入口的互斥切换与再次点击关闭行为。
  if (el?.dataset.pane) { state.pane=state.pane===el.dataset.pane?'':el.dataset.pane; state.auxDetail=''; state.auxModal=''; state.versionMenu=false; render(); return; }
  if (dismissedAuxiliary) { state.pane=''; state.auxDetail=''; state.auxModal=''; }
  if (!el) { if (dismissedAuxiliary && !target?.closest('a[href]')) render(); return; }
  if (el.id==='help') { toast('帮助入口'); if (dismissedAuxiliary) render(); return; }
  if (el.dataset.openDemo) {
    if (state.hasProject && !state.isDemo) projectMemory.current=structuredClone(state);
    Object.assign(state, completedDemo());
    location.hash='workbench'; return;
  }
  if (el.dataset.openCurrent) {
    if (state.isDemo && projectMemory.current) Object.assign(state, structuredClone(projectMemory.current));
    location.hash='workbench'; return;
  }
  if (el.dataset.go) { location.hash=el.dataset.go; return; }
  if (el.dataset.panelLayout) { state.panelLayout=el.dataset.panelLayout; state.previewStatus=false; render(); resetCanvasHorizontalScroll(); return; }
  if (el.dataset.phase!==undefined) {
    const phase=Number(el.dataset.phase);
    if (phase===5 && state.reviewStep && (phase!==state.phase || !state.frontendServiceRunning || !state.backendServiceRunning)) { beginAcceptancePreflight(); return; }
    if (state.isDemo && phase!==state.phase && phase<=state.reached) enterPhase(phase);
    else if (phase<state.phase) { state.pendingPhase=phase; state.modal='switch-phase'; render(); }
    else if (phase<=state.reached && phase!==state.phase) enterPhase(phase);
    else if (dismissedAuxiliary) render();
    return;
  }
  if (el.dataset.database) { state.database=el.dataset.database; state.auxDetail=''; render(); return; }
  if (el.dataset.domain) { state.domain=el.dataset.domain; state.auxDetail=''; render(); return; }
  if (el.dataset.table) { state.auxDetail=`table:${el.dataset.table}`; render(); return; }
  if (el.dataset.api) { state.auxDetail=`api:${el.dataset.api}`; render(); return; }
  if (el.dataset.apiMethod) {
    state.apiDebugMethod=el.dataset.apiMethod;
    state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError='';
    render(); return;
  }
  if (el.dataset.selectDebugArtifact) {
    state.selectedArtifact=el.dataset.selectDebugArtifact;
    if (el.dataset.selectDebugArtifact==='page') state.pageDebugUrl=el.dataset.debugUrl;
    else { state.apiDebugUrl=el.dataset.debugUrl; state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError=''; }
    render(); return;
  }
  if (el.dataset.session!==undefined) { state.sessionSelected=Number(el.dataset.session); state.pane=''; render(); return; }
  if (el.dataset.aux) { handleAuxAction(el.dataset.aux); return; }
  if (el.dataset.tab) {
    // 调试标签随时可进入，可选目标由面板内地址栏下拉控制；其余标签直接切换。
    if (el.dataset.tab==='页面调试'||el.dataset.tab==='接口调试') openDebug(el.dataset.tab);
    else state.tab=el.dataset.tab;
    state.pane=''; state.previewStatus=false;
    if (state.phase===5&&!state.accepted) state.panelLayout=state.tab==='应用预览'?'full':'split';
    render(); return;
  }
  if (el.dataset.apiRequestTab) { state.apiRequestTab=el.dataset.apiRequestTab; render(); return; }
  if (el.dataset.apiResponseTab) { state.apiResponseTab=el.dataset.apiResponseTab; render(); return; }
  if (el.dataset.section) { state.section=el.dataset.section; render(); return; }
  if (el.dataset.build) { beginBuild(el.dataset.build,el.dataset.kind); return; }
  if (el.dataset.file) { state.selectedFilePath=el.dataset.file; render(); return; }
  if (el.dataset.artifact) { state.selectedArtifact=el.dataset.artifact; render(); return; }
  if (el.dataset.action) { handleAction(el.dataset.action); if (dismissedAuxiliary && !state.pane) render(); }
});

// 公共抽屉使用独立的演示状态，不参与主旅程的异步执行选择。
function handleAuxAction(action) {
  if (action==='close') { state.pane=''; state.auxDetail=''; state.auxModal=''; }
  else if (action==='detail-close') state.auxDetail='';
  else if (action==='modal-close') state.auxModal='';
  else if (['database-new','database-settings','domain-new','domain-settings','tables-add'].includes(action)) state.auxModal=action;
  else if (action==='connection-test') { if (!document.querySelector('#aux-form').reportValidity()) return; toast('示例连接检查通过。'); return; }
  else if (action==='api-new') state.auxDetail='api-new';
  else if (action==='session-regular') state.sessionTab='常规对话';
  else if (action==='session-temporary') state.sessionTab='临时对话';
  else if (action==='new-session') { state.sessions.push('新对话'); state.sessionSelected=state.sessions.length-1; state.sessionTab='常规对话'; }
  else if (action==='queue-active') state.queueTab='进行中';
  else if (action==='queue-done') state.queueTab='已完成';
  else if (action==='skills-user') state.skillTab='用户';
  else if (action==='skills-built-in') state.skillTab='内置';
  else if (action==='save-file') { state.agentFile=document.querySelector('#agent-file').value; toast('文件已保存。'); }
  else if (action==='environment-add') { document.querySelector('#environment-fields').insertAdjacentHTML('beforeend','<div class="grid"><input placeholder="变量名"><input placeholder="变量值"></div>'); return; }
  render();
}

// 表单提交只更新当前原型中的示例数据。
document.addEventListener('submit', event => {
  event.preventDefault();
    if (event.target.id==='create-form') {
    const values=new FormData(event.target);
    Object.assign(state,{name:document.querySelector('#name').value.trim(),description:document.querySelector('#description').value.trim(),projectPath:document.querySelector('#project-path').value.trim(),terminal:values.get('terminal'),layout:values.get('layout'),appIcon:values.get('app-icon'),useHeader:values.has('header'),useMenu:values.has('menu'),authEnabled:values.has('auth'),hasProject:true,isDemo:false,phase:0,reached:0,designStep:0,uiGenerated:false,uiSkipped:false,uiVariant:0,uiFeedback:'',requirementFeedback:'',planFeedback:'',revisionEditing:'',target:'',targetKind:'',selectedArtifact:'page',selectedFilePath:'docs/requirement-spec.md',buildStep:'',completed:[],mappingStep:'',mappingConfirmed:false,mappingSourceSelected:false,mappingDescription:'',fieldMappings:{},mappingHints:{},mappingGenerating:false,testStep:0,reviewStep:0,accepted:false,released:false,productivityMethod:'',previousEffort:'',studioEffort:'',productivityRecord:null,previewRunning:false,frontendServiceRunning:false,backendServiceRunning:false,serviceStarting:{frontend:false,backend:false},pageDebugUrl:'http://localhost:5173',debugServiceMenuOpen:false,debugLogOpen:false,serviceLogs:{frontend:[],backend:[]},serviceLogFollowing:{frontend:true,backend:true},serviceRepairStep:'',serviceFailure:'',acceptancePreflightStatus:'idle',acceptancePreflightOriginPhase:null,acceptancePreflightServices:{frontend:'idle',backend:'idle'},acceptancePreflightError:'',acceptancePreflightRun:0,apiCalled:false,apiDebugUrl:'http://localhost:8000/api/news',apiDebugParams:{dimension:'行情聚焦',current:'1',pageSize:'10'},panelLayout:'split',previewStatus:false,tab:'需求文档',section:'概览',pane:'',modal:''});
    state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError=''; state.apiLastRequestUrl=''; state.apiDebugMethod='GET'; state.apiDebugUrl='http://localhost:8000/api/news'; state.apiDebugParams={dimension:'行情聚焦',current:'1',pageSize:'10'}; state.apiDebugEnabledParams={dimension:true,current:true,pageSize:true}; state.apiDebugHeaders={Accept:'application/json'}; state.apiDebugHeaderEnabled={Accept:true}; state.apiDebugAuth='No Auth'; state.apiDebugBody=''; state.apiRequestTab='Params'; state.apiResponseTab='Body';
    state.databases=[]; state.database=''; state.databaseTables={}; state.databaseConfigs={};
    state.domains=['门户平台']; state.domain='门户平台'; state.domainApis={'门户平台':[]}; projectMemory.current=null;
    state.sessions=['设计']; state.sessionSelected=0; state.sessionTab='常规对话'; state.lastMessage=''; state.acceptanceReminderDismissed=false;
    location.hash='workbench';
  } else if (event.target.id==='chat-form') {
    const input=document.querySelector('#message');
    const value=input.value.trim(); if (!value) return;
    if (state.phase===0 && state.designStep===0) state.requirementFeedback=value;
    else if (state.phase===0 && state.designStep===1) { state.uiFeedback=value; state.uiGenerated=true; state.uiSkipped=false; state.uiVariant++; state.tab='UI设计稿'; }
    else if (state.phase===1) state.planFeedback=value;
    else state.lastMessage=value;
    render();
  } else if (event.target.id==='settings-form') {
    const values=new FormData(event.target);
    state.name=document.querySelector('#settings-name').value.trim();
    state.description=document.querySelector('#settings-desc').value.trim();
    state.layout=values.get('layout'); state.useHeader=values.has('header'); state.useFooter=values.has('footer');
    state.authEnabled=values.has('auth'); state.pageTracking=values.has('pageTracking'); state.apiTracking=values.has('apiTracking');
    render(); toast('设置已保存。');
  } else if (event.target.id==='api-form') {
    const fields=new FormData(event.target);
    state.domainApis[state.domain].push({name:String(fields.get('name')).trim(),method:fields.get('method'),url:String(fields.get('url')).trim(),description:String(fields.get('description')||'').trim(),request:String(fields.get('request')||'').trim(),response:String(fields.get('response')||'').trim()});
    state.auxDetail=''; render(); toast('接口已添加。');
  } else if (event.target.id==='aux-form') {
    const fields=new FormData(event.target); const name=String(fields.get('name')||'').trim();
    if (state.auxModal==='database-new' && name) {
      if (Object.hasOwn(state.databaseTables,name)) { toast('数据库名称已存在。'); return; }
      state.databases.push(name); state.databaseTables[name]=[]; state.database=name;
    }
    if (state.auxModal==='database-settings' && name && name!==state.database) { if (Object.hasOwn(state.databaseTables,name)) { toast('数据库名称已存在。'); return; } state.databaseTables[name]=state.databaseTables[state.database]; delete state.databaseTables[state.database]; delete state.databaseConfigs[state.database]; state.databases[state.databases.indexOf(state.database)]=name; state.database=name; }
    if (['database-new','database-settings'].includes(state.auxModal)) state.databaseConfigs[state.database]={mode:fields.get('mode'),dbid:fields.get('dbid'),host:fields.get('host'),port:fields.get('port'),schema:fields.get('schema'),user:fields.get('user')};
    if (state.auxModal==='domain-new' && name) { if (!state.domainApis[name]) { state.domains.push(name); state.domainApis[name]=[]; } state.domain=name; }
    if (state.auxModal==='domain-settings' && name && name!==state.domain) { if (state.domainApis[name]) { toast('域名称已存在。'); return; } state.domainApis[name]=state.domainApis[state.domain]; delete state.domainApis[state.domain]; state.domains[state.domains.indexOf(state.domain)]=name; state.domain=name; }
    if (state.auxModal==='tables-add') fields.getAll('tables').forEach(value=>{ if (!state.databaseTables[state.database].includes(value)) state.databaseTables[state.database].push(value); });
    state.auxModal=''; render(); toast('已保存。');
  }
});

// 根据数据库连接方式展示对应字段。
document.addEventListener('change', event => {
  if (event.target.id==='api-debug-auth') { state.apiDebugAuth=event.target.value; state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError=''; render(); return; }
  if (event.target.dataset.apiHeaderEnabled) { state.apiDebugHeaderEnabled[event.target.dataset.apiHeaderEnabled]=event.target.checked; state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError=''; render(); return; }
  if (event.target.dataset.apiParamEnabled) { state.apiDebugEnabledParams[event.target.dataset.apiParamEnabled]=event.target.checked; state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError=''; render(); return; }
  if (event.target.name==='mapping-source-table') { state.mappingSourceSelected=event.target.value==='portal_news'; state.mappingConfirmed=false; state.mappingStep=''; state.fieldMappings={}; state.mappingHints={}; render(); return; }
  if (event.target.name==='mapping-field') { const position=document.querySelector('.mapping-content')?.scrollTop||0; state.fieldMappings[event.target.dataset.field]=event.target.value; render(); const panel=document.querySelector('.mapping-content'); if (panel) panel.scrollTop=position; return; }
  if (event.target.name!=='mode' || !event.target.closest('#aux-form')) return;
  document.querySelectorAll('[data-connection]').forEach(section=>{ const inactive=section.dataset.connection!==event.target.value; section.hidden=inactive; section.querySelectorAll('input').forEach(input=>{ input.disabled=inactive; }); });
});
// 保留映射说明，以便切换字段或标签后继续审阅。
document.addEventListener('input', event => {
  if (event.target.getAttribute('aria-label')==='接口映射说明') state.mappingDescription=event.target.value;
  if (event.target.id==='page-debug-url') state.pageDebugUrl=event.target.value;
  if (event.target.id==='api-debug-url') {
    const value=event.target.value;
    try {
      const parsed=new URL(value);
      state.apiDebugUrl=`${parsed.origin}${parsed.pathname}`;
      if (parsed.search) Object.keys(state.apiDebugParams).forEach(key=>{
        state.apiDebugEnabledParams[key]=parsed.searchParams.has(key);
        if (parsed.searchParams.has(key)) state.apiDebugParams[key]=parsed.searchParams.get(key);
      });
    } catch { state.apiDebugUrl=value; }
    state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError='';
  }
  if (event.target.dataset.apiParam) {
    state.apiDebugParams[event.target.dataset.apiParam]=event.target.value;
    state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError='';
  }
  if (event.target.dataset.apiHeader) { state.apiDebugHeaders[event.target.dataset.apiHeader]=event.target.value; state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError=''; }
  if (event.target.id==='api-debug-body') { state.apiDebugBody=event.target.value; state.apiCalled=false; state.apiCallStatus='idle'; state.apiCallError=''; }
  if (event.target.id==='release-note') {
    state.releaseNote=event.target.value;
    const counter=document.querySelector('#release-note-count');
    if (counter) counter.textContent=String([...event.target.value].length);
  }
  if (event.target.id==='previous-effort') state.previousEffort=event.target.value;
  if (event.target.id==='studio-effort') state.studioEffort=event.target.value;
});
document.addEventListener('change',event=>{
  if (event.target.id==='productivity-method') state.productivityMethod=event.target.value;
});
// AI 提示气泡：用固定定位的现代 tooltip 展示异常字段的分析与建议，替代原生 title 的迟滞体验。
let aiTipTimer=null;
function ensureAiTip() { let tip=document.querySelector('.ai-tip'); if (!tip) { tip=document.createElement('div'); tip.className='ai-tip'; tip.setAttribute('role','tooltip'); document.body.appendChild(tip); } return tip; }
document.addEventListener('mouseover', event => {
  const hint=event.target.closest?.('.mapping-ai-hint');
  if (!hint || !hint.dataset.tip) return;
  clearTimeout(aiTipTimer);
  aiTipTimer=setTimeout(()=>{
    const tip=ensureAiTip();
    tip.textContent=hint.dataset.tip;
    tip.classList.remove('show');
    const rect=hint.getBoundingClientRect();
    const size=tip.getBoundingClientRect();
    let top=rect.top-size.height-8;
    // 图标贴近顶部时翻转到下方，横向夹在视口内。
    if (top<8) top=rect.bottom+8;
    tip.style.top=`${Math.round(top)}px`;
    tip.style.left=`${Math.round(Math.max(8,Math.min(rect.left+rect.width/2-size.width/2,window.innerWidth-size.width-8)))}px`;
    tip.classList.add('show');
  },120);
});
document.addEventListener('mouseout', event => {
  const hint=event.target.closest?.('.mapping-ai-hint');
  if (!hint || hint.contains(event.relatedTarget)) return;
  clearTimeout(aiTipTimer);
  document.querySelector('.ai-tip')?.classList.remove('show');
});
document.addEventListener('scroll', ()=>{ clearTimeout(aiTipTimer); document.querySelector('.ai-tip')?.classList.remove('show'); }, true);
window.addEventListener('hashchange',()=>{ const canvas=document.querySelector('.prototype-canvas'); canvas.scrollLeft=0; canvas.scrollTop=0; render(); });
window.addEventListener('resize',()=>updateAnnotations(currentRoute()));
document.addEventListener('scroll',()=>updateAnnotations(currentRoute()),true);
render();
