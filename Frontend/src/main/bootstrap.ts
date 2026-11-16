import { app } from 'electron'
import {
  initializeDiagnostics,
  recordDiagnosticError,
  setDiagnosticExitStatus
} from './diagnostics/runtime'

initializeDiagnostics()

// 分离业务模块加载，尽量在模块求值失败前完成本地诊断初始化。
void import('./index').catch((error: unknown) => {
  recordDiagnosticError('main_module_load_failed', error)
  setDiagnosticExitStatus('startup_failed')
  app.exit(1)
})
