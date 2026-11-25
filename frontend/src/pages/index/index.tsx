import { ScrollView, Text, View } from '@tarojs/components'
import { useDidHide, useDidShow } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import {
  api, readPendingGenerations, readRestartGroup, savePendingGenerations, saveRestartGroup,
  type PendingGeneration, type RestartGroup, type TopicInput,
} from '../../api/client'
import { RequestError } from '../../api/request'
import type { AnswerReceipt, Attempt, Evidence, LearningRecord, UserProfile } from '../../api/types'
import { Action, EvidenceSheet, Header, Sheet, Tabs, getWindowLayout } from '../../components/comic'
import { makeKey, PENDING_UNSUBMITTED_AGE, prunePendingGenerations, restartAvailable } from '../../features/flow'
import { GenerationScreen, type GenerationIntent } from '../../features/generation'
import { HomeScreen } from '../../features/home'
import { QuestionBankScreen } from '../../features/question-bank'
import { ProfileScreen } from '../../features/profile'
import { AnswerWorkspace, FeedbackScreen, QuizScreen, type ActiveAttempt } from '../../features/quiz'
import { HistoryScreen, RecordScreen } from '../../features/records'
import { MistakeBookScreen, MistakeDetailScreen } from '../../features/mistake-book'
import { createMistakeBookState, type MistakeBookState } from '../../features/mistakes'
import './index.css'

type ResultScreen = { name: 'result' | 'record'; recordId: string }
type MistakesScreen = { name: 'mistakes'; recordId: string; from: ResultScreen }
type Screen =
  | { name: 'home' | 'questionBank' | 'answer' | 'history' | 'mistakeBook' | 'profile' }
  | { name: 'generation'; intent: GenerationIntent }
  | { name: 'quiz'; attempt: ActiveAttempt; practiceFingerprint?: string }
  | { name: 'feedback'; attempt: ActiveAttempt; receipt: AnswerReceipt; practiceFingerprint?: string }
  | ResultScreen
  | MistakesScreen
  | { name: 'mistakeDetail'; fingerprint: string }
  | { name: 'review'; recordId: string; questionId: string; from: ResultScreen | MistakesScreen }

