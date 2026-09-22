/** Electron 主进程使用的统一品牌与工作区持久化名称。 */
export const PRODUCT_DISPLAY_NAME = 'DevAgent Studio'
export const PRODUCT_ID = 'devagentstudio'
export const WORKSPACE_ARTIFACT_DIR_NAME = '.devagentstudio'
export const USER_DATA_DIRECTORY_NAMES = {
  dev: '.devagentstudio_dev',
  st: '.devagentstudio_st',
  uat: '.devagentstudio_uat',
  prd: '.devagentstudio'
} as const
