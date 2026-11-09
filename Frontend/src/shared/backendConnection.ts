export type BackendConnectionState = {
  revision: number
  status: 'ready' | 'reconnecting' | 'unavailable' | 'stopped'
  baseUrl: string
  backendInstanceId?: string
}

export type BackendConnectionApi = {
  getState: () => Promise<BackendConnectionState>
  onStateChanged: (listener: (state: BackendConnectionState) => void) => () => void
}