const titles: Record<Screen['name'], string> = {
  home: '轻知闯关',
  generation: '生成练习',
  quiz: '练习',
  feedback: '练习',
  result: '本次结果',
  history: '学习记录',
  questionBank: '我的题库',
  answer: '答题',
  profile: '我的',
  record: '记录详情',
  mistakes: '错题回顾',
  mistakeBook: '错题本',
  mistakeDetail: '错题详情',
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
  const [profile, setProfile] = useState<UserProfile>()
  const [mistakeBook, setMistakeBook] = useState<MistakeBookState>(createMistakeBookState)
  const mistakeScrollTop = useRef(0)
  const cache = useRef(new Map<string, LearningRecord>()).current
  const profileUserId = useRef<string>()

  function applyProfile(next: UserProfile) {
    if (profileUserId.current !== next.userId) {
      cache.clear()
      profileUserId.current = next.userId
      const userPending = readPendingGenerations()
      pendingRef.current = userPending
      setPending(userPending)
      setRestart(readRestartGroup())
    }
    setProfile(next)
  }

  useEffect(() => {
    let active = true
    api.profile().then((next) => {
      if (active) applyProfile(next)
    }).catch(() => {})
    return () => { active = false }
  }, [cache])

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

  function openMistakePractice(attempt: Attempt, fingerprint: string) {
    if (attempt.status !== 'ACTIVE' || !attempt.question) {
      throw new RequestError('ATTEMPT_UNAVAILABLE', '这道题暂时无法开始再练，请重试。', 410)
    }
    setScreen({
      name: 'quiz',
      attempt: { ...attempt, question: attempt.question },
      practiceFingerprint: fingerprint,
    })
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
      const currentJob = await api.currentJob().catch(() => null)
      if (currentJob) {
        const existing = pendingRef.current.find((entry) => entry.jobId === currentJob.jobId)
        const restored: PendingGeneration = existing ?? {
          kind: 'generate', input: { topic: currentJob.topicTitle }, title: currentJob.topicTitle,
          key: currentJob.jobId, jobId: currentJob.jobId, requestedAt: Date.parse(currentJob.createdAt),
        }
        updatePending(() => [{ ...restored, status: currentJob.status, phase: currentJob.phase }])
        return
      }
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
          results.set(entry.key, { drop: false, status: job.status, phase: job.phase })
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
    const timer = setInterval(() => { void reconcilePending() }, 3000)
    const saved = readRestartGroup()
    if (!saved) return () => clearInterval(timer)
    let active = true
    const lookup = saved.attemptId ? api.attempt(saved.attemptId) : api.latestAttempt(saved.quizId)
    lookup.then((attempt) => {
      if (active && attempt.status === 'ACTIVE') recordRestartGroup(attempt)
    }).catch(() => {})
    return () => { active = false; clearInterval(timer) }
  }, [screen.name])

  function openGeneration(intent: PendingGeneration) {
    setScreen({ name: 'generation', intent })
  }

  function launchGeneration(topic: string, topicInput: TopicInput = { topic }) {
    setInput(topic)
    setTopicError('')
    if (pendingRef.current.some((entry) => entry.status === 'QUEUED' || entry.status === 'RUNNING')) {
      setTopicError('请先完成当前正在生成的题组。')
      return
    }
    const intent: PendingGeneration = {
      kind: 'generate', input: topicInput, title: topic, key: makeKey(), requestedAt: Date.now(),
    }
    updatePending(() => [intent])
    void api.generate(topicInput, intent.key).then((job) => {
      updatePending(() => [{ ...intent, jobId: job.jobId, status: job.status, phase: job.phase }])
    }).catch(async (problem) => {
      if (problem instanceof RequestError && problem.status === 409) {
        const current = await api.currentJob().catch(() => null)
        if (current) {
          updatePending(() => [{ ...intent, jobId: current.jobId, title: current.topicTitle,
            input: { topic: current.topicTitle }, status: current.status, phase: current.phase }])
          return
        }
      }
      updatePending(() => [])
      setTopicError(problem instanceof RequestError ? problem.message : '生成失败，请稍后重试。')
    })
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
      case 'mistakeDetail':
        setScreen({ name: 'mistakeBook' })
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
          onGenerate={launchGeneration} onPending={(intent) => intent.status === 'FAILED'
            ? launchGeneration(intent.title) : openGeneration(intent)} onRestart={restartQuiz} />
      case 'questionBank':
        return <QuestionBankScreen onTopic={(topic) => {
          updatePending((entries) => entries.filter((entry) => entry.title !== topic.title))
          if (topic.attemptId && topic.attemptStatus === 'ACTIVE') {
            void api.attempt(topic.attemptId).then(openAttempt).catch(() => {
              setScreen({ name: 'generation', intent: { kind: 'saved', topicKey: topic.topicKey, title: topic.title, key: makeKey() } })
            })
            return
          }
          setScreen({ name: 'generation', intent: { kind: 'saved', topicKey: topic.topicKey, title: topic.title, key: makeKey() } })
        }} />
      case 'answer':
        return <AnswerWorkspace onOpen={openAttempt} />
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
            if (intent.kind === 'restart' || intent.kind === 'resume') launchGeneration(intent.group.topicTitle)
            else if (intent.kind === 'saved') launchGeneration(intent.title, { topicKey: intent.topicKey })
            else {
              forgetPending(intent.key)
              launchGeneration(intent.title, intent.input)
            }
          }} />
      }
      case 'quiz':
        return <QuizScreen attempt={screen.attempt} onRegenerate={() => launchGeneration(screen.attempt.topicTitle)} onAnswer={(receipt) => {
          void api.profile().then(applyProfile).catch(() => {})
          if (!screen.practiceFingerprint) {
            if (receipt.recordId) recordRestartGroup()
            else recordRestartGroup({ ...screen.attempt, answeredCount: receipt.answeredCount, totalQuestions: receipt.totalQuestions })
          }
          setScreen({ name: 'feedback', attempt: screen.attempt, receipt, practiceFingerprint: screen.practiceFingerprint })
        }} />
      case 'feedback':
        return <FeedbackScreen attempt={screen.attempt} receipt={screen.receipt} onOpen={openAttempt}
          onEvidence={setEvidence} onRegenerate={() => launchGeneration(screen.attempt.topicTitle)} onResult={(recordId) => setScreen({ name: 'result', recordId })}
          onPrevious={(index) => {
            void api.attemptAnswer(screen.attempt.attemptId, index).then((receipt) => {
              setScreen({ ...screen, receipt })
            }).catch(() => {})
          }}
          practiceFingerprint={screen.practiceFingerprint} onPracticeFinished={() => {
            if (screen.practiceFingerprint) setScreen({ name: 'mistakeDetail', fingerprint: screen.practiceFingerprint })
          }} />
      case 'history':
        return <HistoryScreen onHome={openHome} onRecord={(recordId) => setScreen({ name: 'record', recordId })} />
      case 'profile':
        return <ProfileScreen profile={profile} onProfile={async (nickname) => {
          const next = await api.updateProfile(nickname)
          applyProfile(next)
          return next
        }} onHistory={() => setScreen({ name: 'history' })}
          onRecord={(recordId) => setScreen({ name: 'record', recordId })} />
      case 'mistakeBook':
        return <MistakeBookScreen state={mistakeBook} onState={setMistakeBook}
          onOpen={(fingerprint) => {
            setMistakeBook((previous) => ({ ...previous, scrollTop: mistakeScrollTop.current }))
            setScreen({ name: 'mistakeDetail', fingerprint })
          }} />
      case 'mistakeDetail':
        return <MistakeDetailScreen fingerprint={screen.fingerprint}
          onUpdated={(previous, next) => setMistakeBook((state) => {
            const matchesFilter = state.status === next.status
            const existingIndex = state.items.findIndex((item) => item.fingerprint === next.fingerprint)
            const items = matchesFilter
              ? existingIndex >= 0
                ? state.items.map((item) => item.fingerprint === next.fingerprint ? next : item)
                : [next, ...state.items]
              : state.items.filter((item) => item.fingerprint !== next.fingerprint)
            const countDelta = Number(next.status === 'to_review') - Number(previous.status === 'to_review')
            return {
              ...state,
              items,
              pendingCount: Math.max(0, state.pendingCount + countDelta),
            }
          })}
          onEvidence={setEvidence} onPractice={(attempt) => openMistakePractice(attempt, screen.fingerprint)} />
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

  const tab = screen.name === 'quiz' || screen.name === 'feedback' ? 'answer'
    : screen.name === 'home' || screen.name === 'questionBank' || screen.name === 'answer' || screen.name === 'mistakeBook' || screen.name === 'profile'
    ? screen.name
    : undefined
  const bodyHeight = Math.max(0, layout.windowHeight - layout.headerHeight - (tab ? layout.tabHeight + layout.safeBottom : 0))
  const routeKey = screen.name === 'quiz'
    ? `${screen.name}:${screen.attempt.question.questionId}`
    : screen.name === 'feedback' ? `${screen.name}:${screen.receipt.question.questionId}`
    : screen.name === 'generation' ? `generation:${screen.intent.key}`
      : screen.name
  return <View className='app-shell' style={{ height: `${layout.windowHeight}px` }}>
    <Header title={titles[screen.name]} layout={layout} onBack={tab ? undefined : onBack} />
    <ScrollView className='app-scroll' key={routeKey} style={{ height: `${bodyHeight}px` }}
      scrollY={!evidence && !confirmExit}
      scrollTop={screen.name === 'mistakeBook' ? mistakeBook.scrollTop : 0}
      onScroll={(event) => {
        if (screen.name === 'mistakeBook') mistakeScrollTop.current = event.detail.scrollTop
      }}>
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
      <Action secondary onClick={() => {
        setConfirmExit(false)
        if ((screen.name === 'quiz' || screen.name === 'feedback') && screen.practiceFingerprint) {
          setScreen({ name: 'mistakeDetail', fingerprint: screen.practiceFingerprint })
        } else openHome()
      }}>退出练习</Action>
    </Sheet>}
  </View>
}
