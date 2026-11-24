export const WINDOW_CONTROLS_ARGUMENT = '--devagentstudio-window-controls'

export const WINDOW_CONTROLS_CHANNELS = {
  getMaximized: 'window-controls:get-maximized',
  maximizedChanged: 'window-controls:maximized-changed',
  perform: 'window-controls:perform'
} as const

export type WindowControlAction = 'minimize' | 'toggle-maximize' | 'close'

export type WindowControlsApi = {
  getMaximized: () => Promise<boolean>
  onMaximizedChanged: (listener: (maximized: boolean) => void) => () => void
  perform: (action: WindowControlAction) => Promise<void>
}
