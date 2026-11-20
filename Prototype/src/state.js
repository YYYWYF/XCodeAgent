// 原型仅在当前浏览器页面内保存演示进度。
export const state = {
  name: '苏州分行门户', description: '面向苏州分行员工与访客的 PC 端门户，按行情聚焦、战略推动、一线扫描、文化故事四个区块展示新闻资讯。', phase: 0, reached: 0,
  projectPath: 'C:\\Workspace\\suzhou-portal', hasProject: false, isDemo: false,
  terminal: 'PC', layout: '左侧导航', appIcon: '应用', useHeader: true, useMenu: true, authEnabled: false, pageTracking: false, apiTracking: false,
  designStep: 0, uiGenerated: false, uiVariant: 0, uiSkipped: false,
  requirementFeedback: '', planFeedback: '', revisionEditing: '',
  tab: '', section: '概览', target: '', targetKind: '', buildStep: '', completed: [],
  selectedArtifact: 'page', selectedFilePath: 'docs/requirement-spec.md', mappingStep: '', mappingConfirmed: false, mappingSourceSelected: false, mappingDescription: '', fieldMappings: {},
  testStep: 0, performanceSkipped: false, reviewStep: 0, accepted: false, released: false,
  productivityMethod: '', previousEffort: '', studioEffort: '', productivityRecord: null,
  previewRunning: false, frontendServiceRunning: false, backendServiceRunning: false, serviceStarting: { frontend:false, backend:false }, pageDebugUrl: 'http://localhost:5173', debugServiceMenuOpen: false, debugLogOpen: false, serviceLogs: {frontend:[],backend:[]}, serviceLogFollowing: {frontend:true,backend:true}, serviceRepairStep: '', serviceFailure: '', apiCalled: false, apiCallStatus: 'idle', apiCallError: '', apiLastRequestUrl: '', apiDebugMethod: 'GET', apiDebugUrl: 'http://localhost:8000/api/news', apiDebugParams: {dimension:'行情聚焦',current:'1',pageSize:'10'}, apiDebugEnabledParams: {dimension:true,current:true,pageSize:true}, apiDebugHeaders: {Accept:'application/json'}, apiDebugHeaderEnabled: {Accept:true}, apiDebugAuth: 'No Auth', apiDebugBody: '', apiRequestTab: 'Params', apiResponseTab: 'Body', panelLayout: 'split', previewStatus: false, acceptanceReminderDismissed: false,
  acceptancePreflightStatus: 'idle', acceptancePreflightOriginPhase: null, acceptancePreflightServices: {frontend:'idle',backend:'idle'}, acceptancePreflightError: '', acceptancePreflightRun: 0, modal: '', pane: '',
  auxModal: '', auxDetail: '', queueTab: '进行中', sessionTab: '常规对话',
  sessions: ['设计'], sessionSelected: 0, databases: [],
  database: '', databaseTables: {}, databaseConfigs: {},
  domains: ['门户平台'], domain: '门户平台',
  domainApis: {'门户平台': []},
  agentFile: '# AGENTS.md\n\n描述 Agent 的角色与工作方式。', skillTab: '用户'
};

// 根据 API 契约参数生成与 Postman 类似的同步请求 URL。
export function buildApiDebugUrl() {
  try {
    const url = new URL(state.apiDebugUrl)
    Object.entries(state.apiDebugParams).forEach(([key, value]) => {
      if (state.apiDebugEnabledParams[key] && String(value).trim()) url.searchParams.set(key, String(value))
      else url.searchParams.delete(key)
    })
    return url.toString()
  } catch {
    return state.apiDebugUrl
  }
}
// 临时保存本次从头创建的项目，切换演示案例时不丢失进度。
export const projectMemory = { current: null };
// 转义用户输入。
export function safe(value) { const el = document.createElement('div'); el.textContent = value || ''; return el.innerHTML; }
