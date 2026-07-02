export type AppEnv = 'dev' | 'st' | 'uat'

export type AppConfig = {
  env: AppEnv
  apiBaseUrl: string
  appName: string
  logLevel: 'debug' | 'info' | 'warn' | 'error'
}

export type PublicAppConfig = Pick<AppConfig, 'env' | 'apiBaseUrl' | 'appName'>

const appConfigs: Record<AppEnv, AppConfig> = {
  dev: {
    env: 'dev',
    apiBaseUrl: 'https://dev-api.example.com',
    appName: 'XcodeAgent Dev',
    logLevel: 'debug'
  },
  st: {
    env: 'st',
    apiBaseUrl: 'https://st-api.example.com',
    appName: 'XcodeAgent ST',
    logLevel: 'info'
  },
  uat: {
    env: 'uat',
    apiBaseUrl: 'https://uat-api.example.com',
    appName: 'XcodeAgent UAT',
    logLevel: 'info'
  }
}

const resolveAppEnv = (value: string | undefined): AppEnv => {
  if (value === 'dev' || value === 'st' || value === 'uat') {
    return value
  }

  throw new Error(`Unsupported APP_ENV: ${value}`)
}

export const appConfig = appConfigs[resolveAppEnv(process.env.APP_ENV)]

export const publicAppConfig: PublicAppConfig = {
  env: appConfig.env,
  apiBaseUrl: appConfig.apiBaseUrl,
  appName: appConfig.appName
}
