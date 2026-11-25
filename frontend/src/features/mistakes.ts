import type { Mistake, MistakePage, MistakeStatus, MistakeTopic } from '../api/types'

export const TAB_DESTINATIONS = [
  { key: 'home', label: '学习' },
  { key: 'questionBank', label: '题库' },
  { key: 'mistakeBook', label: '错题本' },
  { key: 'profile', label: '我的' },
] as const

export type MistakeBookState = {
  status: MistakeStatus
  topicKey: string | null
  items: Mistake[]
  nextCursor: string | null
  pendingCount: number
  topics: MistakeTopic[]
  loaded: boolean
  scrollTop: number
}

export type MistakeBookViewState = 'loading' | 'error' | 'empty' | 'list'

export function practiceFinished(answeredCount: number, totalQuestions: number) {
  return totalQuestions > 0 && answeredCount >= totalQuestions
}

export function createMistakeBookState(): MistakeBookState {
  return {
    status: 'to_review',
    topicKey: null,
    items: [],
    nextCursor: null,
    pendingCount: 0,
    topics: [],
    loaded: false,
    scrollTop: 0,
  }
}

export function setMistakeFilters(
  state: MistakeBookState,
  filters: { status?: MistakeStatus; topicKey?: string | null },
): MistakeBookState {
  const status = filters.status ?? state.status
  const topicKey = filters.topicKey === undefined ? state.topicKey : filters.topicKey
  if (status === state.status && topicKey === state.topicKey) return state
  return { ...state, status, topicKey, items: [], nextCursor: null, loaded: false, scrollTop: 0 }
}

export function appendMistakePage(
  state: MistakeBookState,
  page: MistakePage,
  append: boolean,
): MistakeBookState {
  const items = append ? [...state.items] : []
  const positions = new Map(items.map((item, index) => [item.fingerprint, index]))
  for (const item of page.items) {
    const position = positions.get(item.fingerprint)
    if (position === undefined) {
      positions.set(item.fingerprint, items.length)
      items.push(item)
    } else {
      items[position] = item
    }
  }
  return {
    ...state,
    items,
    nextCursor: page.nextCursor,
    pendingCount: page.pendingCount,
    topics: page.topics,
    loaded: true,
  }
}

export function mistakeBookViewState(
  state: MistakeBookState,
  loading: boolean,
  error: string,
): MistakeBookViewState {
  if (!state.items.length && loading) return 'loading'
  if (!state.items.length && error) return 'error'
  if (!state.items.length) return 'empty'
  return 'list'
}
