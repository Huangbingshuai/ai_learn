import { Button, Image, Input, Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'
import { api, type PendingGeneration, type RestartGroup } from '../api/client'
import { messageForError } from '../api/request'
import type { Topic } from '../api/types'
import cover from '../assets/cover.webp'
import { Action, ErrorNote } from '../components/comic'
import { generationPhases, RESTART_CUTOFF_MS, restartAvailable, topicInputError, topicLength } from './flow'

export function HomeScreen({
  input, error, restart, pending, onEdit, onError, onGenerate, onTopic, onPending, onFixedQuiz, onRestart,
}: {
  input: string
  error: string
  restart?: RestartGroup
  pending: PendingGeneration[]
  onEdit: (input: string) => void
  onError: (message: string) => void
  onGenerate: (topic: string) => void
  onTopic: (topic: Topic) => void
  onPending: (intent: PendingGeneration) => void
  onFixedQuiz: () => void
  onRestart: (group: RestartGroup) => void
}) {
  const [topics, setTopics] = useState<Topic[]>()
  const [topicLoadError, setTopicLoadError] = useState('')
  const [nextCursor, setNextCursor] = useState<string>()
  const [loadingMore, setLoadingMore] = useState(false)
  const [reload, setReload] = useState(0)
  const [now, setNow] = useState(Date.now)
  const canRestart = restart && restartAvailable(restart.quizExpiresAt, now)

  useEffect(() => {
    let active = true
    setTopicLoadError('')
    api.topics().then(({ items, nextCursor }) => {
      if (!active) return
      setTopics(items)
      setNextCursor(nextCursor ?? undefined)
    }).catch((problem: unknown) => {
      if (active) setTopicLoadError(messageForError(problem))
    })
    return () => { active = false }
  }, [reload])

  useEffect(() => {
    if (!restart) return
    const wait = Date.parse(restart.quizExpiresAt) - Date.now() - RESTART_CUTOFF_MS
    if (wait <= 0) return
    const timer = setTimeout(() => setNow(Date.now()), wait)
    return () => clearTimeout(timer)
  }, [restart])

  function submitTopic() {
    const problem = topicInputError(input)
    if (problem) onError(problem)
    else onGenerate(input.trim())
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return
    setLoadingMore(true)
    setTopicLoadError('')
    try {
      const result = await api.topics(nextCursor)
      setTopics((previous) => {
        const shown = new Set(previous?.map((entry) => entry.topicKey))
        return [...(previous ?? []), ...result.items.filter((entry) => !shown.has(entry.topicKey))]
      })
      setNextCursor(result.nextCursor ?? undefined)
    } catch (problem) {
      setTopicLoadError(messageForError(problem))
    } finally {
      setLoadingMore(false)
    }
  }

  return <>
    <View className='comic-cover'>
      <Image className='cover-art' src={cover} mode='widthFix' aria-label='翻开的书里跳出一颗地球，戴眼镜的学生在旁边发问' />
      <View className='cover-title'>
        <Text>今天想弄懂</Text>
        <Text className='cover-emphasis'>什么？</Text>
      </View>
      <View className='cover-dialogue'>
        <Text>地球忘带</Text>
        <Text>校服啦？</Text>
      </View>
    </View>
    <Text className='home-caption'>一组 20 道题，每题都有讲解</Text>
    <View className='topic-form'>
      <View className='topic-field'>
        <Input
          className={`topic-input${error ? ' invalid' : ''}`}
          type='text'
          value={input}
          maxlength={-1}
          confirmType='go'
          placeholder='输入主题，例如「为什么会有四季」'
          placeholderStyle='color: #60566c'
          aria-label='学习主题'
          onInput={(event) => onEdit(event.detail.value)}
          onConfirm={submitTopic}
        />
        <Text className='char-count'>{topicLength(input)} / 80</Text>
      </View>
      {error && <ErrorNote>{error}</ErrorNote>}
      <Action onClick={submitTopic}>生成练习</Action>
    </View>
    {(pending.length > 0 || restart) && <View className='home-continue'>
      {pending.map((intent) => <Button
        className='continue-row ui-button'
        hoverClass='press-offset'
        key={intent.key}
        onClick={() => onPending(intent)}
      >
        <View className='continue-copy'>
          <Text className='continue-title'>{intent.title}</Text>
          <Text className='continue-meta'>
            {intent.status === 'SUCCEEDED'
              ? '已答 0/20 题'
              : intent.phase ? generationPhases[intent.phase] : '正在出题'}
          </Text>
        </View>
        <Text className='continue-arrow' aria-hidden>›</Text>
      </Button>)}
      {restart && <Button
        className='continue-row is-restart ui-button'
        hoverClass='press-offset'
        onClick={() => onRestart(restart)}
      >
        <View className='continue-copy'>
          <Text className='continue-title'>{restart.topicTitle}</Text>
          <Text className='continue-meta'>
            {!canRestart ? '练习已到期' : restart.attemptId && typeof restart.answeredCount === 'number' && typeof restart.totalQuestions === 'number'
              ? `已答 ${restart.answeredCount}/${restart.totalQuestions} 题` : '点击开始练习'}
          </Text>
        </View>
        <Text className='continue-arrow' aria-hidden>›</Text>
      </Button>}
    </View>}
    <View className='topic-picker'>
      <Text className='h2'>已保存主题</Text>
      {topics && <View className='topic-options'>
        {topics.map((topic) => <Button
          className={`topic ui-button ${topic.art === 'topic' ? 'saved-topic' : topic.art}`}
          key={topic.topicKey}
          hoverClass='press-offset'
          onClick={() => onTopic(topic)}
        >
          <View className='topic-symbol' aria-hidden />
          <Text className='topic-title'>{topic.title}</Text><Text className='topic-arrow' aria-hidden>›</Text>
        </Button>)}
      </View>}
      {!topics && (topicLoadError ? <View className='topic-load-error'>
        <ErrorNote>{topicLoadError}</ErrorNote>
        <Action secondary onClick={() => setReload((version) => version + 1)}>重新加载主题</Action>
      </View> : <Text className='stage-note'>正在加载主题…</Text>)}
      {topics?.length === 0 && <Text className='stage-note'>出题成功后，主题会出现在这里。</Text>}
      {topics && topicLoadError && <ErrorNote>{topicLoadError}</ErrorNote>}
      {nextCursor && <View className='topic-load-more'>
        <Action secondary disabled={loadingMore} onClick={() => { void loadMore() }}>
          {loadingMore ? '正在加载…' : '查看更多主题'}
        </Action>
      </View>}
    </View>
    {__QINGZHI_DEV_FIXED_QUIZ__ && <Action secondary onClick={onFixedQuiz}>打开开发固定题组</Action>}
  </>
}
