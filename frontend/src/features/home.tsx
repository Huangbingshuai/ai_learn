import { Button, Image, Input, Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'
import type { PendingGeneration, RestartGroup } from '../api/client'
import cover from '../assets/cover.webp'
import { Action, ErrorNote } from '../components/comic'
import { generationPhases, RESTART_CUTOFF_MS, restartAvailable, topicInputError, topicLength } from './flow'

const topicIdeas = [
  ['为什么会有彩虹？', '恐龙为什么会灭绝？', '月亮为什么会变圆？'],
  ['猫咪为什么会打呼噜？', '飞机怎么飞上天？', '海水为什么是咸的？'],
]

export function HomeScreen({
  input, error, restart, pending, onEdit, onError, onGenerate, onPending, onRestart,
}: {
  input: string
  error: string
  restart?: RestartGroup
  pending: PendingGeneration[]
  onEdit: (input: string) => void
  onError: (message: string) => void
  onGenerate: (topic: string) => void
  onPending: (intent: PendingGeneration) => void
  onRestart: (group: RestartGroup) => void
}) {
  const [now, setNow] = useState(Date.now)
  const [ideaPage, setIdeaPage] = useState(0)
  const canRestart = restart && restartAvailable(restart.quizExpiresAt, now)

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
    <Text className='home-caption'>一组 12 道题，每题都有讲解</Text>
    <View className='topic-form'>
      <View className='topic-field'>
        <Input className={`topic-input${error ? ' invalid' : ''}`} type='text' value={input} maxlength={-1}
          confirmType='go' placeholder='输入主题，例如「为什么会有四季」'
          placeholderStyle='color: #60566c' aria-label='学习主题'
          onInput={(event) => onEdit(event.detail.value)} onConfirm={submitTopic} />
        <Text className='char-count'>{topicLength(input)} / 80</Text>
      </View>
      {error && <ErrorNote>{error}</ErrorNote>}
      <Action onClick={submitTopic}>生成练习</Action>
    </View>
    <View className='home-ideas'>
      <View className='home-ideas-head'>
        <Text className='home-ideas-title'>还没想好学什么？</Text>
        <Button className='home-ideas-refresh ui-button' hoverClass='none'
          onClick={() => setIdeaPage((page) => (page + 1) % topicIdeas.length)}>换一组 ↻</Button>
      </View>
      <View className='home-idea-list'>
        {topicIdeas[ideaPage].map((idea, index) => <Button key={idea} className={`home-idea ui-button idea-${index}`}
          hoverClass='row-press' onClick={() => onEdit(idea)}>{idea}<Text>＋</Text></Button>)}
      </View>
    </View>
    {(pending.length > 0 || restart) && <View className='home-continue'>
      {pending.map((intent) => <View className='continue-row-shell' key={intent.key}>
        <View className='continue-copy'>
          <Text className='continue-title'>{intent.title}</Text>
          <Text className='continue-meta'>{intent.status === 'SUCCEEDED'
            ? '已生成 · 开始答题' : intent.phase ? generationPhases[intent.phase] : '正在出题'}</Text>
        </View>
        <Action onClick={() => onPending(intent)}>继续</Action>
      </View>)}
      {restart && <View className='continue-row-shell'>
        <View className='continue-copy'>
          <Text className='continue-title'>{restart.topicTitle}</Text>
          <Text className='continue-meta'>{!canRestart ? '练习已到期'
            : restart.attemptId && typeof restart.answeredCount === 'number' && typeof restart.totalQuestions === 'number'
              ? `已答 ${restart.answeredCount}/${restart.totalQuestions} 题` : '尚未开始答题'}</Text>
        </View>
        <Action onClick={() => onRestart(restart)}>{canRestart ? '继续练习' : '重新出题'}</Action>
      </View>}
    </View>}
  </>
}
