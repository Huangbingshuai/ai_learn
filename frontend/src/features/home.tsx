import { Button, Image, Input, Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api } from '../api/client'
import cover from '../assets/cover.webp'
import { Action, ErrorNote } from '../components/comic'
import { topicInputError } from './flow'
import { topicIdeas } from './topic-ideas'

export function HomeScreen({
  input, error, generating, generationProgress, currentAttemptId, onEdit, onError, onGenerate, onNavigate,
}: {
  input: string
  error: string
  generating: boolean
  generationProgress?: ReactNode
  currentAttemptId?: string
  onEdit: (input: string) => void
  onError: (message: string) => void
  onGenerate: (topic: string) => void
  onNavigate: (destination: 'questionBank' | 'mistakeBook' | 'favorites' | 'answer') => void
}) {
  const [ideaPage, setIdeaPage] = useState(0)
  const [topicCount, setTopicCount] = useState<string>()
  const [pendingMistakes, setPendingMistakes] = useState<number>()
  const [favoriteCount, setFavoriteCount] = useState<number>()
  const [answerInfo, setAnswerInfo] = useState<{ title: string; progress: string }>()

  useEffect(() => {
    let active = true
    api.topics().then((page) => {
      if (active) setTopicCount(`${page.items.length}${page.nextCursor ? '+' : ''}`)
    }).catch(() => {})
    api.mistakes().then((page) => {
      if (active) setPendingMistakes(page.pendingCount)
    }).catch(() => {})
    api.favorites().then((page) => {
      if (active) setFavoriteCount(page.items.length)
    }).catch(() => {})
    async function loadAnswerProgress() {
      const current = currentAttemptId ? await api.attempt(currentAttemptId).catch(() => undefined) : undefined
      const attempt = current?.purpose === 'LEARNING' && current.status === 'ACTIVE' ? current
        : (await api.activeAttempts()).items.find((item) => item.purpose === 'LEARNING')
      if (active) setAnswerInfo(attempt ? { title: attempt.topicTitle, progress: `${attempt.answeredCount}/${attempt.totalQuestions}` } : undefined)
    }
    void loadAnswerProgress().catch(() => {})
    return () => { active = false }
  }, [currentAttemptId])

  function submitTopic() {
    if (generating) return
    const problem = topicInputError(input)
    if (problem) onError(problem)
    else onGenerate(input.trim())
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
    <View className='topic-form'>
      {generationProgress}
      <View className='topic-field'>
        <Input className={`topic-input${error ? ' invalid' : ''}`} type='text' value={input} maxlength={-1}
          confirmType='go' placeholder='输入你想弄懂的问题'
          placeholderStyle='color: #60566c' aria-label='学习主题' disabled={generating}
          onInput={(event) => { if (!generating) onEdit(event.detail.value) }} onConfirm={submitTopic} />
      </View>
      {error && <ErrorNote>{error}</ErrorNote>}
      {!generating && <Action onClick={submitTopic}>生成练习</Action>}
    </View>
    <View className='home-ideas'>
      <View className='home-ideas-head'>
        <Text className='home-ideas-title'>还没想好学什么？</Text>
        <Button className='home-ideas-refresh ui-button' hoverClass='none' disabled={generating}
          onClick={() => setIdeaPage((page) => (page + 1) % topicIdeas.length)}>换一组 ↻</Button>
      </View>
      <View className='home-idea-list'>
        {topicIdeas[ideaPage].map((idea, index) => <Button key={idea} disabled={generating} className={`home-idea ui-button idea-${index}`}
          hoverClass='row-press' onClick={() => { if (!generating) onEdit(idea) }}>{idea}<Text>＋</Text></Button>)}
      </View>
    </View>
    <View className='home-shortcuts'>
      <Button className='home-shortcut home-shortcut-library ui-button' hoverClass='press-offset' onClick={() => onNavigate('questionBank')}>
        <View className='home-shortcut-heading'><Text className='home-shortcut-label'>题库</Text><Text className='home-shortcut-arrow'>›</Text></View>
        <View className='home-shortcut-meta'><Text className='home-shortcut-count'>{topicCount ?? '–'}</Text><Text className='home-shortcut-unit'>组主题</Text></View>
      </Button>
      <Button className='home-shortcut home-shortcut-mistakes ui-button' hoverClass='press-offset' onClick={() => onNavigate('mistakeBook')}>
        <View className='home-shortcut-heading'><Text className='home-shortcut-label'>错题本</Text><Text className='home-shortcut-arrow'>›</Text></View>
        <View className='home-shortcut-meta'><Text className='home-shortcut-count'>{pendingMistakes ?? '–'}</Text><Text className='home-shortcut-unit'>道错题</Text></View>
      </Button>
      <Button className='home-shortcut home-shortcut-favorites ui-button' hoverClass='press-offset' onClick={() => onNavigate('favorites')}>
        <View className='home-shortcut-heading'><Text className='home-shortcut-label'>收藏夹</Text><Text className='home-shortcut-arrow'>›</Text></View>
        <View className='home-shortcut-meta'><Text className='home-shortcut-count'>{favoriteCount ?? '–'}</Text><Text className='home-shortcut-unit'>道收藏</Text></View>
      </Button>
      <Button className='home-shortcut home-shortcut-answer ui-button' hoverClass='press-offset' onClick={() => onNavigate('answer')}>
        <View className='home-shortcut-heading'><Text className='home-shortcut-label'>答题</Text><Text className='home-shortcut-arrow'>›</Text></View>
        {answerInfo && <View className='home-shortcut-meta home-shortcut-answer-meta'><Text className='home-shortcut-answer-progress'>{answerInfo.title}</Text><Text className='home-shortcut-answer-count'>{answerInfo.progress}</Text></View>}
      </Button>
    </View>
  </>
}
