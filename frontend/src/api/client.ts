import Taro from '@tarojs/taro'
import { makeKey, prunePendingGenerations } from '../features/flow'
import { createRequestClient, readResponse, RequestError, type RequestOptions, type Session } from './request'
import type { AiSummary, AnswerReceipt, Attempt, BodyFor, GenerationJob, LearningRecord, Mistake, MistakePage, MistakeStatus, ProfileStats, QuizGroup, RecordPage, TopicList, UserProfile } from './types'

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
  const retryableRequest = method === 'GET' || Boolean(idempotencyKey)
  try {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await Taro.request<unknown>({ url: __QINGZHI_API_BASE_URL__ + path, method, data, header, timeout: 15000 })
      } catch (error) {
        if (!retryableRequest || attempt >= 1) throw error
        await new Promise((resolve) => setTimeout(resolve, 500))
      }
    }
  } catch {
    throw new RequestError('NETWORK', '连接中断了，检查网络后再试。', 0, true)
  }
}

let businessSession = readSaved<Session>(SESSION_KEY)
let activeUserId: string | undefined = businessSession?.userId

const request = createRequestClient({
  transport,
  readSession: () => businessSession,
  saveSession(session) {
    businessSession = session
    if (session) activeUserId = session.userId
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
  favorites: () => request<{ items: { fingerprint: string; topicKey: string; topicTitle: string }[] }>({ path: '/v1/favorites' }),
  favoriteStatus: (attemptId: string, questionId: string) => request<{ fingerprint: string; favorited: boolean }>({ path: `/v1/attempts/${encodeURIComponent(attemptId)}/favorites/${encodeURIComponent(questionId)}` }),
  addFavorite: (attemptId: string, questionId: string) => request<{ fingerprint: string }>({ path: `/v1/attempts/${encodeURIComponent(attemptId)}/favorites/${encodeURIComponent(questionId)}`, method: 'PUT' }),
  removeFavorite: (fingerprint: string) => request<void>({ path: `/v1/favorites/${encodeURIComponent(fingerprint)}`, method: 'DELETE' }),
  startFavoritePractice: (fingerprint: string, idempotencyKey: string) => request<Attempt>({ path: `/v1/favorites/${encodeURIComponent(fingerprint)}/practice`, method: 'POST', idempotencyKey }),
  profile: async () => {
    const profile = await request<UserProfile>({ path: '/v1/me' })
    activeUserId = profile.userId
    return profile
  },
  updateProfile: (nickname: string | null) => request<UserProfile>({
    path: '/v1/me', method: 'PATCH', data: { nickname } satisfies BodyFor<'/v1/me', 'patch'>,
  }),
  profileStats: () => request<ProfileStats>({ path: '/v1/me/stats' }),
  topics: (cursor?: string) => request<TopicList>({
    path: '/v1/topics' + (cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''),
  }),
  deleteTopic: (topicKey: string) => request<void>({
    path: `/v1/topics/${encodeURIComponent(topicKey)}`, method: 'DELETE',
  }),
  savedQuiz: (topicKey: string) => request<QuizGroup>({
    path: `/v1/topics/${encodeURIComponent(topicKey)}/quiz`, method: 'POST',
  }),
  generate: (data: TopicInput, idempotencyKey: string) => request<GenerationJob>({
    path: '/v1/generation-jobs', method: 'POST', data: data satisfies BodyFor<'/v1/generation-jobs', 'post'>, idempotencyKey,
  }),
  job: (id: string) => request<GenerationJob>({ path: `/v1/generation-jobs/${encodeURIComponent(id)}` }),
  currentJob: () => request<GenerationJob | null>({ path: '/v1/generation-jobs/current' }),
  activeAttempts: () => request<{ items: Attempt[] }>({ path: '/v1/attempts/active' }),
  saveDraft: (attemptId: string, selectedOptionIds: string[]) => request<Attempt>({
    path: `/v1/attempts/${encodeURIComponent(attemptId)}/draft`, method: 'PUT', data: { selectedOptionIds },
  }),
  startAttempt: (quizId: string, idempotencyKey: string) => request<Attempt>({
    path: '/v1/attempts', method: 'POST', data: { quizId } satisfies BodyFor<'/v1/attempts', 'post'>, idempotencyKey,
  }),
  latestAttempt: (quizId: string) => request<Attempt>({ path: `/v1/quizzes/${encodeURIComponent(quizId)}/latest-attempt` }),
  attempt: (id: string) => request<Attempt>({ path: `/v1/attempts/${encodeURIComponent(id)}` }),
  answer: (attemptId: string, questionId: string, selectedOptionIds: string[]) => request<AnswerReceipt>({
    path: `/v1/attempts/${encodeURIComponent(attemptId)}/answers/${encodeURIComponent(questionId)}`,
    method: 'PUT', data: { selectedOptionIds } satisfies BodyFor<'/v1/attempts/{attempt_id}/answers/{question_id}', 'put'>,
  }),
  attemptAnswer: (attemptId: string, questionIndex: number) => request<AnswerReceipt>({
    path: `/v1/attempts/${encodeURIComponent(attemptId)}/answers/${questionIndex}`,
  }),
  records: (cursor?: string, since?: string) => request<RecordPage>({
    path: '/v1/records?limit=20'
      + (cursor ? `&cursor=${encodeURIComponent(cursor)}` : '')
      + (since ? `&since=${encodeURIComponent(since)}` : ''),
  }),
  record: (id: string) => request<LearningRecord>({ path: `/v1/records/${encodeURIComponent(id)}` }),
  mistakes: (status?: MistakeStatus, topicKey?: string, cursor?: string) => {
    const params = ['limit=20']
    if (status) params.push(`status=${encodeURIComponent(status)}`)
    if (topicKey) params.push(`topicKey=${encodeURIComponent(topicKey)}`)
    if (cursor) params.push(`cursor=${encodeURIComponent(cursor)}`)
    return request<MistakePage>({ path: `/v1/mistakes?${params.join('&')}` })
  },
  mistake: (fingerprint: string) => request<Mistake>({
    path: `/v1/mistakes/${encodeURIComponent(fingerprint)}`,
  }),
  updateMistake: (fingerprint: string, status: MistakeStatus) => request<Mistake>({
    path: `/v1/mistakes/${encodeURIComponent(fingerprint)}`,
    method: 'PATCH',
    data: { status } satisfies BodyFor<'/v1/mistakes/{fingerprint}', 'patch'>,
  }),
  removeMistake: (fingerprint: string) => request<void>({
    path: `/v1/mistakes/${encodeURIComponent(fingerprint)}`,
    method: 'DELETE',
  }),
  startMistakePractice: (fingerprint: string, idempotencyKey: string) => request<Attempt>({
    path: `/v1/mistakes/${encodeURIComponent(fingerprint)}/practice`, method: 'POST', idempotencyKey,
  }),
  retrySummary: (id: string, idempotencyKey: string) => request<AiSummary>({
    path: `/v1/records/${encodeURIComponent(id)}/ai-summary/retry`, method: 'POST', idempotencyKey,
  }),
}

export type TopicInput = { topic: string } | { topicKey: string }
export type PendingGeneration = {
  kind: 'generate'
  input: TopicInput
  title: string
  key: string
  requestedAt: number
  jobId?: string
  status?: GenerationJob['status']
  phase?: GenerationJob['phase']
}

function userStorageKey(key: string) {
  return `${key}.${activeUserId ?? 'unresolved'}`
}

export function readPendingGenerations(): PendingGeneration[] {
  const entries = readSaved<PendingGeneration[]>(userStorageKey(`${SESSION_KEY}.generation.v1`))
  if (!Array.isArray(entries)) return []
  const valid = entries.filter((entry) => entry?.kind === 'generate' && typeof entry.key === 'string'
    && typeof entry.requestedAt === 'number'
    && typeof entry.title === 'string' && entry.input && typeof entry.input === 'object'
    && ('topic' in entry.input
      ? typeof entry.input.topic === 'string'
      : typeof entry.input.topicKey === 'string'))
  return prunePendingGenerations(valid)
}

export function savePendingGenerations(entries: PendingGeneration[]) {
  save(userStorageKey(`${SESSION_KEY}.generation.v1`), entries.length ? entries : undefined)
}
