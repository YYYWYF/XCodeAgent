export type AppUpdatePhase =
  | 'checking'
  | 'downloading'
  | 'downloaded'
  | 'installing'
  | 'error'
  | 'continuing'
  | 'quitting'

export type AppUpdateState = {
  revision: number
  phase: AppUpdatePhase
  currentVersion: string
  latestAppVersion?: string
  isPackaged: boolean
  percent: number
  transferred: number
  total: number
  message: string
}

export type AppUpdateApi = {
  getState: () => Promise<AppUpdateState>
  onStateChanged: (listener: (state: AppUpdateState) => void) => () => void
  install: () => Promise<void>
  continueDevelopment: () => Promise<void>
  quit: () => Promise<void>
}
