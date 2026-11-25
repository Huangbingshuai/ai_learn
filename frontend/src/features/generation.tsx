import { Text, View } from '@tarojs/components'
import { useEffect, useRef, useState } from 'react'
import { api, type RestartGroup } from '../api/client'
import { attemptNeedsRegeneration, messageForError, RequestError } from '../api/request'
import type { Attempt, GenerationJob, QuizGroup } from '../api/types'
import { Action, Tape } from '../components/comic'
import { FIRST_POLL_DELAY, generationStep, makeKey, nextPollDelay } from './flow'

export type GenerationIntent =
  | { kind: 'generate'; topic: string; key: string }
  | { kind: 'fixed'; key: string }
  | { kind: 'restart'; group: RestartGroup; key: string }

type Failure = {
  title: string
  description: string
  retry: 'same' | 'new' | 'none'
  requestId?: string | null
}

function jobFailure(job: GenerationJob): Failure {
  const issue = job.error
  const retry = issue?.retryable ? 'new' : 'none'
  if (issue?.code === 'QUIZ_EXPIRED') {
    return { title: '这组练习已到期', description: issue.message, retry, requestId: issue.requestId }
  }
  if (issue?.code === 'SOURCE_INSUFFICIENT') {
    return { title: '这个主题的依据不足', description: '现有资料还支撑不了一组完整练习。请选择另一个主题。', retry, requestId: issue.requestId }
  }
  if (issue?.code === 'GENERATION_QUALITY_FAILED') {
    return { title: '这组题没有通过核验', description: '这组题暂时无法用于练习。你可以重新生成，或换个主题。', retry, requestId: issue.requestId }
  }
  return {
    title: '题目暂时没生成',
    description: issue?.message || '请稍后再试，或从首页选择另外一个主题。',
    retry,
    requestId: issue?.requestId,
  }
}

export function GenerationScreen({ intent, visible, onCancel, onOpen, onInvalidTopic, onStartAgain }: {
  intent: GenerationIntent
  visible: boolean
  onCancel: () => void
  onOpen: (attempt: Attempt) => void
  onInvalidTopic: (message: string) => void
  onStartAgain: () => void
}) {
  const [failure, setFailure] = useState<Failure>()
  const [phase, setPhase] = useState<GenerationJob['phase']>()
  const [title, setTitle] = useState(intent.kind === 'generate' ? intent.topic : intent.kind === 'restart' ? intent.group.topicTitle : '固定题组')
  const [retryTrigger, setRetryTrigger] = useState(0)
  const job = useRef<GenerationJob>()
  const group = useRef<QuizGroup>()
  const [attemptKey] = useState(makeKey)
  const callbacks = useRef({ onOpen, onInvalidTopic })
  callbacks.current = { onOpen, onInvalidTopic }

  useEffect(() => {
    if (!visible || failure) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined

    async function run(delay: number) {
      try {
        if (!group.current) {
          if (intent.kind === 'restart') group.current = intent.group
          else if (intent.kind === 'fixed') {
            if (!api.fixedQuiz) throw new RequestError('CONFIGURATION', '开发固定题组未开启。', 0)
            const fixed = await api.fixedQuiz(intent.key)
            if (!active) return
            group.current = fixed
          }
          else {
            let result = job.current
            if (!result) result = await api.generate(intent.topic, intent.key)
            else if (result.status === 'QUEUED' || result.status === 'RUNNING') {
              result = await api.job(result.jobId)
            }
            if (!active) return
            job.current = result
            setTitle(result.topicTitle)
            setPhase(result.phase)
            if (result.status === 'FAILED') {
              setFailure(jobFailure(result))
              return
            }
            if (result.status !== 'SUCCEEDED') {
              timer = setTimeout(() => { void run(nextPollDelay(delay)) }, delay)
              return
            }
            if (!result.quizId || !result.quizExpiresAt) {
              throw new RequestError('MISSING_QUIZ', '练习暂时无法打开，请重新生成。', 0)
            }
            group.current = { quizId: result.quizId, quizExpiresAt: result.quizExpiresAt }
          }
        }

        if (!active) return
        const attempt = await api.startAttempt(group.current.quizId, attemptKey)
        if (active) callbacks.current.onOpen(attempt)
      } catch (problem) {
        if (!active) return
        const requestError = problem instanceof RequestError ? problem : undefined
        if (requestError?.code === 'TOPIC_UNSUPPORTED' || requestError?.code === 'INVALID_INPUT') {
          callbacks.current.onInvalidTopic(messageForError(problem))
          return
        }
        const unavailable = group.current && attemptNeedsRegeneration(problem)
        const insufficient = requestError?.code === 'SOURCE_INSUFFICIENT'
        setFailure({
          title: unavailable ? '这组练习已不可用' : insufficient ? '这个主题的依据不足' : '题目暂时没准备好',
          description: unavailable ? '可以重新生成一组练习。' : messageForError(problem),
          retry: unavailable || requestError?.code === 'MISSING_QUIZ' ? 'new'
            : (requestError?.retryable || !requestError ? 'same' : 'none'),
          requestId: requestError?.requestId,
        })
      }
    }

    void run(FIRST_POLL_DELAY)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [attemptKey, failure, intent, retryTrigger, visible])

  const restart = intent.kind === 'restart'
  const step = generationStep(phase)
  return <View className='stage'>
    <Tape>{title}</Tape>
    <View className={`manifest${failure ? ' is-error' : ''}`}>
      <View className='manifest-art' aria-hidden />
      <View className='manifest-title'>
        {failure ? <Text>{failure.title}</Text> : <>
          <Text>{restart ? '正在打开' : '正在准备'}</Text>
          <Text className='manifest-emphasis'>{restart ? '这组练习' : '你的 20 道题'}</Text>
        </>}
      </View>
      <View className='spot' aria-hidden />
    </View>
    {failure ? <View>
      <Text className='stage-note'>{failure.description}</Text>
      {failure.requestId && <Text className='stage-note'>请求编号 {failure.requestId}</Text>}
    </View> : !restart && <>
      <View className='step-list'>
        {['读取主题资料', '生成并核对题目'].map((label, index) => {
          const status = index < step ? 'complete' : index === step ? 'current' : 'queued'
          return <View className={`step ${status}`} key={label}>
            <Text>{label}</Text>
            <Text className='stage-note'>{status === 'complete' ? '已完成' : status === 'current' ? '进行中' : '等待中'}</Text>
          </View>
        })}
      </View>
      <Text className='stage-note'>题组就绪后即可开始练习。</Text>
    </>}
    <View className='actions'>
      {failure?.retry === 'same' && <Action onClick={() => {
        setFailure(undefined)
        setRetryTrigger((version) => version + 1)
      }}>重试连接</Action>}
      {failure?.retry === 'new' && <Action onClick={onStartAgain}>重新生成</Action>}
      <Action secondary onClick={onCancel}>{failure ? '换个主题' : '返回选主题'}</Action>
    </View>
  </View>
}
