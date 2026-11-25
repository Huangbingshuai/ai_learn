import { Button, Picker, Text, View } from '@tarojs/components'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { messageForError } from '../api/request'
import type { AnswerReceipt, Evidence, Mistake } from '../api/types'
import { Action, ComicRow, ErrorNote, StatusPanel, Tape } from '../components/comic'
import { ReceiptView } from './quiz'
import {
  appendMistakePage,
  mistakeBookViewState,
  setMistakeFilters,
  type MistakeBookState,
} from './mistakes'
import { formatCompletionDate } from './flow'

export function MistakeBookScreen({ state, onState, onOpen }: {
  state: MistakeBookState
  onState: (update: (previous: MistakeBookState) => MistakeBookState) => void
  onOpen: (fingerprint: string) => void
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
    api.mistakes(state.status, state.topicKey ?? undefined).then((page) => {
      if (!active || requestId.current !== currentRequest) return
      onState((previous) => appendMistakePage(previous, page, false))
    }).catch((problem) => {
      if (active && requestId.current === currentRequest) setError(messageForError(problem))
    }).finally(() => {
      if (active && requestId.current === currentRequest) setLoading(false)
    })
    return () => { active = false }
  }, [onState, reload, state.loaded, state.status, state.topicKey])

  async function loadMore() {
    if (!state.nextCursor || loading) return
    const currentRequest = ++requestId.current
    setLoading(true)
    setError('')
    try {
      const page = await api.mistakes(state.status, state.topicKey ?? undefined, state.nextCursor)
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
  if (viewState === 'empty') return <>
    <MistakeBookFilters state={state} onState={onState} />
    <StatusPanel title={state.topicKey ? '这个主题下暂无错题' : state.status === 'mastered' ? '还没有已掌握的错题' : '暂时没有待复习的错题'}>
      <Text className='body-copy'>{state.status === 'mastered'
        ? '掌握的题目会保留在这里，方便你随时回看。'
        : '练习中答错的题目会自动收进错题本。'}</Text>
    </StatusPanel>
  </>

  return <>
    <MistakeBookFilters state={state} onState={onState} />
    <View className='mistake-list-head'>
      <Text className='h2'>{state.status === 'to_review' ? `待复习 · ${state.pendingCount}` : '已掌握'}</Text>
      <Tape tone='yellow'>{state.items.length} 题</Tape>
    </View>
    <View className='comic-row-list mistake-list'>
      {state.items.map((item) => <ComicRow key={item.fingerprint} mint={item.status === 'mastered'}
        secondary={`${item.topicTitle} · 答错 ${item.wrongCount} 次 · 最近 ${formatCompletionDate(item.lastWrongAt)}`}
        onClick={() => onOpen(item.fingerprint)}>
        {item.question.stem}
      </ComicRow>)}
    </View>
    {error && <ErrorNote>{error}</ErrorNote>}
    {state.nextCursor && <View className='mistake-more'>
      <Action secondary disabled={loading} onClick={() => { void loadMore() }}>
        {loading ? '正在加载…' : error ? '重试加载' : '加载更多错题'}
      </Action>
    </View>}
  </>
}

function MistakeBookFilters({ state, onState }: {
  state: MistakeBookState
  onState: (update: (previous: MistakeBookState) => MistakeBookState) => void
}) {
  const topicLabels = ['全部主题', ...state.topics.map(({ title }) => title)]
  const topicIndex = state.topicKey ? state.topics.findIndex(({ topicKey }) => topicKey === state.topicKey) + 1 : 0
  return <View className='mistake-filters'>
    <View className='mistake-status-tabs'>
      <Button className={`mistake-status${state.status === 'to_review' ? ' current' : ''}`}
        hoverClass='row-press' onClick={() => onState((previous) => setMistakeFilters(previous, { status: 'to_review' }))}>
        待复习 <Text className='mistake-count'>{state.pendingCount}</Text>
      </Button>
      <Button className={`mistake-status${state.status === 'mastered' ? ' current' : ''}`}
        hoverClass='row-press' onClick={() => onState((previous) => setMistakeFilters(previous, { status: 'mastered' }))}>
        已掌握
      </Button>
    </View>
    {state.topics.length > 0 && <Picker mode='selector' range={topicLabels} value={Math.max(topicIndex, 0)}
      onChange={(event) => {
        const selected = Number(event.detail.value)
        onState((previous) => setMistakeFilters(previous, {
          topicKey: selected === 0 ? null : previous.topics[selected - 1]?.topicKey ?? null,
        }))
      }}>
      <View className='mistake-topic-picker'>
        <Text>主题 · {state.topicKey ? state.topics[topicIndex - 1]?.title ?? '已选主题' : '全部'}</Text>
        <Text className='topic-arrow'>⌄</Text>
      </View>
    </Picker>}
  </View>
}

export function MistakeDetailScreen({ fingerprint, onUpdated, onEvidence }: {
  fingerprint: string
  onUpdated: (previous: Mistake, next: Mistake) => void
  onEvidence: (evidence: Evidence[]) => void
}) {
  const [item, setItem] = useState<Mistake>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [reload, setReload] = useState(0)
  const requestId = useRef(0)

  useEffect(() => {
    const currentRequest = ++requestId.current
    let active = true
    setLoading(true)
    setError('')
    api.mistake(fingerprint).then((result) => {
      if (active && requestId.current === currentRequest) setItem(result)
    }).catch((problem) => {
      if (active && requestId.current === currentRequest) setError(messageForError(problem))
    }).finally(() => {
      if (active && requestId.current === currentRequest) setLoading(false)
    })
    return () => { active = false }
  }, [fingerprint, reload])

  async function toggleMastered() {
    if (!item || saving) return
    setSaving(true)
    setError('')
    try {
      const next = await api.updateMistake(
        fingerprint,
        item.status === 'mastered' ? 'to_review' : 'mastered',
      )
      setItem(next)
      onUpdated(item, next)
    } catch (problem) {
      setError(messageForError(problem))
    } finally {
      setSaving(false)
    }
  }

  if (!item) return <StatusPanel title={loading ? '正在加载错题…' : '暂时无法查看这道题'}>
    {error && <ErrorNote>{error}</ErrorNote>}
    {error && <Action onClick={() => setReload((value) => value + 1)}>重新加载</Action>}
  </StatusPanel>

  const receipt: AnswerReceipt = item.latestReceipt
  return <>
    <View className='mistake-detail-head'>
      <View className='mistake-detail-tags'>
        <Tape tone='yellow'>{item.topicTitle}</Tape>
        <Tape tone={item.status === 'mastered' ? 'mint' : 'paper'}>{item.status === 'mastered' ? '已掌握' : '待复习'}</Tape>
      </View>
      <Text className='mistake-detail-meta'>答错 {item.wrongCount} 次 · 最近 {formatCompletionDate(item.lastWrongAt)}</Text>
      <Text className='mistake-detail-question'>{item.question.stem}</Text>
    </View>
    <Text className='h2'>最近一次作答</Text>
    <ReceiptView receipt={receipt} onEvidence={onEvidence} />
    {error && <ErrorNote>{error}</ErrorNote>}
    <View className='actions'>
      <Action secondary disabled={saving} onClick={() => { void toggleMastered() }}>
        {saving ? '正在保存…' : item.status === 'mastered' ? '重新加入待复习' : '标记为已掌握'}
      </Action>
    </View>
  </>
}
