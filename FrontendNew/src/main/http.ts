import axios, { AxiosError, AxiosHeaders } from 'axios'
import type { AxiosInstance } from 'axios'
import { getStoredToken } from './auth'
import { appConfig } from './env'
import type { ApiErrorPayload } from '../shared/api'

const DEFAULT_TIMEOUT_MS = 30000

type ErrorLikePayload = {
  code?: unknown
  message?: unknown
  requestId?: unknown
}

export class ApiRequestError extends Error {
  readonly payload: ApiErrorPayload

  constructor(payload: ApiErrorPayload) {
    super(payload.message)
    this.name = 'ApiRequestError'
    this.payload = payload
  }
}

const isErrorLikePayload = (value: unknown): value is ErrorLikePayload =>
  typeof value === 'object' && value !== null

const getHeaderValue = (headers: unknown, key: string): string | undefined => {
  if (headers instanceof AxiosHeaders) {
    const value = headers.get(key)
    return typeof value === 'string' ? value : undefined
  }

  if (!isErrorLikePayload(headers)) {
    return undefined
  }

  const value = headers[key as keyof ErrorLikePayload]
  return typeof value === 'string' ? value : undefined
}

const getErrorCode = (status: number | undefined, data: unknown, fallbackCode?: string): string => {
  if (isErrorLikePayload(data) && typeof data.code === 'string') {
    return data.code
  }

  if (status) {
    return `HTTP_${status}`
  }

  return fallbackCode ?? 'NETWORK_ERROR'
}

const getErrorMessage = (data: unknown, fallbackMessage: string): string => {
  if (isErrorLikePayload(data) && typeof data.message === 'string') {
    return data.message
  }

  return fallbackMessage
}

const getRequestId = (headers: unknown, data: unknown): string | undefined => {
  const headerRequestId = getHeaderValue(headers, 'x-request-id')

  if (headerRequestId) {
    return headerRequestId
  }

  if (isErrorLikePayload(data) && typeof data.requestId === 'string') {
    return data.requestId
  }

  return undefined
}

export const backendHttp: AxiosInstance = axios.create({
  baseURL: appConfig.apiBaseUrl,
  timeout: DEFAULT_TIMEOUT_MS
})

backendHttp.interceptors.request.use(async (config) => {
  const token = await getStoredToken()

  if (token) {
    config.headers = AxiosHeaders.from(config.headers)
    config.headers.set('Authorization', `Bearer ${token}`)
  }

  return config
})

export const createApiError = (code: string, message: string, status?: number): ApiRequestError =>
  new ApiRequestError({
    code,
    message,
    status
  })

export const toApiErrorPayload = (error: unknown): ApiErrorPayload => {
  if (error instanceof ApiRequestError) {
    return error.payload
  }

  if (error instanceof AxiosError) {
    const status = error.response?.status
    const data = error.response?.data

    return {
      code: getErrorCode(status, data, error.code),
      message: getErrorMessage(data, error.message || 'Request failed'),
      status,
      requestId: getRequestId(error.response?.headers, data)
    }
  }

  if (error instanceof Error) {
    return {
      code: 'UNKNOWN_ERROR',
      message: error.message
    }
  }

  return {
    code: 'UNKNOWN_ERROR',
    message: 'Unknown error'
  }
}
