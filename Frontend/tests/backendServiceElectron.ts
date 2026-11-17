/** 仅替换 Electron 宿主身份，确保测试真实走内置后台分支且不能读取旧实例登记。 */
export const app = { isPackaged: true }

/** 生命周期测试无需启动 Electron 本地崩溃收集。 */
export const crashReporter = {
  /** 替代 Electron 的 Crashpad 启动调用。 */
  start(): void {}
}
