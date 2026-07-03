export type AppEnv = 'dev' | 'st' | 'uat' | 'prd'

export type XcodeAgentEnvConfig = {
  XCODE_AGENT_BASE_URL: string
  XCODE_AGENT_BACKEND_URL: string
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

export const APP_ENV_CONFIG: Record<AppEnv, XcodeAgentEnvConfig> = {
  dev: {
    XCODE_AGENT_BASE_URL: DEFAULT_AGENT_URL,
    XCODE_AGENT_BACKEND_URL: DEFAULT_AGENT_URL,
    WORKING_DIR: '.xcodeagent_dev'
  },
  st: {
    XCODE_AGENT_BASE_URL: DEFAULT_AGENT_URL,
    XCODE_AGENT_BACKEND_URL: DEFAULT_AGENT_URL,
    WORKING_DIR: '.xcodeagent_st'
  },
  uat: {
    XCODE_AGENT_BASE_URL: DEFAULT_AGENT_URL,
    XCODE_AGENT_BACKEND_URL: DEFAULT_AGENT_URL,
    WORKING_DIR: '.xcodeagent_uat'
  },
  prd: {
    XCODE_AGENT_BASE_URL: DEFAULT_AGENT_URL,
    XCODE_AGENT_BACKEND_URL: DEFAULT_AGENT_URL,
    WORKING_DIR: '.xcodeagent'
  }
}

export const APP_ENV = resolveAppEnv(process.env.APP_ENV)

const selectedConfig = APP_ENV_CONFIG[APP_ENV]

export const XCODE_AGENT_ENV: XcodeAgentEnvConfig = {
  ...selectedConfig,
  XCODE_AGENT_BASE_URL: process.env.XCODE_AGENT_BASE_URL || selectedConfig.XCODE_AGENT_BASE_URL,
  XCODE_AGENT_BACKEND_URL:
    process.env.XCODE_AGENT_BACKEND_URL || selectedConfig.XCODE_AGENT_BACKEND_URL
}
