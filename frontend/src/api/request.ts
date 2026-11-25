import type { Session } from './types'
export type { Session } from './types'

export type RequestOptions = {
  path: string
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH'
  data?: object
  idempotencyKey?: string
}
type Response = { statusCode: number; data: unknown }
type RequestClientOptions = {
  transport: (options: RequestOptions, token?: string) => Promise<Response>
  readSession: () => Session | undefined
  saveSession: (session: Session | undefined) => void
  login: () => Promise<Session>
}

export class RequestError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly retryable = false,
    readonly requestId?: string,
  ) {
    super(message)
    this.name = 'RequestError'
  }
}

export function readResponse<T>(response: Response): T {
  const { statusCode, data } = response
  if (statusCode >= 200 && statusCode < 300) return data as T
  const details = data && typeof data === 'object' ? data : {}
  throw new RequestError(
    'code' in details && typeof details.code === 'string' ? details.code : 'REQUEST_FAILED',
    'message' in details && typeof details.message === 'string' ? details.message : '暂时无法连接，请稍后再试。',
    statusCode,
    'retryable' in details && details.retryable === true,
    'requestId' in details && typeof details.requestId === 'string' ? details.requestId : undefined,
  )
}

export function createRequestClient({ transport, readSession, saveSession, login }: RequestClientOptions) {
  let signingIn: Promise<Session> | undefined

  function sessionToken(): Promise<Session> {
    const current = readSession()
    if (current && Date.parse(current.expiresAt) > Date.now()) return Promise.resolve(current)
    signingIn ??= login().then((session) => {
      saveSession(session)
      return session
    }).finally(() => { signingIn = undefined })
    return signingIn
  }

  return async function request<T>(options: RequestOptions): Promise<T> {
    const current = await sessionToken()
    const response = await transport(options, current.accessToken)
    if (response.statusCode !== 401) return readResponse<T>(response)

    if (readSession()?.accessToken === current.accessToken) saveSession(undefined)
    const renewed = await sessionToken()
    return readResponse<T>(await transport(options, renewed.accessToken))
  }
}

export function messageForError(error: unknown): string {
  if (!(error instanceof RequestError)) return '暂时无法连接，请稍后再试。'
  if (error.status === 401) return '登录暂时未完成，请重试。'
  return error.message
}

export function attemptNeedsRegeneration(error: unknown): boolean {
  return error instanceof RequestError && (
    error.status === 404 || error.status === 410 ||
    error.code === 'QUIZ_EXPIRED' || error.code === 'ATTEMPT_CLOSED'
  )
}
