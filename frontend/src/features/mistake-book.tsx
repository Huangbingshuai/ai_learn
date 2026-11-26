import { Button, Text, View } from '@tarojs/components'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { messageForError } from '../api/request'
import type { MistakePage } from '../api/types'
import { Action, ErrorNote, StatusPanel } from '../components/comic'
import {
  appendMistakePage,
  groupMistakesByTopic,
  mistakeBookViewState,
  type MistakeBookState,
} from './mistakes'

export function MistakeBookScreen({ state, onState, onOpen, onHome, onQuestionBank }: {
  state: MistakeBookState
  onState: (update: (previous: MistakeBookState) => MistakeBookState) => void
  onOpen: (fingerprints: string[]) => void
  onHome: () => void
  onQuestionBank: () => void
}) {
  const [loading, setLoading] = useState(!state.loaded)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const requestId = useRef(0)

  useEffect(() => {
    if (state.loaded) return
    const currentRequest = ++requestId.current
    let active = true
    setLoading(true)
    setError('')
    async function loadAll() {
      let cursor: string | undefined
      let combined: MistakePage | undefined
      do {
        const page = await api.mistakes(undefined, undefined, cursor)
        combined = combined ? { ...page, items: [...combined.items, ...page.items] } : page
        cursor = page.nextCursor ?? undefined
      } while (cursor && active)
      return combined
    }
    loadAll().then((page) => {
      if (!page || !active || requestId.current !== currentRequest) return
      onState((previous) => appendMistakePage(previous, page, false))
    }).catch((problem) => {
      if (active && requestId.current === currentRequest) setError(messageForError(problem))
    }).finally(() => {
      if (active && requestId.current === currentRequest) setLoading(false)
    })
    return () => { active = false }
  }, [onState, reload, state.loaded])

  async function loadMore() {
    if (!state.nextCursor || loading) return
    const currentRequest = ++requestId.current
    setLoading(true)
    setError('')
    try {
      const page: MistakePage = await api.mistakes(undefined, undefined, state.nextCursor)
      if (requestId.current === currentRequest) onState((previous) => appendMistakePage(previous, page, true))
    } catch (problem) {
      if (requestId.current === currentRequest) setError(messageForError(problem))
    } finally {
      if (requestId.current === currentRequest) setLoading(false)
    }
  }

  const viewState = mistakeBookViewState(state, loading, error)
  if (viewState === 'loading') return <StatusPanel title='正在加载错题本…' />
  if (viewState === 'error') return <StatusPanel title='暂时无法查看错题本'>
    <ErrorNote>{error}</ErrorNote>
    <Action onClick={() => setReload((value) => value + 1)}>重新加载</Action>
  </StatusPanel>
  if (viewState === 'empty') return <StatusPanel title='还没有错题'>
    <View className='empty-actions empty-actions-split'>
      <Action secondary onClick={onQuestionBank}>去题库</Action>
      <Action onClick={onHome}>去学习</Action>
    </View>
  </StatusPanel>

  return <>
    <View className='topic-library-list'>
      {groupMistakesByTopic(state.items).map((group) => <Button key={group.topicKey}
        className='mistake-topic-card ui-button' hoverClass='press-offset'
        onClick={() => onOpen(group.items.map((item) => item.fingerprint))}>
        <Text className='mistake-topic-title'>{group.title}</Text>
        <Text className='mistake-topic-badge'>{group.items.length} 道错题</Text>
        <Text className='mistake-topic-arrow'>›</Text>
      </Button>)}
    </View>
    {error && <ErrorNote>{error}</ErrorNote>}
    {state.nextCursor && <View className='mistake-more'>
      <Action secondary disabled={loading} onClick={() => { void loadMore() }}>
        {loading ? '正在加载…' : error ? '重试加载' : '加载更多错题'}
      </Action>
    </View>}
  </>
}
