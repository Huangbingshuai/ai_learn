import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { PendingGeneration } from '../src/api/client'
import {
  formatElapsed, generationPhases, nextPollDelay, optionAppearance, pendingIdentity,
  prunePendingGenerations, selectOption, restartAvailable, topicInputError,
} from '../src/features/flow'

test('replaces single-choice selections and toggles multiple selections independently', () => {
  assert.deepEqual(selectOption('single', ['a'], 'b'), ['b'])
  assert.deepEqual(selectOption('judge', ['a'], 'b'), ['b'])
  assert.deepEqual(selectOption('multi', ['a'], 'b'), ['a', 'b'])
  assert.deepEqual(selectOption('multi', ['a', 'b'], 'a'), ['b'])
})

test('marks every correct option and highlights a selected distractor after submission', () => {
  assert.equal(optionAppearance('a', ['c'], ['a', 'b']), 'correct')
  assert.equal(optionAppearance('b', ['c'], ['a', 'b']), 'correct')
  assert.equal(optionAppearance('c', ['c'], ['a', 'b']), 'incorrect')
  assert.equal(optionAppearance('d', ['c'], ['a', 'b']), '')
  assert.equal(optionAppearance('c', ['c']), 'selected')
})

test('requires fresh generation with thirty minutes of validity or less', () => {
  const now = Date.parse('2026-11-25T01:00:00Z')
  assert.equal(restartAvailable('2026-11-25T01:30:01Z', now), true)
  assert.equal(restartAvailable('2026-11-25T01:30:00Z', now), false)
  assert.equal(restartAvailable('2026-11-25T00:59:00Z', now), false)
  assert.equal(restartAvailable('not a date', now), false)
})

test('uses the trimmed code point count for the topic limit', () => {
  assert.match(topicInputError('  ') ?? '', /请输入/)
  assert.match(topicInputError(' 月 ') ?? '', /2–80/)
  assert.equal(topicInputError('  月相  '), undefined)
  assert.equal(topicInputError('月'.repeat(79) + '🌙'), undefined)
  assert.match(topicInputError('月'.repeat(80) + '🌙') ?? '', /2–80/)
})

test('displays the server generation phase and caps polling delay at eight seconds', () => {
  assert.equal(generationPhases.RESOLVING_TOPIC, '理解主题')
  assert.equal(generationPhases.VERIFYING, '核验答案与讲解')
  assert.equal(formatElapsed(1000, 0), '0 秒')
  assert.equal(formatElapsed(1000, 66000), '1 分 5 秒')
  assert.deepEqual([2000, 3000, 4500, 6750, 8000].map(nextPollDelay), [3000, 4500, 6750, 8000, 8000])
})

test('drops stale, failed and duplicate pending generations', () => {
  const now = Date.parse('2026-11-25T08:00:00Z')
  const entry = (
    key: string,
    input: PendingGeneration['input'],
    requestedAt: number,
    status?: PendingGeneration['status'],
  ): PendingGeneration => ({ kind: 'generate', input, title: key, key, requestedAt, status })
  const entries = [
    entry('stale', { topic: '四季成因' }, now - 25 * 60 * 60 * 1000),
    entry('old', { topic: 'Vibe Coding' }, now - 1000),
    entry('new', { topic: '  VIBE   coding ' }, now - 500),
    entry('failed', { topic: '月相' }, now, 'FAILED'),
    entry('keyed', { topicKey: 'seasons' }, now - 2000),
    entry('fresh', { topic: '量子纠缠' }, now - 2000),
  ]

  assert.deepEqual(prunePendingGenerations(entries, now).map((item) => item.key), ['new', 'keyed', 'fresh'])
  assert.equal(pendingIdentity(entries[1]), 'topic:vibe coding')
  assert.equal(pendingIdentity(entries[4]), 'key:seasons')
})
