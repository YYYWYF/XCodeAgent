// 门户演示使用虚构地址，不连接真实外部服务。
export const exampleApi = {
  name: '查询门户新闻', description: '按新闻分类查询门户资讯。', method: 'GET',
  url: 'https://example.com/api/portal-news', request: 'dimension：新闻分类',
  response: 'title：标题；summary：摘要；content：内容；publishDate：发布日期'
};

// AI 辅助映射的异常提示：只在需人工处理的字段上出现，hover 展示分析结果与建议。
// warn = 未找到映射、留待人工填写；info = AI 已填但置信度不高，建议核对。
export const aiMappingHints = {
  total: { level: 'info', text: 'AI 分析：total 与业务处理记录总数的对应基于语义推断，置信度中等。建议：核对业务处理的统计口径后再确认。' },
  'list[].sequenceNumber': { level: 'warn', text: 'AI 分析：接口字段 list[].sequenceNumber（序号）在 portal_news 表中未找到可映射的字段。建议：选择 id 充当序号，或与业务方确认序号来源后手动指定，请人工确认。' }
};

// 已完成版本的各阶段产物与状态保持一致。
export function completedDemo() {
  return {
    isDemo: true, hasProject: true, name: '苏州分行门户',
    description: '按行情聚焦、战略推动、一线扫描、文化故事四个区块展示新闻资讯。',
    projectPath: 'C:\\Workspace\\suzhou-portal', terminal: 'PC', layout: '左侧导航',
    appIcon: '应用', useHeader: true, useMenu: true, authEnabled: false,
    phase: 5, reached: 5, designStep: 1, uiGenerated: true, uiVariant: 1, uiSkipped: false,
    requirementFeedback: '不需要点击某条新闻查看完整内容', planFeedback: '维度字段改为文本类型',
    revisionEditing: '', uiFeedback: '', tab: '应用预览', section: '概览',
    target: '门户首页', targetKind: 'page', selectedArtifact: 'page', selectedFilePath: 'docs/requirement-spec.md', buildStep: 'complete',
    completed: ['查询新闻列表', '门户首页'], mappingConfirmed: true, mappingSourceSelected: true,
    mappingStep: 'confirmed', mappingDescription: '按维度查询新闻列表，返回标题、摘要、发布日期、来源与内容。',
    fieldMappings: {total:'业务处理',pageSize:'pageSize',current:'current','list[].sequenceNumber':'id',
      'list[].title':'title','list[].summary':'summary','list[].dimension':'dimension',
      'list[].publishDate':'publish_date','list[].source':'source','list[].content':'content'},
    mappingHints: aiMappingHints,
    databases: ['新闻数据库'], database: '新闻数据库', databaseTables: {'新闻数据库':['portal_news']},
    databaseConfigs: {'新闻数据库':{mode:'本地直连',host:'127.0.0.1',port:'3306',schema:'portal',user:'demo'}},
    domains: ['门户平台'], domain: '门户平台', domainApis: {'门户平台':[]},
    testStep: 1, performanceSkipped: true, reviewStep: 1, accepted: true, released: false,
    productivityMethod: '', previousEffort: '', studioEffort: '', productivityRecord: null,
    previewRunning: true, frontendServiceRunning: true, backendServiceRunning: true, serviceStarting: { frontend:false, backend:false }, pageDebugUrl: 'http://localhost:5173', debugServiceMenuOpen: false, debugLogOpen: false, serviceLogs: {frontend:['[安装 · stdout] frontend-install.stdout.log\n依赖安装完成','[启动 · stdout] frontend.stdout.log\nVite 服务已就绪'],backend:['[构建 · stdout] backend-build.stdout.log\n后端构建完成','[启动 · stdout] backend.stdout.log\nAPI 服务已就绪']}, serviceLogFollowing: {frontend:true,backend:true}, serviceRepairStep: '', serviceFailure: '', apiCalled: false, apiCallStatus: 'idle', apiCallError: '', apiLastRequestUrl: '', apiDebugMethod: 'GET', apiDebugUrl: 'http://localhost:8000/api/news', apiDebugParams: {dimension:'行情聚焦',current:'1',pageSize:'10'}, apiDebugEnabledParams: {dimension:true,current:true,pageSize:true}, apiDebugHeaders: {Accept:'application/json'}, apiDebugHeaderEnabled: {Accept:true}, apiDebugAuth: 'No Auth', apiDebugBody: '', apiRequestTab: 'Params', apiResponseTab: 'Body', panelLayout: 'split', previewStatus: false, acceptanceReminderDismissed: false,
    acceptancePreflightStatus: 'ready', acceptancePreflightOriginPhase: 5, acceptancePreflightServices: {frontend:'ready',backend:'ready'}, acceptancePreflightError: '', acceptancePreflightRun: 0,
    pane: '', auxModal: '', auxDetail: '', modal: '', lastMessage: '',
    sessions: ['演示对话'], sessionSelected: 0, sessionTab: '常规对话'
  };
}
