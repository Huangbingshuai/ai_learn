import { ScrollView, Text, View } from '@tarojs/components'
import Taro, { useDidHide, useDidShow } from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'
import {
  api, readPendingGenerations, savePendingGenerations, type PendingGeneration, type TopicInput,
} from '../../api/client'
import { messageForError, RequestError } from '../../api/request'
import type { AnswerReceipt, Attempt, Evidence, LearningRecord, UserProfile } from '../../api/types'
import { Action, EvidenceSheet, Header, Sheet, Tabs, getWindowLayout } from '../../components/comic'
import { makeKey, PENDING_UNSUBMITTED_AGE, prunePendingGenerations } from '../../features/flow'
import { GenerationScreen, type GenerationIntent } from '../../features/generation'
import { HomeScreen } from '../../features/home'
import { QuestionBankScreen } from '../../features/question-bank'
import { ProfileScreen } from '../../features/profile'
import { AnswerWorkspace, FeedbackScreen, QuizScreen, type ActiveAttempt } from '../../features/quiz'
import { HistoryScreen, RecordScreen } from '../../features/records'
import { FavoritesScreen } from '../../features/favorites'
import { MistakeBookScreen } from '../../features/mistake-book'
import { createMistakeBookState, removeMistakeFromBook, type MistakeBookState } from '../../features/mistakes'
import './index.css'

type ResultScreen = { name: 'result' | 'record'; recordId: string }
type MistakesScreen = { name: 'mistakes'; recordId: string; from: ResultScreen }
type Screen =
  | { name: 'home' | 'questionBank' | 'answer' | 'history' | 'mistakeBook' | 'favorites' | 'profile' }
  | { name: 'generation'; intent: GenerationIntent }
  | { name: 'quiz' | 'mistakeQuiz'; attempt: ActiveAttempt; practiceFingerprint?: string }
  | { name: 'feedback' | 'mistakeFeedback'; attempt: ActiveAttempt; receipt: AnswerReceipt; practiceFingerprint?: string }
  | ResultScreen
  | MistakesScreen
  | { name: 'review'; recordId: string; questionId: string; from: ResultScreen | MistakesScreen }

const titles: Record<Screen['name'], string> = {
  home: '轻知闯关',
  generation: '生成练习',
  quiz: '练习',
  feedback: '练习',
  mistakeQuiz: '错题练习',
  mistakeFeedback: '错题练习',
  result: '本次结果',
  history: '学习记录',
  questionBank: '我的题库',
  answer: '答题',
  profile: '我的',
  record: '记录详情',
  mistakes: '错题回顾',
  mistakeBook: '错题本',
  favorites: '收藏夹',
  review: '作答回顾',
}

