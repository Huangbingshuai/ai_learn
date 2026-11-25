import assert from 'node:assert/strict'
import { test } from 'node:test'
import { attemptNeedsRegeneration, createRequestClient, RequestError, type RequestOptions, type Session } from '../src/api/request'

test('shares one login after concurrent unauthorized requests and preserves the write idempotency key', async () => {
  let session: Session | undefined = { accessToken: 'old', expiresAt: '2099-01-01T00:00:00Z' }
  let logins = 0
  const writes: RequestOptions[] = []
  const request = createRequestClient({
    async transport(options, token) {
      writes.push(options)
      return token === 'old'
        ? { statusCode: 401, data: {} }
        : { statusCode: 200, data: { ok: true } }
    },
    readSession: () => session,
    saveSession: (next) => { session = next },
    login: async () => {
      logins += 1
      return { accessToken: 'new', expiresAt: '2099-01-01T00:00:00Z' }
    },
  })
  const original = { path: '/v1/attempts', method: 'POST' as const, data: { quizId: 'id' }, idempotencyKey: 'first' }
  assert.deepEqual(await Promise.all([request(original), request(original)]), [{ ok: true }, { ok: true }])
  assert.equal(logins, 1)
  assert.equal(writes.length, 4)
  assert.ok(writes.every((write) => write.idempotencyKey === 'first' && write.data === original.data))
})

test('returns the service error body without replaying a rate-limited request', async () => {
  let calls = 0
  const request = createRequestClient({
    async transport() {
      calls += 1
      return { statusCode: 429, data: { code: 'RATE_LIMITED', message: '稍后再试。', requestId: 'req-1', retryable: true } }
    },
    readSession: () => ({ accessToken: 'one', expiresAt: '2099-01-01T00:00:00Z' }),
    saveSession() {},
    login: async () => { throw new Error('unexpected login') },
  })
  await assert.rejects(request({ path: '/v1/generation-jobs', method: 'POST', idempotencyKey: 'job' }), {
    code: 'RATE_LIMITED', status: 429, requestId: 'req-1', retryable: true,
  })
  assert.equal(calls, 1)
})

test('regenerates an expired, closed, or missing attempt while allowing network retries', () => {
  assert.equal(attemptNeedsRegeneration(new RequestError('QUIZ_EXPIRED', '已过期', 409)), true)
  assert.equal(attemptNeedsRegeneration(new RequestError('ATTEMPT_CLOSED', '已结束', 409)), true)
  assert.equal(attemptNeedsRegeneration(new RequestError('NOT_FOUND', '未找到', 404)), true)
  assert.equal(attemptNeedsRegeneration(new RequestError('ATTEMPT_UNAVAILABLE', '已结束', 410)), true)
  assert.equal(attemptNeedsRegeneration(new RequestError('NETWORK', '网络不通', 0, true)), false)
})
