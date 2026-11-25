import assert from 'node:assert/strict'
import test from 'node:test'
import type { Mistake, MistakePage } from '../src/api/types'
import {
  appendMistakePage,
  createMistakeBookState,
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

test('adds the mistake book as a third bottom navigation tab', () => {
  assert.deepEqual(TAB_DESTINATIONS.map(({ key }) => key), ['home', 'history', 'mistakeBook'])
  assert.equal(TAB_DESTINATIONS[2].label, '错题本')
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