export default function Index() {
  const [layout, setLayout] = useState(getWindowLayout)
  const [screen, setScreen] = useState<Screen>({ name: 'home' })
  const screenRef = useRef(screen)
  screenRef.current = screen
  const [input, setInput] = useState('')
  const [topicError, setTopicError] = useState('')
  const [pending, setPending] = useState(readPendingGenerations)
  const pendingRef = useRef(pending)
  const currentAttemptId = useRef<string>()
  const mistakePracticePending = useRef(false)
  const mistakeQueue = useRef<{ fingerprints: string[]; index: number; kind: 'mistakes' | 'favorites'; attempts: Record<number, ActiveAttempt>; receipts: Record<number, AnswerReceipt>; removed: Set<string> }>()
  const [visible, setVisible] = useState(true)
  const [evidence, setEvidence] = useState<Evidence[]>()
  const [confirmExit, setConfirmExit] = useState(false)
  const historyReturn = useRef<'home' | 'profile'>('home')
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

  function openHistory(returnTo: 'home' | 'profile' = 'home') {
    historyReturn.current = returnTo === 'profile' ? 'profile' : 'home'
    setScreen({ name: 'history' })
  }

  async function navigateTab(next: 'home' | 'questionBank' | 'answer' | 'mistakeBook' | 'favorites' | 'profile') {
    setScreen({ name: next })
  }

  function openAttempt(attempt: Attempt) {
    if (attempt.purpose !== 'LEARNING') throw new RequestError('ATTEMPT_UNAVAILABLE', '请从错题本打开错题练习。', 409)
    if (attempt.status === 'PASSED' && attempt.recordId) {
      currentAttemptId.current = undefined
      setScreen({ name: 'result', recordId: attempt.recordId })
      return
    }
    if (attempt.status !== 'ACTIVE' || !attempt.question) {
      throw new RequestError('ATTEMPT_UNAVAILABLE', '这组练习暂时无法打开，请重新生成。', 410)
    }
    currentAttemptId.current = attempt.attemptId
    setScreen({ name: 'quiz', attempt: { ...attempt, question: attempt.question } })
  }

  function openMistakePractice(attempt: Attempt, fingerprint: string) {
    if (attempt.status !== 'ACTIVE' || !attempt.question) {
      throw new RequestError('ATTEMPT_UNAVAILABLE', '这道题暂时无法开始再练，请重试。', 410)
    }
    setScreen({
      name: 'mistakeQuiz',
      attempt: { ...attempt, question: attempt.question },
      practiceFingerprint: fingerprint,
    })
  }

  async function advanceMistakePractice() {
    const queue = mistakeQueue.current
    if (!queue || mistakePracticePending.current) return
    if (queue.kind === 'favorites' && queue.index >= 0 && queue.removed.has(queue.fingerprints[queue.index])) {
      const index = queue.index
      queue.fingerprints.splice(index, 1)
      for (let i = index; i <= queue.fingerprints.length; i++) {
        if (queue.attempts[i + 1]) queue.attempts[i] = queue.attempts[i + 1]
        else delete queue.attempts[i]
        if (queue.receipts[i + 1]) queue.receipts[i] = queue.receipts[i + 1]
        else delete queue.receipts[i]
      }
      queue.index--
    }
    const nextIndex = queue.index + 1
    if (nextIndex >= queue.fingerprints.length) {
      mistakeQueue.current = undefined
      setMistakeBook((previous) => ({ ...previous, items: [], nextCursor: null, loaded: false }))
      setScreen({ name: queue.kind === 'favorites' ? 'favorites' : 'mistakeBook' })
      return
    }
    const savedReceipt = queue.receipts[nextIndex]
    const savedAttempt = queue.attempts[nextIndex]
    if (savedReceipt && savedAttempt) {
      queue.index = nextIndex
      setScreen({ name: 'mistakeFeedback', attempt: savedAttempt, receipt: savedReceipt, practiceFingerprint: queue.fingerprints[nextIndex] })
      return
    }
    mistakePracticePending.current = true
    void Taro.showLoading({ title: '正在打开题目…', mask: true })
    try {
      const fingerprint = queue.fingerprints[nextIndex]
      const attempt = savedAttempt ? await api.attempt(savedAttempt.attemptId) : await (queue.kind === 'favorites' ? api.startFavoritePractice(fingerprint, makeKey()) : api.startMistakePractice(fingerprint, makeKey()))
      if (!attempt.question) throw new RequestError('ATTEMPT_UNAVAILABLE', '这道题暂时无法打开。', 410)
      queue.attempts[nextIndex] = { ...attempt, question: attempt.question }
      queue.index = nextIndex
      openMistakePractice(attempt, fingerprint)
    } catch (problem) {
      void Taro.showToast({ title: messageForError(problem), icon: 'none' })
    } finally {
      mistakePracticePending.current = false
      void Taro.hideLoading()
    }
  }

  function previousMistake() {
    const queue = mistakeQueue.current
    if (!queue || queue.index <= 0) return
    const previous = queue.index - 1
    const attempt = queue.attempts[previous]
    const receipt = queue.receipts[previous]
    if (!attempt || !receipt) return
    queue.index = previous
    setScreen({ name: 'mistakeFeedback', attempt, receipt, practiceFingerprint: queue.fingerprints[previous] })
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
    const inlineGenerationActive = pendingRef.current.some((entry) => entry.status !== 'FAILED')
    if (screenRef.current.name === 'home' && inlineGenerationActive) return
    reconciling.current = true
    try {
      const currentJob = await api.currentJob().catch(() => null)
      if (currentJob && (currentJob.status === 'QUEUED' || currentJob.status === 'RUNNING')) {
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
          if (job.status === 'SUCCEEDED' && job.quizId && screenRef.current.name !== 'home') {
            const attempt = await api.startAttempt(job.quizId, `${entry.key}-attempt`)
            if (pendingRef.current.some((current) => current.key === entry.key)) {
              openAttempt(attempt)
              forgetPending(entry.key)
            }
            results.set(entry.key, { drop: true })
            return
          }
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
    void reconcilePending()
    const timer = setInterval(() => { void reconcilePending() }, 3000)
    return () => clearInterval(timer)
  }, [screen.name])

  function launchGeneration(topic: string, topicInput: TopicInput = { topic }) {
    setInput(topic)
    setTopicError('')
    if (pendingRef.current.some((entry) => !entry.status || entry.status === 'QUEUED'
      || entry.status === 'RUNNING')) {
      setTopicError('请先完成当前正在生成的题组。')
      return
    }
    const intent: PendingGeneration = {
      kind: 'generate', input: topicInput, title: topic, key: makeKey(), requestedAt: Date.now(), status: 'QUEUED',
    }
    updatePending(() => [intent])
  }

  function onBack() {
    switch (screen.name) {
      case 'quiz':
      case 'feedback':
      case 'mistakeQuiz':
      case 'mistakeFeedback':
        if (screen.name === 'feedback' && screen.receipt.recordId) {
          openHome()
        } else setConfirmExit(true)
        break
      case 'result':
        openHistory()
        break
      case 'record':
        setScreen({ name: 'history' })
        break
      case 'history':
        setScreen({ name: historyReturn.current })
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
      case 'home': {
        const activeGeneration = pending.find((entry) => entry.status !== 'FAILED')
        const generationProgress = activeGeneration && <GenerationScreen intent={activeGeneration} visible={visible} inline
          onOpen={(attempt) => {
            openAttempt(attempt)
            forgetPending(activeGeneration.key)
          }}
          onCancel={() => openHome()}
          onInvalidTopic={(error) => {
            forgetPending(activeGeneration.key)
            setTopicError(error)
          }}
          onJobId={(jobId) => updatePending((entries) =>
            entries.map((entry) => entry.key === activeGeneration.key ? { ...entry, jobId } : entry))}
          onFailed={(message) => {
            forgetPending(activeGeneration.key)
            if (message) setTopicError(message)
          }}
          onStartAgain={() => {
            forgetPending(activeGeneration.key)
            launchGeneration(activeGeneration.title, activeGeneration.input)
          }} />
        return <HomeScreen input={input} error={topicError} generating={Boolean(activeGeneration)} generationProgress={generationProgress} currentAttemptId={currentAttemptId.current}
          onNavigate={(destination) => setScreen({ name: destination })}
          onEdit={(next) => { setInput(next); setTopicError('') }} onError={setTopicError}
          onGenerate={launchGeneration} />
      }
      case 'questionBank':
        return <QuestionBankScreen onHome={openHome} onRedo={(topic) => {
          setScreen({ name: 'generation', intent: { kind: 'saved', topicKey: topic.topicKey, title: topic.title, key: makeKey() } })
        }} onTopic={(topic) => {
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
        return <AnswerWorkspace attemptId={currentAttemptId.current} onOpen={openAttempt}
          onQuestionBank={() => setScreen({ name: 'questionBank' })} onHome={openHome} />
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
            if (intent.kind === 'saved') launchGeneration(intent.title, { topicKey: intent.topicKey })
            else {
              forgetPending(intent.key)
              launchGeneration(intent.title, intent.input)
            }
          }} />
      }
      case 'quiz':
      case 'mistakeQuiz':
        return <QuizScreen key={screen.attempt.attemptId} attempt={screen.attempt}
          practicePosition={mistakeQueue.current && screen.practiceFingerprint ? { index: mistakeQueue.current.index, total: mistakeQueue.current.fingerprints.length } : undefined}
          onFavoriteChange={screen.practiceFingerprint && mistakeQueue.current?.kind === 'favorites' ? (favorited) => { if (favorited) mistakeQueue.current?.removed.delete(screen.practiceFingerprint!); else mistakeQueue.current?.removed.add(screen.practiceFingerprint!) } : undefined}
          onPreviousMistake={screen.name === 'mistakeQuiz' && (mistakeQueue.current?.index ?? 0) > 0 ? previousMistake : undefined}
          onRegenerate={() => launchGeneration(screen.attempt.topicTitle)} onAnswer={(receipt) => {
          if (screen.name === 'mistakeQuiz' && mistakeQueue.current) mistakeQueue.current.receipts[mistakeQueue.current.index] = receipt
          void api.profile().then(applyProfile).catch(() => {})
          setScreen({ name: screen.name === 'mistakeQuiz' ? 'mistakeFeedback' : 'feedback', attempt: screen.attempt, receipt, practiceFingerprint: screen.practiceFingerprint })
        }} />
      case 'feedback':
      case 'mistakeFeedback':
        return <FeedbackScreen key={`${screen.attempt.attemptId}:${screen.receipt.question.index}`} attempt={screen.attempt} receipt={screen.receipt} onOpen={openAttempt}
          onEvidence={setEvidence} onRegenerate={() => launchGeneration(screen.attempt.topicTitle)} onResult={(recordId) => setScreen({ name: 'result', recordId })}
          onPrevious={(index) => {
            if (screen.name === 'mistakeFeedback') { previousMistake(); return }
            void api.attemptAnswer(screen.attempt.attemptId, index).then((receipt) => {
              setScreen({ ...screen, receipt })
            }).catch(() => {})
          }}
          practiceFingerprint={screen.practiceFingerprint}
          practiceReturnLabel={mistakeQueue.current?.kind === 'favorites' ? '返回收藏夹' : '返回错题本'}
          practicePosition={mistakeQueue.current && screen.practiceFingerprint ? { index: mistakeQueue.current.index, total: mistakeQueue.current.fingerprints.length } : undefined}
          onFavoriteChange={screen.practiceFingerprint && mistakeQueue.current?.kind === 'favorites' ? (favorited) => { if (favorited) mistakeQueue.current?.removed.delete(screen.practiceFingerprint!); else mistakeQueue.current?.removed.add(screen.practiceFingerprint!) } : undefined}
          onMastered={screen.practiceFingerprint && mistakeQueue.current?.kind !== 'favorites' && !mistakeQueue.current?.removed.has(screen.practiceFingerprint) ? async () => {
            await api.removeMistake(screen.practiceFingerprint!)
            mistakeQueue.current?.removed.add(screen.practiceFingerprint!)
            setMistakeBook((previous) => removeMistakeFromBook(previous, screen.practiceFingerprint!))
            await advanceMistakePractice()
          } : undefined}
          onPracticeFinished={() => { void advanceMistakePractice() }} />
      case 'history':
        return <HistoryScreen onHome={openHome} onQuestionBank={() => setScreen({ name: 'questionBank' })}
          onRecord={(recordId) => setScreen({ name: 'record', recordId })} />
      case 'profile':
        return <ProfileScreen profile={profile} onProfile={async (nickname) => {
          const next = await api.updateProfile(nickname)
          applyProfile(next)
          return next
        }} onHistory={() => openHistory('profile')}
          onRecord={(recordId) => {
            historyReturn.current = 'profile'
            setScreen({ name: 'record', recordId })
          }} />
      case 'mistakeBook':
        return <MistakeBookScreen state={mistakeBook} onState={setMistakeBook}
          onHome={openHome} onQuestionBank={() => setScreen({ name: 'questionBank' })}
          onOpen={(fingerprints) => {
            if (mistakePracticePending.current || !fingerprints.length) return
            setMistakeBook((previous) => ({ ...previous, scrollTop: mistakeScrollTop.current }))
            mistakeQueue.current = { fingerprints, index: -1, kind: 'mistakes', attempts: {}, receipts: {}, removed: new Set() }
            void advanceMistakePractice()
          }} />
      case 'favorites':
        return <FavoritesScreen onQuestionBank={() => setScreen({ name: 'questionBank' })} onOpen={(fingerprints) => {
          if (mistakePracticePending.current || !fingerprints.length) return
          mistakeQueue.current = { fingerprints, index: -1, kind: 'favorites', attempts: {}, receipts: {}, removed: new Set() }
          void advanceMistakePractice()
        }} />
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
          onEvidence={setEvidence} onHome={openHome}
          onHistory={() => screen.name === 'result' ? openHistory() : setScreen({ name: 'history' })}
          onBack={onBack} />
    }
  }

  const tab = screen.name === 'mistakeQuiz' || screen.name === 'mistakeFeedback' ? (mistakeQueue.current?.kind === 'favorites' ? 'favorites' : 'mistakeBook')
    : screen.name === 'quiz' || screen.name === 'feedback' ? 'answer'
    : screen.name === 'home' || screen.name === 'questionBank' || screen.name === 'answer' || screen.name === 'mistakeBook' || screen.name === 'favorites' || screen.name === 'profile'
    ? screen.name
    : undefined
  const bodyHeight = Math.max(0, layout.windowHeight - layout.headerHeight - (tab ? layout.tabHeight + layout.safeBottom : 0))
  const routeKey = screen.name === 'quiz' || screen.name === 'mistakeQuiz'
    ? `${screen.name}:${screen.attempt.question.questionId}`
    : screen.name === 'feedback' || screen.name === 'mistakeFeedback' ? `${screen.name}:${screen.receipt.question.questionId}`
    : screen.name === 'generation' ? `generation:${screen.intent.key}`
      : screen.name
  return <View className='app-shell' style={{ height: `${layout.windowHeight}px` }}>
    <Header title={(screen.name === 'mistakeQuiz' || screen.name === 'mistakeFeedback') && mistakeQueue.current?.kind === 'favorites' ? '收藏练习' : titles[screen.name]} layout={layout} onBack={tab ? undefined : onBack} />
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
    {tab && <Tabs current={tab} safeBottom={layout.safeBottom} onNavigate={(next) => {
      if (next === 'mistakeBook') {
        setMistakeBook((previous) => ({
          ...previous,
          items: [],
          nextCursor: null,
          loaded: false,
        }))
      }
      void navigateTab(next)
    }} />}
    {evidence && <EvidenceSheet evidence={evidence} onClose={() => setEvidence(undefined)} />}
    {confirmExit && <Sheet title='要退出这组练习？' onClose={() => setConfirmExit(false)}>
      <Text className='body-copy'>返回首页后可继续当前答题进度。</Text>
      <Action onClick={() => setConfirmExit(false)}>继续答题</Action>
      <Action secondary onClick={() => {
        setConfirmExit(false)
        if ((screen.name === 'mistakeQuiz' || screen.name === 'mistakeFeedback') && screen.practiceFingerprint) {
          setScreen({ name: 'mistakeBook' })
        } else openHome()
      }}>退出练习</Action>
    </Sheet>}
  </View>
}
