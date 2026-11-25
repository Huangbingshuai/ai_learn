import { ScrollView, Text, View } from '@tarojs/components'
import { useDidHide, useDidShow } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import {
  api, readPendingGenerations, readRestartGroup, savePendingGenerations, saveRestartGroup,
  type PendingGeneration, type RestartGroup, type TopicInput,
} from '../../api/client'
import { RequestError } from '../../api/request'
import type { AnswerReceipt, Attempt, Evidence, LearningRecord } from '../../api/types'
import { Action, EvidenceSheet, Header, Sheet, Tabs, getWindowLayout } from '../../components/comic'
import { makeKey, PENDING_UNSUBMITTED_AGE, pendingIdentity, prunePendingGenerations, restartAvailable } from '../../features/flow'
import { GenerationScreen, type GenerationIntent } from '../../features/generation'
import { HomeScreen } from '../../features/home'
import { FeedbackScreen, QuizScreen, type ActiveAttempt } from '../../features/quiz'
import { HistoryScreen, RecordScreen } from '../../features/records'
import './index.css'

type ResultScreen = { name: 'result' | 'record'; recordId: string }
type MistakesScreen = { name: 'mistakes'; recordId: string; from: ResultScreen }
type Screen =
  | { name: 'home' | 'history' }
  | { name: 'generation'; intent: GenerationIntent }
  | { name: 'quiz'; attempt: ActiveAttempt }
  | { name: 'feedback'; attempt: ActiveAttempt; receipt: AnswerReceipt }
  | ResultScreen
  | MistakesScreen
  | { name: 'review'; recordId: string; questionId: string; from: ResultScreen | MistakesScreen }

const titles: Record<Screen['name'], string> = {
  home: '轻知闯关',
  generation: '生成练习',
  quiz: '练习',
  feedback: '练习',
  result: '本次结果',
  history: '学习记录',
  record: '记录详情',
  mistakes: '错题回顾',
  review: '作答回顾',
}

