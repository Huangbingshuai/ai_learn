import { Button, Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { messageForError } from '../api/request'
import type { Topic } from '../api/types'
import { Action, ErrorNote, Sheet, StatusPanel } from '../components/comic'

export function QuestionBankScreen({ onTopic, onFixedQuiz }: {
  onTopic: (topic: Topic) => void
  onFixedQuiz: () => void
}) {
  const [topics, setTopics] = useState<Topic[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [deleteTarget, setDeleteTarget] = useState<Topic>()
  const [deleting, setDeleting] = useState(false)

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

  return <>
    <View className='library-intro'>
      <Text className='h1'>我的题库</Text>
      <Text className='body-copy'>保存过的主题都在这里，点开即可继续学习。</Text>
    </View>
    {error && <ErrorNote>{error}</ErrorNote>}
    {loading ? <StatusPanel title='正在加载题库…' /> : topics.length === 0 ? <StatusPanel title='题库还是空的'>
      <Text className='body-copy'>回到“学习”输入感兴趣的内容，生成练习后会自动收进题库。</Text>
    </StatusPanel> : <View className='topic-library-list'>
      {topics.map((topic) => <View className='topic-library-row' key={topic.topicKey}>
        <Button className={`topic ui-button topic-library-card ${topic.art === 'topic' ? 'saved-topic' : topic.art}`}
          hoverClass='press-offset' onClick={() => onTopic(topic)}>
          <View className='topic-symbol' aria-hidden />
          <View className='topic-library-copy'>
            <Text className='topic-title'>{topic.title}</Text>
            <Text className='topic-library-hint'>{topic.canDelete ? '我的主题 · 点击开始练习' : '推荐主题 · 点击开始练习'}</Text>
          </View>
          <Text className='topic-arrow' aria-hidden>›</Text>
        </Button>
        {topic.canDelete && <Button className='topic-delete-visible ui-button' hoverClass='row-press'
          aria-label={`删除主题${topic.title}`} onClick={() => setDeleteTarget(topic)}>
          <Text aria-hidden>×</Text><Text>删除</Text>
        </Button>}
      </View>)}
      {cursor && <View className='topic-load-more'>
        <Action secondary disabled={loadingMore} onClick={() => { void loadMore() }}>
          {loadingMore ? '正在加载…' : '加载更多主题'}
        </Action>
      </View>}
    </View>}
    {error && !loading && <Action secondary onClick={() => setReload((value) => value + 1)}>重新加载</Action>}
    {__QINGZHI_DEV_FIXED_QUIZ__ && <View className='fixed-quiz-card'>
      <Text className='h3'>快速体验</Text>
      <Text className='body-copy'>用内置示例题组体验答题和金币结算。</Text>
      <Action secondary onClick={onFixedQuiz}>体验示例题组</Action>
    </View>}
    {deleteTarget && <Sheet title='确认删除主题？' onClose={() => setDeleteTarget(undefined)}>
      <Text className='body-copy'>「{deleteTarget.title}」会从你的题库移除，已有学习记录仍会保留。</Text>
      <View className='topic-delete-actions'>
        <Action secondary disabled={deleting} onClick={() => setDeleteTarget(undefined)}>先保留</Action>
        <Action disabled={deleting} onClick={() => { void deleteTopic() }}>{deleting ? '正在删除…' : '确认删除'}</Action>
      </View>
    </Sheet>}
  </>
}
