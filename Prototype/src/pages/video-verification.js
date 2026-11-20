import { state, safe } from '../state.js?v=20261122a';
import { action, agentHeader, message, workflowCard } from '../components/video-common.js?v=20261122a';

// 测试阶段展示构建检查、可选性能测试以及测试报告。
export function testConversation() {
  const checks = ['前端依赖安装检查','前端构建检查','后端构建检查','后端启动检查'];
  const list = `<div class="simple-card"><strong>测试检查步骤</strong>${checks.map((c,i)=>`<div class="list-row"><span>${String(i+1).padStart(2,'0')}　${c}</span><span>已通过</span></div>`).join('')}${state.testStep? `<div class="list-row"><span>05　前端性能测试</span><span>${state.performanceSkipped?'已跳过':'已通过'}</span></div>` : ''}</div>`;
  if (!state.testStep) return agentHeader(3) + list + workflowCard('<p>构建检查已完成。前端性能测试不是必需步骤，可能耗时较长，是否执行？</p>',action('执行性能测试','test-perform') + action('跳过性能测试','test-skip','class="primary"'));
  return message(state.performanceSkipped?'跳过前端性能测试':'执行前端性能测试') + agentHeader(3) + list + workflowCard('<strong>测试已完成</strong><p>测试报告已生成，可在右侧查看。</p>',action('查看测试报告','show-test-report') + action('进入审查阶段','go-review','class="primary"'),'已完成');
}

export function testReport() {
  return `<div class="document-content"><div class="artifact-heading"><div><strong>测试报告</strong><small>.devagentstudio/reports/test-report.md</small></div></div><h2>前后端构建与后端启动检查</h2><table class="video-table"><thead><tr><th>检查</th><th>状态</th><th>结果摘要</th></tr></thead><tbody><tr><td>前端构建检查</td><td>通过</td><td>构建完成</td></tr><tr><td>后端构建检查</td><td>通过</td><td>构建完成</td></tr><tr><td>后端启动检查</td><td>通过</td><td>服务已就绪</td></tr><tr><td>前端性能测试</td><td>${state.performanceSkipped?'已跳过':'通过'}</td><td>按本轮选择执行</td></tr></tbody></table></div>`;
}

// 审查完成后明确交接到验收阶段。
export function reviewConversation() {
  if (!state.reviewStep) return agentHeader(4) + workflowCard('<p>对本轮前后端代码进行全量审查，检查代码规范、安全与健康度。</p>',action('开始全量审查','run-review','class="primary"'));
  if (state.acceptancePreflightOriginPhase===4 && state.acceptancePreflightStatus==='checking') {
    return agentHeader(4) + workflowCard(`<strong>验收前服务检查</strong><p>正在读取前后端实时状态；仅启动当前未运行的服务。</p>${preflightRows()}<div class="list-row"><span>等待服务就绪</span><span>检查中</span></div>`,'','进行中');
  }
  if (state.acceptancePreflightOriginPhase===4 && state.acceptancePreflightStatus==='failed') {
    const failedKind=state.acceptancePreflightServices.backend==='failed'?'backend':'frontend';
    const openService=failedKind==='backend'?'service-open-backend':'service-open-frontend';
    const failedLabel=failedKind==='backend'?'后端':'前端';
    return agentHeader(4) + workflowCard(`<strong>服务未就绪，暂不能进入验收</strong><p>${safe(state.acceptancePreflightError)}</p>${preflightRows()}<p class="hint">处理后回到此处重新检查；已运行的另一端会保留。</p>`,action(`查看${failedLabel}服务日志`,openService) + action('重新检查并启动','go-acceptance','class="primary"'),'未就绪');
  }
  return agentHeader(4) + workflowCard('<strong>代码审查已完成</strong><p>✓　扫描工作区代码</p><p>✓　前后端代码审查</p><p>✓　生成审查报告</p><p>进入验收前会重新检查前后端服务；未运行的服务将自动启动，就绪后再进入验收。</p>',action('查看审查报告','show-review-report') + action('进入验收阶段','go-acceptance','class="primary"'),'已完成');
}

// 按最近一次预检状态展示前后端服务启动进度。
function preflightRows() {
  const statusLabel={idle:'未检查',checking:'检查中',starting:'启动中',ready:'已就绪',failed:'启动失败',stopped:'已停止'};
  const statusFor=kind=>{
    const running=kind==='backend'?state.backendServiceRunning:state.frontendServiceRunning;
    const current=state.acceptancePreflightServices?.[kind] || 'idle';
    return current==='ready'&&!running?'stopped':current;
  };
  return `<div class="list-row"><span>后端服务</span><span>${statusLabel[statusFor('backend')]||'未检查'}</span></div><div class="list-row"><span>前端服务</span><span>${statusLabel[statusFor('frontend')]||'未检查'}</span></div>`;
}

