import { USER_DATA_DIRECTORY_NAMES } from './branding'

export type AppEnv = 'dev' | 'st' | 'uat' | 'prd'

export type DevAgentStudioEnvConfig = {
  DEVAGENTSTUDIO_BASE_URL: string
  DEVAGENTSTUDIO_BACKEND_URL: string
  WORKING_DIR: string
}

const DEFAULT_AGENT_URL = 'http://127.0.0.1:8000'

const supportedAppEnvs: AppEnv[] = ['dev', 'st', 'uat', 'prd']

const isAppEnv = (value: string): value is AppEnv => supportedAppEnvs.includes(value as AppEnv)

const resolveAppEnv = (value: string | undefined): AppEnv => {
  if (!value) {
    return 'dev'
  }

  if (isAppEnv(value)) {
    return value
  }

  throw new Error(`Unsupported APP_ENV: ${value}`)
}

export const APP_ENV_CONFIG: Record<AppEnv, DevAgentStudioEnvConfig> = {
  dev: {
    DEVAGENTSTUDIO_BASE_URL: DEFAULT_AGENT_URL,
    DEVAGENTSTUDIO_BACKEND_URL: DEFAULT_AGENT_URL,
    WORKING_DIR: USER_DATA_DIRECTORY_NAMES.dev
  },
  st: {
    DEVAGENTSTUDIO_BASE_URL: DEFAULT_AGENT_URL,
    DEVAGENTSTUDIO_BACKEND_URL: DEFAULT_AGENT_URL,
    WORKING_DIR: USER_DATA_DIRECTORY_NAMES.st
  },
  uat: {
    DEVAGENTSTUDIO_BASE_URL: DEFAULT_AGENT_URL,
    DEVAGENTSTUDIO_BACKEND_URL: DEFAULT_AGENT_URL,
    WORKING_DIR: USER_DATA_DIRECTORY_NAMES.uat
  },
  prd: {
    DEVAGENTSTUDIO_BASE_URL: DEFAULT_AGENT_URL,
    DEVAGENTSTUDIO_BACKEND_URL: DEFAULT_AGENT_URL,
    WORKING_DIR: USER_DATA_DIRECTORY_NAMES.prd
  }
}

export const APP_ENV = resolveAppEnv(process.env.APP_ENV)

const selectedConfig = APP_ENV_CONFIG[APP_ENV]

export const DEVAGENTSTUDIO_ENV: DevAgentStudioEnvConfig = {
  ...selectedConfig,
  DEVAGENTSTUDIO_BASE_URL:
    process.env.DEVAGENTSTUDIO_BASE_URL || selectedConfig.DEVAGENTSTUDIO_BASE_URL,
  DEVAGENTSTUDIO_BACKEND_URL:
    process.env.DEVAGENTSTUDIO_BACKEND_URL || selectedConfig.DEVAGENTSTUDIO_BACKEND_URL
}
