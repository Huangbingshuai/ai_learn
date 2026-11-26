import assert from 'node:assert/strict'
import test from 'node:test'
import type { Mistake, MistakePage } from '../src/api/types'
import {
  appendMistakePage,
  createMistakeBookState,
  groupMistakesByTopic,
  removeMistakeFromBook,
  practiceFinished,
  mistakeBookViewState,
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
  assert.deepEqual(TAB_DESTINATIONS.map(({ key }) => key), ['home', 'questionBank', 'answer', 'mistakeBook', 'favorites', 'profile'])
  assert.deepEqual(TAB_DESTINATIONS.map(({ label }) => label), ['学习', '题库', '答题', '错题本', '收藏夹', '我的'])
})

test('groups mistakes by topic while preserving question order within each topic', () => {
  const items = [
    mistake('a'),
    { ...mistake('b'), topicKey: 'langgraph', topicTitle: 'LangGraph' },
    { ...mistake('c'), topicTitle: '四季成因' },
  ]

  assert.deepEqual(groupMistakesByTopic(items).map((group) => ({
    topicKey: group.topicKey,
    title: group.title,
    fingerprints: group.items.map(({ fingerprint }) => fingerprint),
  })), [
    { topicKey: 'seasons', title: '四季成因', fingerprints: ['a', 'c'] },
    { topicKey: 'langgraph', title: 'LangGraph', fingerprints: ['b'] },
  ])
})

test('removes only the requested mistake and updates the count', () => {
  const state = {
    ...createMistakeBookState(),
    items: [mistake('a'), mistake('b')],
    pendingCount: 2,
    loaded: true,
  }

  const next = removeMistakeFromBook(state, 'a')

  assert.deepEqual(next.items.map(({ fingerprint }) => fingerprint), ['b'])
  assert.equal(next.pendingCount, 1)
  assert.equal(next.loaded, true)
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
