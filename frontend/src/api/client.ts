import Taro from '@tarojs/taro'
import { makeKey } from '../features/flow'
import { createRequestClient, readResponse, RequestError, type RequestOptions, type Session } from './request'
import type { AiSummary, AnswerReceipt, Attempt, BodyFor, GenerationJob, LearningRecord, QuizGroup, RecordPage, TopicList } from './types'

const SESSION_KEY = __QINGZHI_DEV_AUTH__ ? 'qingzhi.session.dev.v1' : 'qingzhi.session.wechat.v1'
const DEVICE_KEY = 'qingzhi.device.v1'

function readSaved<T>(key: string): T | undefined {
  try {
    return Taro.getStorageSync<T>(key) || undefined
  } catch {
    return undefined
  }
}

function save<T>(key: string, value?: T) {
  try {
    if (value === undefined) Taro.removeStorageSync(key)
    else Taro.setStorageSync(key, value)
  } catch {
    // The current session remains usable when local storage isn't available.
  }
}

async function transport({ path, method = 'GET', data, idempotencyKey }: RequestOptions, token?: string) {
  if (!__QINGZHI_API_BASE_URL__) {
    throw new RequestError('CONFIGURATION', '学习服务暂未配置，请稍后再试。', 0)
  }
  const header: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) header.Authorization = `Bearer ${token}`
  if (idempotencyKey) header['Idempotency-Key'] = idempotencyKey
  try {
    return await Taro.request<unknown>({ url: __QINGZHI_API_BASE_URL__ + path, method, data, header, timeout: 15000 })
  } catch {
    throw new RequestError('NETWORK', '连接中断了，检查网络后再试。', 0, true)
  }
}

let businessSession = readSaved<Session>(SESSION_KEY)

const request = createRequestClient({
  transport,
  readSession: () => businessSession,
  saveSession(session) {
    businessSession = session
    save(SESSION_KEY, session)
  },
  async login() {
    if (__QINGZHI_DEV_AUTH__) {
      const deviceId = readSaved<string>(DEVICE_KEY) || makeKey()
      save(DEVICE_KEY, deviceId)
      return readResponse<Session>(await transport({ path: '/v1/dev/auth', method: 'POST', data: { deviceId } satisfies BodyFor<'/v1/dev/auth', 'post'> }))
    }

    let code: string
    try {
      const loginResult = await Taro.login()
      code = loginResult.code
    } catch {
      throw new RequestError('WECHAT_LOGIN', '微信登录暂未完成，请重试。', 401, true)
    }
    if (!code) throw new RequestError('WECHAT_LOGIN', '微信登录暂未完成，请重试。', 401, true)
    return readResponse<Session>(await transport({ path: '/v1/auth/wechat', method: 'POST', data: { code } satisfies BodyFor<'/v1/auth/wechat', 'post'> }))
  },
})

export const api = {
  topics: () => request<TopicList>({ path: '/v1/topics' }),
  generate: (topic: string, idempotencyKey: string) => request<GenerationJob>({
    path: '/v1/generation-jobs', method: 'POST', data: { topic } satisfies BodyFor<'/v1/generation-jobs', 'post'>, idempotencyKey,
  }),
  job: (id: string) => request<GenerationJob>({ path: `/v1/generation-jobs/${encodeURIComponent(id)}` }),
  fixedQuiz: __QINGZHI_DEV_FIXED_QUIZ__ ? (idempotencyKey: string) => request<QuizGroup>({
    path: '/v1/dev/quizzes/fixed', method: 'POST', idempotencyKey,
  }) : undefined,
  startAttempt: (quizId: string, idempotencyKey: string) => request<Attempt>({
    path: '/v1/attempts', method: 'POST', data: { quizId } satisfies BodyFor<'/v1/attempts', 'post'>, idempotencyKey,
  }),
  attempt: (id: string) => request<Attempt>({ path: `/v1/attempts/${encodeURIComponent(id)}` }),
  answer: (attemptId: string, questionId: string, selectedOptionIds: string[]) => request<AnswerReceipt>({
    path: `/v1/attempts/${encodeURIComponent(attemptId)}/answers/${encodeURIComponent(questionId)}`,
    method: 'PUT', data: { selectedOptionIds } satisfies BodyFor<'/v1/attempts/{attempt_id}/answers/{question_id}', 'put'>,
  }),
  records: (cursor?: string) => request<RecordPage>({
    path: '/v1/records?limit=20' + (cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''),
  }),
  record: (id: string) => request<LearningRecord>({ path: `/v1/records/${encodeURIComponent(id)}` }),
  retrySummary: (id: string, idempotencyKey: string) => request<AiSummary>({
    path: `/v1/records/${encodeURIComponent(id)}/ai-summary/retry`, method: 'POST', idempotencyKey,
  }),
}

export type RestartGroup = QuizGroup & { topicTitle: string }
const RESTART_KEY = __QINGZHI_DEV_AUTH__ ? 'qingzhi.restart.dev.v1' : 'qingzhi.restart.wechat.v1'

export function readRestartGroup(): RestartGroup | undefined {
  const group = readSaved<RestartGroup>(RESTART_KEY)
  if (group && typeof group.quizId === 'string' && typeof group.topicTitle === 'string'
      && typeof group.quizExpiresAt === 'string') return group
}

export function saveRestartGroup(group?: RestartGroup) {
  const restart = group && { quizId: group.quizId, topicTitle: group.topicTitle, quizExpiresAt: group.quizExpiresAt }
  save(RESTART_KEY, restart)
}