export default function Index() {
  const [layout, setLayout] = useState(getWindowLayout)
  const [screen, setScreen] = useState<Screen>({ name: 'home' })
  const [input, setInput] = useState('')
  const [topicError, setTopicError] = useState('')
  const [restart, setRestart] = useState(readRestartGroup)
  const [pending, setPending] = useState(readPendingGenerations)
  const pendingRef = useRef(pending)
  const [visible, setVisible] = useState(true)
  const [evidence, setEvidence] = useState<Evidence[]>()
  const [confirmExit, setConfirmExit] = useState(false)
  const cache = useRef(new Map<string, LearningRecord>()).current

  useDidShow(() => {
    setLayout(getWindowLayout())
    setVisible(true)
    void reconcilePending()
  })
  useDidHide(() => setVisible(false))

  function openHome() {
    setScreen({ name: 'home' })
  }

  function openHistory() {
    setScreen({ name: 'history' })
  }

  function recordRestartGroup(group?: RestartGroup) {
    saveRestartGroup(group)
    setRestart(group)
  }

  function openAttempt(attempt: Attempt) {
    if (attempt.status === 'PASSED' && attempt.recordId) {
      recordRestartGroup()
      setScreen({ name: 'result', recordId: attempt.recordId })
      return
    }
    if (attempt.status !== 'ACTIVE' || !attempt.question) {
      throw new RequestError('ATTEMPT_UNAVAILABLE', '这组练习暂时无法打开，请重新生成。', 410)
    }
    recordRestartGroup(attempt)
    setScreen({ name: 'quiz', attempt: { ...attempt, question: attempt.question } })
  }

  function updatePending(change: (entries: PendingGeneration[]) => PendingGeneration[]) {
    const next = change(pendingRef.current)
    pendingRef.current = next
    setPending(next)
    savePendingGenerations(next)
  }

  function forgetPending(key: string) {
    updatePending((entries) => entries.filter((entry) => entry.key !== key))
  }

  const reconciling = useRef(false)
  async function reconcilePending() {
    if (reconciling.current) return
    reconciling.current = true
    try {
      const entries = prunePendingGenerations(pendingRef.current)
      if (!entries.length) {
        if (pendingRef.current.length) updatePending(() => entries)
        return
      }
      const results = new Map<string, {
        drop: boolean
        status?: PendingGeneration['status']
        phase?: PendingGeneration['phase']
      }>()
      await Promise.all(entries.map(async (entry) => {
        if (!entry.jobId) {
          if (Date.now() - entry.requestedAt > PENDING_UNSUBMITTED_AGE) {
            results.set(entry.key, { drop: true })
          }
          return
        }
        try {
          const job = await api.job(entry.jobId)
          if (job.status === 'FAILED') results.set(entry.key, { drop: true })
          else results.set(entry.key, { drop: false, status: job.status, phase: job.phase })
        } catch (problem) {
          if (problem instanceof RequestError && problem.status === 404) {
            results.set(entry.key, { drop: true })
          }
        }
      }))
      updatePending((current) => prunePendingGenerations(current
        .filter((entry) => !results.get(entry.key)?.drop)
        .map((entry) => {
          const result = results.get(entry.key)
          return result && !result.drop
            ? { ...entry, status: result.status, phase: result.phase }
            : entry
        })))
    } finally {
      reconciling.current = false
    }
  }

  useEffect(() => {
    if (screen.name !== 'home') return
    void reconcilePending()
    const saved = readRestartGroup()
    if (!saved) return
    let active = true
    const lookup = saved.attemptId ? api.attempt(saved.attemptId) : api.latestAttempt(saved.quizId)
    lookup.then((attempt) => {
      if (active && attempt.status === 'ACTIVE') recordRestartGroup(attempt)
    }).catch(() => {})
    return () => { active = false }
  }, [screen.name])

  function openGeneration(intent: PendingGeneration) {
    setScreen({ name: 'generation', intent })
  }

  function launchGeneration(topic: string, topicInput: TopicInput = { topic }) {
    setInput(topic)
    setTopicError('')
    const intent: PendingGeneration = {
      kind: 'generate', input: topicInput, title: topic, key: makeKey(), requestedAt: Date.now(),
    }
    updatePending((entries) => prunePendingGenerations([
      intent,
      ...entries.filter((entry) => pendingIdentity(entry) !== pendingIdentity(intent)),
    ]).slice(0, 20))
    openGeneration(intent)
  }

  function launchFixed() {
    if (__QINGZHI_DEV_FIXED_QUIZ__) setScreen({ name: 'generation', intent: { kind: 'fixed', key: makeKey() } })
  }

  function restartQuiz(group: RestartGroup) {
    if (!restartAvailable(group.quizExpiresAt)) launchGeneration(group.topicTitle)
    else setScreen({ name: 'generation', intent: { kind: group.attemptId ? 'resume' : 'restart', group, key: makeKey() } })
  }

  function onBack() {
    switch (screen.name) {
      case 'quiz':
      case 'feedback':
        if (screen.name === 'feedback' && screen.receipt.recordId) {
          openHome()
        } else setConfirmExit(true)
        break
      case 'result':
      case 'record':
        openHistory()
        break
      case 'review':
      case 'mistakes':
        setScreen(screen.from)
        break
      case 'generation':
        openHome()
        break
    }
  }

  function renderScreen() {
    switch (screen.name) {
      case 'home':
        return <HomeScreen input={input} error={topicError} restart={restart} pending={pending}
          onEdit={(next) => { setInput(next); setTopicError('') }} onError={setTopicError}
          onGenerate={launchGeneration} onTopic={(topic) => {
            updatePending((entries) => entries.filter((entry) => entry.title !== topic.title))
            setScreen({ name: 'generation', intent: { kind: 'saved', topicKey: topic.topicKey, title: topic.title, key: makeKey() } })
          }}
          onPending={openGeneration} onFixedQuiz={launchFixed} onRestart={restartQuiz} />
      case 'generation': {
        const { intent } = screen
        return <GenerationScreen intent={intent} visible={visible}
          onOpen={(attempt) => {
            openAttempt(attempt)
            if (intent.kind === 'generate') forgetPending(intent.key)
          }}
          onCancel={(finished) => {
            if (finished && intent.kind === 'generate') forgetPending(intent.key)
            openHome()
          }}
          onInvalidTopic={(error) => {
            if (intent.kind === 'generate') forgetPending(intent.key)
            setTopicError(error)
            openHome()
          }}
          onJobId={(jobId) => updatePending((entries) =>
            entries.map((entry) => entry.key === intent.key ? { ...entry, jobId } : entry))}
          onFailed={() => { if (intent.kind === 'generate') forgetPending(intent.key) }}
          onStartAgain={() => {
            if (intent.kind === 'fixed') launchFixed()
            else if (intent.kind === 'restart' || intent.kind === 'resume') launchGeneration(intent.group.topicTitle)
            else if (intent.kind === 'saved') launchGeneration(intent.title, { topicKey: intent.topicKey })
            else {
              forgetPending(intent.key)
              launchGeneration(intent.title, intent.input)
            }
          }} />
      }
      case 'quiz':
        return <QuizScreen attempt={screen.attempt} onRegenerate={() => launchGeneration(screen.attempt.topicTitle)} onAnswer={(receipt) => {
          if (receipt.recordId) recordRestartGroup()
          else recordRestartGroup({ ...screen.attempt, answeredCount: receipt.answeredCount, totalQuestions: receipt.totalQuestions })
          setScreen({ name: 'feedback', attempt: screen.attempt, receipt })
        }} />
      case 'feedback':
        return <FeedbackScreen attempt={screen.attempt} receipt={screen.receipt} onOpen={openAttempt}
          onEvidence={setEvidence} onRegenerate={() => launchGeneration(screen.attempt.topicTitle)} onResult={(recordId) => setScreen({ name: 'result', recordId })} />
      case 'history':
        return <HistoryScreen onHome={openHome} onRecord={(recordId) => setScreen({ name: 'record', recordId })} />
      case 'result':
      case 'record':
      case 'mistakes':
      case 'review':
        return <RecordScreen recordId={screen.recordId} mode={screen.name} visible={visible} cache={cache}
          questionId={screen.name === 'review' ? screen.questionId : undefined}
          onMistakes={() => {
            if (screen.name === 'result' || screen.name === 'record') {
              setScreen({ name: 'mistakes', recordId: screen.recordId, from: screen })
            }
          }}
          onReview={(questionId) => {
            if (screen.name !== 'review') setScreen({ name: 'review', recordId: screen.recordId, questionId, from: screen })
          }}
          onEvidence={setEvidence} onHome={openHome} onHistory={openHistory} onBack={onBack} />
    }
  }

  const tab = screen.name === 'home' || screen.name === 'history' ? screen.name : undefined
  const bodyHeight = Math.max(0, layout.windowHeight - layout.headerHeight - (tab ? layout.tabHeight + layout.safeBottom : 0))
  const routeKey = screen.name === 'quiz' || screen.name === 'feedback'
    ? `${screen.name}:${screen.attempt.question.questionId}`
    : screen.name === 'generation' ? `generation:${screen.intent.key}`
      : screen.name
  return <View className='app-shell' style={{ height: `${layout.windowHeight}px` }}>
    <Header title={titles[screen.name]} layout={layout} onBack={tab ? undefined : onBack} />
    <ScrollView className='app-scroll' key={routeKey} style={{ height: `${bodyHeight}px` }}
      scrollY={!evidence && !confirmExit}>
      <View className={`screen screen-${screen.name}`} style={{
        minHeight: `${bodyHeight}px`,
        paddingBottom: `${Math.max(layout.screenInset, tab ? 0 : layout.safeBottom)}px`,
      }}>
        {renderScreen()}
      </View>
    </ScrollView>
    {tab && <Tabs current={tab} safeBottom={layout.safeBottom} onNavigate={(next) => setScreen({ name: next })} />}
    {evidence && <EvidenceSheet evidence={evidence} onClose={() => setEvidence(undefined)} />}
    {confirmExit && <Sheet title='要退出这组练习？' onClose={() => setConfirmExit(false)}>
      <Text className='body-copy'>返回首页后可继续当前答题进度。</Text>
      <Action onClick={() => setConfirmExit(false)}>继续答题</Action>
      <Action secondary onClick={() => { setConfirmExit(false); openHome() }}>退出练习</Action>
    </Sheet>}
  </View>
}
