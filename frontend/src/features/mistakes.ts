import type { Mistake, MistakePage } from '../api/types'

export type MistakeTopicGroup = { topicKey: string; title: string; items: Mistake[] }

export function groupMistakesByTopic(items: Mistake[]): MistakeTopicGroup[] {
  const groups = new Map<string, MistakeTopicGroup>()
  for (const item of items) {
    let group = groups.get(item.topicKey)
    if (!group) {
      group = { topicKey: item.topicKey, title: item.topicTitle, items: [] }
      groups.set(item.topicKey, group)
    }
    group.items.push(item)
  }
  return [...groups.values()]
}

export function removeMistakeFromBook(state: MistakeBookState, fingerprint: string): MistakeBookState {
  if (!state.items.some((item) => item.fingerprint === fingerprint)) return state
  return {
    ...state,
    items: state.items.filter((item) => item.fingerprint !== fingerprint),
    pendingCount: Math.max(0, state.pendingCount - 1),
  }
}

export const TAB_DESTINATIONS = [
  { key: 'home', label: '学习' },
  { key: 'questionBank', label: '题库' },
  { key: 'answer', label: '答题' },
  { key: 'mistakeBook', label: '错题本' },
  { key: 'favorites', label: '收藏夹' },
  { key: 'profile', label: '我的' },
] as const

export type MistakeBookState = {
  items: Mistake[]
  nextCursor: string | null
  pendingCount: number
  loaded: boolean
  scrollTop: number
}

export type MistakeBookViewState = 'loading' | 'error' | 'empty' | 'list'

export function practiceFinished(answeredCount: number, totalQuestions: number) {
  return totalQuestions > 0 && answeredCount >= totalQuestions
}

export function createMistakeBookState(): MistakeBookState {
  return {
    items: [],
    nextCursor: null,
    pendingCount: 0,
    loaded: false,
    scrollTop: 0,
  }
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
