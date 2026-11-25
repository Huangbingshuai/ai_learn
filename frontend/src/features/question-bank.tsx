import { Button, Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { messageForError } from '../api/request'
import type { Topic } from '../api/types'
import { Action, ErrorNote, Sheet, StatusPanel } from '../components/comic'

export function QuestionBankScreen({ onTopic }: {
  onTopic: (topic: Topic) => void
}) {
  const [topics, setTopics] = useState<Topic[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [deleteTarget, setDeleteTarget] = useState<Topic>()
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(() => new Set())
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const deletableTopics = topics.filter((topic) => topic.canDelete)
  const selectedTopics = deletableTopics.filter((topic) => selectedKeys.has(topic.topicKey))

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    api.topics().then((page) => {
      if (!active) return
      setTopics(page.items)
      setCursor(page.nextCursor ?? null)
    }).catch((problem) => {
      if (active) setError(messageForError(problem))
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [reload])

  async function loadMore() {
    if (!cursor || loadingMore) return
    setLoadingMore(true)
    setError('')
    try {
      const page = await api.topics(cursor)
      setTopics((previous) => {
        const shown = new Set(previous.map((topic) => topic.topicKey))
        return [...previous, ...page.items.filter((topic) => !shown.has(topic.topicKey))]
      })
      setCursor(page.nextCursor ?? null)
    } catch (problem) {
      setError(messageForError(problem))
    } finally {
      setLoadingMore(false)
    }
  }

  async function deleteTopic() {
    if (!deleteTarget || deleting) return
    setDeleting(true)
    setError('')
    try {
      await api.deleteTopic(deleteTarget.topicKey)
      setTopics((previous) => previous.filter((topic) => topic.topicKey !== deleteTarget.topicKey))
      setDeleteTarget(undefined)
    } catch (problem) {
      setError(messageForError(problem))
    } finally {
      setDeleting(false)
    }
  }

  function toggleSelection(topic: Topic) {
    if (!topic.canDelete || deleting) return
    setSelectedKeys((previous) => {
      const next = new Set(previous)
      if (next.has(topic.topicKey)) next.delete(topic.topicKey)
      else next.add(topic.topicKey)
      return next
    })
  }

  function toggleSelectAll() {
    if (deleting) return
    setSelectedKeys(selectedTopics.length === deletableTopics.length
      ? new Set()
      : new Set(deletableTopics.map((topic) => topic.topicKey)))
  }

  function toggleSelectionMode() {
    if (deleting) return
    if (selectionMode) setSelectedKeys(new Set())
    setSelectionMode((previous) => !previous)
  }

  async function deleteSelectedTopics() {
    if (!selectedTopics.length || deleting) return
    setDeleting(true)
    setError('')
    try {
      const results = await Promise.allSettled(selectedTopics.map((topic) => api.deleteTopic(topic.topicKey)))
      const succeededKeys = new Set(selectedTopics.filter((_, index) => results[index].status === 'fulfilled')
        .map((topic) => topic.topicKey))
      const failedKeys = new Set(selectedTopics.filter((_, index) => results[index].status === 'rejected')
        .map((topic) => topic.topicKey))
      setTopics((previous) => previous.filter((topic) => !succeededKeys.has(topic.topicKey)))
      setSelectedKeys(failedKeys)
      setBatchDeleteOpen(false)
      if (failedKeys.size) {
        setError(`已删除 ${succeededKeys.size} 个，${failedKeys.size} 个删除失败；失败项仍保留，可重新选择后重试。`)
      } else {
        setSelectionMode(false)
      }
    } catch (problem) {
      setError(messageForError(problem))
    } finally {
      setDeleting(false)
    }
  }

  return <>
    {!loading && topics.length > 0 && <View className='library-intro'>
      <Button className={`topic-selection-toggle ui-button${selectionMode ? ' active' : ''}`}
        hoverClass='row-press' onClick={toggleSelectionMode}>{selectionMode ? '取消选择' : '选择'}</Button>
    </View>}
    {selectionMode && deletableTopics.length > 0 && <View className='topic-selection-panel'>
      <View className='topic-selection-tools'>
        <Button className='topic-select-all ui-button' hoverClass='none' onClick={toggleSelectAll}>
          {selectedTopics.length === deletableTopics.length ? '取消全选' : '全选已加载'}
        </Button>
        <Text className='topic-selected-count'>已选 {selectedTopics.length} 个</Text>
      </View>
      <Button className='topic-batch-delete ui-button' hoverClass='row-press' disabled={!selectedTopics.length || deleting}
        onClick={() => setBatchDeleteOpen(true)}>删除已选主题{selectedTopics.length ? `（${selectedTopics.length}）` : ''}</Button>
    </View>}
    {error && <ErrorNote>{error}</ErrorNote>}
    {loading ? <StatusPanel title='正在加载题库…' /> : topics.length === 0 ? <StatusPanel title='题库还是空的'>
      <Text className='body-copy'>回到“学习”输入感兴趣的内容，生成练习后会自动收进题库。</Text>
    </StatusPanel> : <View className='topic-library-list'>
      {topics.map((topic) => <View className='topic-library-row' key={topic.topicKey}>
        <Button className={`topic ui-button topic-library-card user-topic${selectedKeys.has(topic.topicKey) ? ' is-selected' : ''}`}
          hoverClass='press-offset' onClick={() => selectionMode ? toggleSelection(topic) : onTopic(topic)}>
          {selectionMode && topic.canDelete && <Text className={`topic-select-mark${selectedKeys.has(topic.topicKey) ? ' checked' : ''}`} aria-hidden>
            {selectedKeys.has(topic.topicKey) ? '✓' : ''}
          </Text>}
          <View className='topic-symbol' aria-hidden />
          <View className='topic-library-copy'>
            <Text className='topic-title'>{topic.title}</Text>
            {topic.attemptId && typeof topic.answeredCount === 'number'
              && typeof topic.totalQuestions === 'number'
              && <Text className='topic-progress'>{topic.attemptStatus === 'PASSED' ? '已完成' : '已答'} {topic.answeredCount}/{topic.totalQuestions} 题</Text>}
            {topic.originalInput && topic.originalInput.trim() !== topic.title.trim()
              && <Text className='topic-original-input'>原始主题：{topic.originalInput}</Text>}
          </View>
          <Text className='topic-arrow' aria-hidden>›</Text>
        </Button>
        {topic.canDelete && !selectionMode && <Button className='topic-delete-visible ui-button' hoverClass='row-press'
          aria-label={`删除主题${topic.title}`} onClick={() => setDeleteTarget(topic)}>
          <Text aria-hidden>×</Text><Text>删除</Text>
        </Button>}
      </View>)}
      {cursor && <View className='topic-load-more'>
        <Action secondary disabled={loadingMore} onClick={() => { void loadMore() }}>
          {loadingMore ? '正在加载…' : '加载更多主题'}
        </Action>
      </View>}
      {selectionMode && selectedTopics.length > 0 && <Button className='topic-batch-delete bottom ui-button' hoverClass='row-press'
        disabled={deleting} onClick={() => setBatchDeleteOpen(true)}>删除已选主题（{selectedTopics.length}）</Button>}
    </View>}
    {error && !loading && <Action secondary onClick={() => setReload((value) => value + 1)}>重新加载</Action>}
    {deleteTarget && <Sheet title='确认删除主题？' onClose={() => setDeleteTarget(undefined)}>
      <Text className='body-copy'>「{deleteTarget.title}」会从你的题库移除，已有学习记录仍会保留。</Text>
      <View className='topic-delete-actions'>
        <Action secondary disabled={deleting} onClick={() => setDeleteTarget(undefined)}>先保留</Action>
        <Action disabled={deleting} onClick={() => { void deleteTopic() }}>{deleting ? '正在删除…' : '确认删除'}</Action>
      </View>
    </Sheet>}
    {batchDeleteOpen && <Sheet title={`删除 ${selectedTopics.length} 个主题？`} onClose={() => { if (!deleting) setBatchDeleteOpen(false) }}>
      <Text className='body-copy'>选中的主题将从题库移除，已有学习记录会保留。</Text>
      <View className='topic-delete-actions'>
        <Action secondary disabled={deleting} onClick={() => setBatchDeleteOpen(false)}>先保留</Action>
        <Action disabled={deleting} onClick={() => { void deleteSelectedTopics() }}>{deleting ? '正在删除…' : `确认删除 ${selectedTopics.length} 个`}</Action>
      </View>
    </Sheet>}
  </>
}
