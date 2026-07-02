/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useMemo } from 'react'
import type { ReactNode } from 'react'

type RendererApi = Window['api']

type ApiProviderProps = {
  children: ReactNode
  api?: RendererApi
}

export const ApiContext = createContext<RendererApi | null>(null)

const getPreloadApi = (): RendererApi => {
  if (!window.api) {
    throw new Error('window.api is not available. Please check the preload API setup.')
  }

  return window.api
}

export function ApiProvider({ children, api }: ApiProviderProps): React.JSX.Element {
  const apiValue = useMemo(() => api ?? getPreloadApi(), [api])

  return <ApiContext.Provider value={apiValue}>{children}</ApiContext.Provider>
}

export const useApi = (): RendererApi => {
  const api = useContext(ApiContext)

  if (!api) {
    throw new Error('useApi must be used within ApiProvider.')
  }

  return api
}
