import { Button, Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { messageForError } from '../api/request'
import type { Topic } from '../api/types'
import { Action, ComicRow, ErrorNote, Sheet, StatusPanel } from '../components/comic'

export function QuestionBankScreen({ onTopic, onRedo, onHome }: {
  onTopic: (topic: Topic) => void
  onRedo: (topic: Topic) => void
  onHome: () => void
}) {
  const [topicPages, setTopicPages] = useState<Topic[][]>([])
  const [pageCursors, setPageCursors] = useState<(string | null)[]>([])
  const [pageIndex, setPageIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingPage, setLoadingPage] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(() => new Set())
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false)
  const [redoPickerOpen, setRedoPickerOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const topics = topicPages[pageIndex] ?? []
  const allLoadedTopics = topicPages.flat()
  const deletableTopics = allLoadedTopics.filter((topic) => topic.canDelete)
  const pageDeletableTopics = topics.filter((topic) => topic.canDelete)
  const selectedTopics = deletableTopics.filter((topic) => selectedKeys.has(topic.topicKey))
  const allPageSelected = pageDeletableTopics.length > 0
    && pageDeletableTopics.every((topic) => selectedKeys.has(topic.topicKey))
  const hasNextPage = pageIndex + 1 < topicPages.length || Boolean(pageCursors[pageIndex])

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setTopicPages([])
    setPageCursors([])
    setPageIndex(0)
    setSelectedKeys(new Set())
    setSelectionMode(false)
    api.topics().then((page) => {
      if (!active) return
      setTopicPages([page.items])
      setPageCursors([page.nextCursor ?? null])
    }).catch((problem) => {
      if (active) setError(messageForError(problem))
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [reload])

  async function goToNextPage() {
    if (loadingPage || deleting) return
    if (pageIndex + 1 < topicPages.length) {
      setPageIndex((current) => current + 1)
      return
    }
    const cursor = pageCursors[pageIndex]
    if (!cursor) return
    setLoadingPage(true)
    setError('')
    try {
      const page = await api.topics(cursor)
      setTopicPages((previous) => [...previous, page.items])
      setPageCursors((previous) => [...previous, page.nextCursor ?? null])
      setPageIndex((current) => current + 1)
    } catch (problem) {
      setError(messageForError(problem))
    } finally {
      setLoadingPage(false)
    }
  }

  function goToPreviousPage() {
    if (!deleting && pageIndex > 0) setPageIndex((current) => current - 1)
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
    const pageKeys = pageDeletableTopics.map((topic) => topic.topicKey)
    setSelectedKeys((previous) => {
      const next = new Set(previous)
      pageKeys.forEach((key) => allPageSelected ? next.delete(key) : next.add(key))
      return next
    })
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
      const topicsToDelete = [...selectedTopics]
      const results = await Promise.allSettled(topicsToDelete.map((topic) => api.deleteTopic(topic.topicKey)))
      const succeededKeys = new Set(topicsToDelete.filter((_, index) => results[index].status === 'fulfilled')
        .map((topic) => topic.topicKey))
      const failedKeys = new Set(topicsToDelete.filter((_, index) => results[index].status === 'rejected')
        .map((topic) => topic.topicKey))
      const nextPages = topicPages.map((page) => page.filter((topic) => !succeededKeys.has(topic.topicKey)))
      setTopicPages(nextPages)
      if (!nextPages[pageIndex]?.length) {
        const nearestPage = nextPages.findIndex((page) => page.length > 0)
        setPageIndex(nearestPage < 0 ? 0 : nearestPage)
      }
      setSelectedKeys(failedKeys)
      setConfirmDeleteOpen(false)
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

  function redoSelectedTopics() {
    if (!selectedTopics.length || deleting) return
    if (selectedTopics.length === 1) onRedo(selectedTopics[0])
    else setRedoPickerOpen(true)
  }

  return <>
    {!loading && deletableTopics.length > 0 && <View className='library-intro'>
      {selectionMode ? <View className='topic-selection-toolbar'>
        <Button className='topic-selection-action cancel-action ui-button' hoverClass='row-press' disabled={deleting}
          onClick={toggleSelectionMode}>取消选择</Button>
        <Button className={`topic-selection-action select-all ui-button${allPageSelected ? ' selected' : ''}`} hoverClass='row-press' disabled={!pageDeletableTopics.length || deleting}
          onClick={toggleSelectAll}>
          <View className='topic-selection-check' aria-hidden>{allPageSelected ? '✓' : ''}</View>
          {allPageSelected ? '取消全选' : '全选'}
        </Button>
        <Button className='topic-selection-action delete-action ui-button' hoverClass='row-press'
          disabled={!selectedTopics.length || deleting} onClick={() => setConfirmDeleteOpen(true)}>删除</Button>
        <Button className='topic-selection-action redo-action ui-button' hoverClass='row-press'
          disabled={!selectedTopics.length || deleting} onClick={redoSelectedTopics}>重新做题</Button>
      </View> : <Button className='topic-selection-toggle ui-button'
        hoverClass='row-press' onClick={toggleSelectionMode}>选择</Button>}
    </View>}
    {error && <ErrorNote>{error}</ErrorNote>}
    {loading ? <StatusPanel title='正在加载题库…' />
      : topics.length === 0 && allLoadedTopics.length === 0 && !pageCursors.some(Boolean) ? <StatusPanel title='题库空空的'>
      <View className='empty-actions'>
        <Action onClick={onHome}>去学习</Action>
      </View>
    </StatusPanel> : <View className='topic-library-list'>
      {topics.map((topic) => <View className='topic-library-row' key={topic.topicKey}>
        <Button className={`topic ui-button topic-library-card user-topic${selectedKeys.has(topic.topicKey) ? ' is-selected' : ''}`}
          hoverClass='press-offset' onClick={() => selectionMode ? toggleSelection(topic) : onTopic(topic)}>
          {selectionMode && topic.canDelete && <Text className={`topic-select-mark${selectedKeys.has(topic.topicKey) ? ' checked' : ''}`} aria-hidden>
            {selectedKeys.has(topic.topicKey) ? '✓' : ''}
          </Text>}
          <View className='topic-library-copy'>
            <Text className='topic-title'>{topic.title}</Text>
            {topic.attemptId && typeof topic.answeredCount === 'number'
              && typeof topic.totalQuestions === 'number'
              && <Text className='topic-progress'>{topic.attemptStatus === 'PASSED' ? '已完成' : '已答'} {topic.answeredCount}/{topic.totalQuestions} 题</Text>}
            {topic.originalInput && topic.originalInput.trim() !== topic.title.trim()
              && <Text className='topic-original-input'>原始提问：{topic.originalInput}</Text>}
          </View>
          <Text className='mistake-topic-arrow' aria-hidden>›</Text>
        </Button>
      </View>)}
      {(pageIndex > 0 || hasNextPage) && <View className='topic-pagination'>
        <Button className='topic-page-button ui-button' hoverClass='row-press' disabled={pageIndex === 0 || loadingPage || deleting}
          onClick={goToPreviousPage}>上一页</Button>
        <Text className='topic-page-index'>第 {pageIndex + 1} 页</Text>
        <Button className='topic-page-button ui-button' hoverClass='row-press' disabled={!hasNextPage || loadingPage || deleting}
          onClick={() => { void goToNextPage() }}>{loadingPage ? '加载中…' : '下一页'}</Button>
      </View>}
    </View>}
    {error && !loading && <Action secondary onClick={() => setReload((value) => value + 1)}>重新加载</Action>}
    {redoPickerOpen && <Sheet title='重新做题' onClose={() => setRedoPickerOpen(false)}>
      <View className='topic-redo-list'>
        {selectedTopics.map((topic) => <ComicRow key={topic.topicKey} onClick={() => {
          setRedoPickerOpen(false)
          onRedo(topic)
        }}>{topic.title}</ComicRow>)}
      </View>
    </Sheet>}
    {confirmDeleteOpen && <Sheet title={`删除 ${selectedTopics.length} 个主题？`} onClose={() => { if (!deleting) setConfirmDeleteOpen(false) }}>
      <Text className='body-copy'>选中的主题将从题库移除，已有学习记录会保留。</Text>
      <View className='topic-delete-actions'>
        <Action secondary disabled={deleting} onClick={() => setConfirmDeleteOpen(false)}>先保留</Action>
        <Action disabled={deleting} onClick={() => { void deleteSelectedTopics() }}>{deleting ? '正在删除…' : `确认删除 ${selectedTopics.length} 个`}</Action>
      </View>
    </Sheet>}
  </>
}