export function reviewReport() {
  return '<div class="document-content"><div class="artifact-heading"><div><strong>审查报告</strong><small>.devagentstudio/reports/code-review.md</small></div></div><h2>代码审查报告</h2><table class="video-table"><thead><tr><th>审查项</th><th>结果</th><th>说明</th></tr></thead><tbody><tr><td>代码规范</td><td>通过</td><td>命名与结构检查</td></tr><tr><td>安全检查</td><td>通过</td><td>参数与访问检查</td></tr><tr><td>健康度</td><td>通过</td><td>构建及代码质量检查</td></tr></tbody></table></div>';
}

// 验收阶段展示真实的服务就绪状态，未启动时提供对应的服务管理入口。
export function acceptanceConversation() {
  const servicesReady = state.frontendServiceRunning && state.backendServiceRunning;
  const preflightRunning=state.acceptancePreflightOriginPhase===5 && state.acceptancePreflightStatus==='checking';
  const preflightFailed=state.acceptancePreflightOriginPhase===5 && state.acceptancePreflightStatus==='failed';
  const body = state.accepted
    ? `<strong>验收已通过</strong><p>${state.released?'本次代码和业务变更说明已提交。':'可以提交本次代码和业务变更说明。'}</p>`
    : preflightRunning
      ? `<strong>正在检查并启动预览服务</strong><p>重新读取当前运行状态；已运行的服务会保留，未运行的服务将自动启动。</p>${preflightRows()}<div class="list-row"><span>健康检查就绪</span><span>等待服务</span></div>`
      : preflightFailed
        ? `<strong>预览服务未就绪</strong><p>${safe(state.acceptancePreflightError)}</p>${preflightRows()}<p class="hint">处理失败服务后，可在此重新检查，不需要离开验收页面再找启动入口。</p>`
        : `<strong>${servicesReady?'项目启动完成':'预览服务待检查'}</strong><p>${servicesReady?'前端与后端已就绪，请打开预览并根据需求文档验收当前应用。':'进入验收前会检查服务状态，并自动启动未运行的服务。'}</p>${preflightRows()}<div class="list-row"><span>健康检查就绪</span><span>${servicesReady?'已完成':'待检查'}</span></div>`;
  const replay = state.isDemo && state.accepted ? action('重新演示验收','replay-acceptance') : '';
  const failedKind=state.acceptancePreflightServices.backend==='failed'?'backend':'frontend';
  const failureAction=failedKind==='backend'?action('查看后端服务日志','service-open-backend'):action('查看前端服务日志','service-open-frontend');
  const serviceActions=(preflightFailed?failureAction:'') + action('重新检查并启动','go-acceptance','class="primary"');
  const actions = state.accepted ? replay : preflightRunning ? '' : servicesReady ? action('打开预览','open-preview','class="primary"') : serviceActions;
  return agentHeader(5) + workflowCard(body,actions,state.accepted?'已完成':preflightRunning?'检查中':preflightFailed?'服务未就绪':servicesReady?'待确认':'待检查');
}

// 服务未就绪时锁定验收通过，并提示用户先启动前后端服务。
export function acceptanceBar() {
  const servicesReady = state.frontendServiceRunning && state.backendServiceRunning;
  const preflightRunning=state.acceptancePreflightOriginPhase===5 && state.acceptancePreflightStatus==='checking';
  const preflightFailed=state.acceptancePreflightOriginPhase===5 && state.acceptancePreflightStatus==='failed';
  const reminder = state.acceptanceReminderDismissed ? '' : `<div class="accept-reminder"><span>代码已通过自动检查，可先创建版本</span><div class="actions">${action('稍后','checkpoint-later')}${action('审阅并提交','checkpoint-review')}</div></div>`;
  const readinessHint = servicesReady?'请根据需求文档基线验收当前应用。':preflightRunning?'正在检查前后端状态，并自动启动未运行的服务。':preflightFailed?'服务未就绪，请查看对应服务日志并重试。':'前后端服务未就绪时，可在此重新检查并自动启动。';
  const serviceRows=servicesReady?'':preflightRows();
  const failedKind=state.acceptancePreflightServices.backend==='failed'?'backend':'frontend';
  const failureAction=failedKind==='backend'?action('查看后端服务日志','service-open-backend'):action('查看前端服务日志','service-open-frontend');
  const retryActions=preflightRunning?'':`${preflightFailed?failureAction:''}${!servicesReady?action('重新检查并启动','go-acceptance','class="primary"'):''}`;
  const acceptAttributes = servicesReady?'class="primary"':'class="primary" disabled title="请先启动前后端服务"';
  return `<div class="accept-footer">${reminder}<div class="accept-bar"><div><strong>版本验收</strong><p class="hint">${readinessHint}</p>${preflightFailed?`<p>${safe(state.acceptancePreflightError)}</p>`:''}${serviceRows}</div><div class="actions">${action('不通过，进入对话','accept-reject') + retryActions + action('验收通过','accept',acceptAttributes)}</div></div></div>`;
}
