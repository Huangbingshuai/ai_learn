import assert from 'node:assert/strict'
import test from 'node:test'
import type { Mistake, MistakePage } from '../src/api/types'
import {
  appendMistakePage,
  createMistakeBookState,
  practiceFinished,
  mistakeBookViewState,
  setMistakeFilters,
  TAB_DESTINATIONS,
} from '../src/features/mistakes'

function mistake(fingerprint: string, wrongCount = 1): Mistake {
  return {
    fingerprint,
    question: {} as Mistake['question'],
    latestReceipt: {} as Mistake['latestReceipt'],
    topicKey: 'seasons',
    topicTitle: '四季成因',
    wrongCount,
    firstWrongAt: '2026-01-01T00:00:00Z',
    lastWrongAt: '2026-01-02T00:00:00Z',
    status: 'to_review',
    masteredAt: null,
  }
}

function page(items: Mistake[], nextCursor: string | null = null): MistakePage {
  return { items, nextCursor, pendingCount: 3, topics: [{ topicKey: 'seasons', title: '四季成因' }] }
}

test('shows learning, question bank, answers, mistakes and profile in bottom navigation', () => {
  assert.deepEqual(TAB_DESTINATIONS.map(({ key }) => key), ['home', 'questionBank', 'answer', 'mistakeBook', 'profile'])
  assert.deepEqual(TAB_DESTINATIONS.map(({ label }) => label), ['学习', '题库', '答题', '错题本', '我的'])
})

test('changing status or topic filters clears stale pages but keeps total and topic options', () => {
  const current = {
    ...createMistakeBookState(),
    items: [mistake('a')],
    nextCursor: 'cursor',
    pendingCount: 3,
    topics: [{ topicKey: 'seasons', title: '四季成因' }],
    loaded: true,
    scrollTop: 120,
  }

  const filtered = setMistakeFilters(current, { status: 'mastered', topicKey: 'seasons' })

  assert.equal(filtered.status, 'mastered')
  assert.equal(filtered.topicKey, 'seasons')
  assert.deepEqual(filtered.items, [])
  assert.equal(filtered.nextCursor, null)
  assert.equal(filtered.loaded, false)
  assert.equal(filtered.scrollTop, 0)
  assert.equal(filtered.pendingCount, 3)
  assert.equal(filtered.topics.length, 1)
})

test('appends pages without duplicating a semantic question and adopts the latest row', () => {
  const current = { ...createMistakeBookState(), items: [mistake('a'), mistake('b')], loaded: true }
  const next = appendMistakePage(current, page([mistake('b', 4), mistake('c')], 'next'), true)

  assert.deepEqual(next.items.map(({ fingerprint }) => fingerprint), ['a', 'b', 'c'])
  assert.equal(next.items[1].wrongCount, 4)
  assert.equal(next.nextCursor, 'next')
  assert.equal(next.pendingCount, 3)
  assert.equal(next.loaded, true)
})

test('describes loading, errors, empty filters and populated lists separately', () => {
  const empty = createMistakeBookState()

  assert.equal(mistakeBookViewState(empty, true, ''), 'loading')
  assert.equal(mistakeBookViewState(empty, false, '网络错误'), 'error')
  assert.equal(mistakeBookViewState({ ...empty, loaded: true }, false, ''), 'empty')
  assert.equal(mistakeBookViewState({ ...empty, items: [mistake('a')] }, false, ''), 'list')
})

test('returns from single-question practice only after its answer is accepted', () => {
  assert.equal(practiceFinished(0, 1), false)
  assert.equal(practiceFinished(1, 1), true)
  assert.equal(practiceFinished(2, 1), true)
})
