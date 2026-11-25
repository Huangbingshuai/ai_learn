import { Text, View } from '@tarojs/components'
import { useEffect, useRef, useState } from 'react'
import { api, type PendingGeneration, type RestartGroup } from '../api/client'
import { attemptNeedsRegeneration, messageForError, RequestError } from '../api/request'
import type { Attempt, GenerationJob, QuizGroup } from '../api/types'
import { Action, Tape } from '../components/comic'
import { FIRST_POLL_DELAY, formatElapsed, generationPhases, nextPollDelay } from './flow'

export type GenerationIntent =
  | PendingGeneration
  | { kind: 'fixed'; key: string }
  | { kind: 'saved'; topicKey: string; title: string; key: string }
  | { kind: 'restart'; group: RestartGroup; key: string }
  | { kind: 'resume'; group: RestartGroup; key: string }

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
    return { title: '这个主题的依据不足', description: '找到的资料还支撑不了 20 道有依据的题。可以缩小范围或换个主题。', retry, requestId: issue.requestId }
  }
  if (issue?.code === 'TOPIC_NEEDS_CLARIFICATION') {
    return { title: '想了解哪一部分？', description: issue.message, retry, requestId: issue.requestId }
  }
  if (issue?.code === 'SEARCH_UNAVAILABLE') {
    return { title: '资料检索暂时不可用', description: issue.message, retry, requestId: issue.requestId }
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

export function GenerationScreen({ intent, visible, onCancel, onOpen, onInvalidTopic, onStartAgain, onJobId, onFailed }: {
  intent: GenerationIntent
  visible: boolean
  onCancel: (finished: boolean) => void
  onOpen: (attempt: Attempt) => void
  onInvalidTopic: (message: string) => void
  onStartAgain: () => void
  onJobId: (id: string) => void
  onFailed: () => void
}) {
  const [failure, setFailure] = useState<Failure>()
  const [status, setStatus] = useState<GenerationJob>()
  const [now, setNow] = useState(Date.now)
  const [connecting, setConnecting] = useState(false)
  const [retryTrigger, setRetryTrigger] = useState(0)
  const job = useRef<GenerationJob>()
  const jobId = useRef(intent.kind === 'generate' ? intent.jobId : undefined)
  const group = useRef<QuizGroup>()
  const attemptKey = `${intent.key}-attempt`
  const callbacks = useRef({ onOpen, onInvalidTopic, onJobId, onFailed })
  callbacks.current = { onOpen, onInvalidTopic, onJobId, onFailed }

  useEffect(() => {
    if (!visible || failure || intent.kind !== 'generate') return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [failure, intent.kind, visible])

  useEffect(() => {
    if (!visible || failure) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined

    async function run(delay: number) {
      try {
        if (intent.kind === 'resume') {
          const attempt = await api.attempt(intent.group.attemptId!)
          if (active) callbacks.current.onOpen(attempt)
          return
        }
        if (!group.current) {
          if (intent.kind === 'restart') group.current = intent.group
          else if (intent.kind === 'saved') group.current = await api.savedQuiz(intent.topicKey)
          else if (intent.kind === 'fixed') {
            if (!api.fixedQuiz) throw new RequestError('CONFIGURATION', '开发固定题组未开启。', 0)
            const fixed = await api.fixedQuiz(intent.key)
            if (!active) return
            group.current = fixed
          }
          else {
            let result = job.current
            if (!result || result.status === 'QUEUED' || result.status === 'RUNNING') {
              result = jobId.current
                ? await api.job(jobId.current)
                : await api.generate(intent.input, intent.key)
            }
            if (!active) return
            if (!jobId.current) {
              jobId.current = result.jobId
              callbacks.current.onJobId(result.jobId)
            }
            job.current = result
            setStatus(result)
            setConnecting(false)
            if (result.status === 'FAILED') {
              callbacks.current.onFailed()
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
        if (!group.current && jobId.current && requestError?.retryable) {
          setConnecting(true)
          timer = setTimeout(() => { void run(nextPollDelay(delay)) }, delay)
          return
        }
        if (requestError?.code === 'INVALID_INPUT') {
          callbacks.current.onInvalidTopic(messageForError(problem))
          return
        }
        const unavailable = group.current && attemptNeedsRegeneration(problem)
        const insufficient = requestError?.code === 'SOURCE_INSUFFICIENT'
        if (unavailable || insufficient || requestError?.code === 'MISSING_QUIZ'
          || requestError?.status === 404 || (requestError && !requestError.retryable)) {
          callbacks.current.onFailed()
        }
        setFailure({
          title: unavailable ? '这组练习已不可用' : insufficient ? '这个主题的依据不足' : '题目暂时没准备好',
          description: unavailable ? '可以重新生成一组练习。' : messageForError(problem),
          retry: unavailable || requestError?.code === 'MISSING_QUIZ' || requestError?.status === 404 ? 'new'
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

  const live = intent.kind === 'generate'
  const title = live ? status?.topicTitle || intent.title : intent.kind === 'restart' || intent.kind === 'resume' ? intent.group.topicTitle : intent.kind === 'saved' ? intent.title : '固定题组'
  const createdAt = status?.createdAt ? Date.parse(status.createdAt) : NaN
  const startedAt = Number.isFinite(createdAt) ? createdAt : live ? intent.requestedAt : now
  const phase = status?.status === 'SUCCEEDED' ? '正在打开练习'
    : status?.phase ? generationPhases[status.phase]
      : status ? '等待开始' : jobId.current ? '读取出题进度' : '正在提交主题'
  return <View className='stage'>
    <Tape>{title}</Tape>
    <View className={`manifest${failure ? ' is-error' : ''}`}>
      <View className='manifest-art' aria-hidden />
      <View className='manifest-title'>
        {failure ? <Text>{failure.title}</Text> : <>
          <Text>{live ? '正在准备' : '正在打开'}</Text>
          <Text className='manifest-emphasis'>{live ? '你的 20 道题' : '这组练习'}</Text>
        </>}
      </View>
      <View className='spot' aria-hidden />
    </View>
    {failure ? <View>
      <Text className='stage-note'>{failure.description}</Text>
      {failure.requestId && <Text className='stage-note'>请求编号 {failure.requestId}</Text>}
    </View> : live && <>
      <View className='step current'>
        <Text>{phase}</Text>
        <Text className='generation-clock'>已等 {formatElapsed(startedAt, now)}</Text>
      </View>
      <Text className='stage-note'>{connecting
        ? '正在重新连接，作业会在后台继续。'
        : now - startedAt >= 30_000
          ? '查资料和逐题核对有时会久一点。可以先回首页，稍后接着看。'
          : '题组通过核验后会自动保存。'}</Text>
    </>}
    <View className='actions'>
      {failure?.retry === 'same' && <Action onClick={() => {
        setFailure(undefined)
        setRetryTrigger((version) => version + 1)
      }}>重试连接</Action>}
      {failure?.retry === 'new' && <Action onClick={onStartAgain}>重新生成</Action>}
      <Action secondary onClick={() => onCancel(Boolean(failure))}>{failure ? '换个主题' : live ? '返回首页，稍后查看' : '返回首页'}</Action>
    </View>
  </View>
}
