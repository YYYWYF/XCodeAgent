/** 仅替换 Electron 宿主身份，确保测试真实走内置后台分支且不能读取旧实例登记。 */
export const app = { isPackaged: true }
