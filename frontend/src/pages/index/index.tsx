import { ScrollView, Text, View } from '@tarojs/components'
import { useDidHide, useDidShow } from '@tarojs/taro'
import { useRef, useState } from 'react'
import { readRestartGroup, saveRestartGroup, type RestartGroup } from '../../api/client'
import { RequestError } from '../../api/request'
import type { AnswerReceipt, Attempt, Evidence, LearningRecord } from '../../api/types'
import { Action, EvidenceSheet, Header, Sheet, Tabs, getWindowLayout } from '../../components/comic'
import { makeKey, restartAvailable } from '../../features/flow'
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
  const [visible, setVisible] = useState(true)
  const [evidence, setEvidence] = useState<Evidence[]>()
  const [confirmExit, setConfirmExit] = useState(false)
  const cache = useRef(new Map<string, LearningRecord>()).current

  useDidShow(() => {
    setLayout(getWindowLayout())
    setVisible(true)
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

  function launchGeneration(topic: string) {
    setInput(topic)
    setTopicError('')
    setScreen({ name: 'generation', intent: { kind: 'generate', topic, key: makeKey() } })
  }

  function launchFixed() {
    if (__QINGZHI_DEV_FIXED_QUIZ__) setScreen({ name: 'generation', intent: { kind: 'fixed', key: makeKey() } })
  }

  function restartQuiz(group: RestartGroup) {
    if (!restartAvailable(group.quizExpiresAt)) launchGeneration(group.topicTitle)
    else setScreen({ name: 'generation', intent: { kind: 'restart', group, key: makeKey() } })
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
        return <HomeScreen input={input} error={topicError} restart={restart}
          onEdit={(next) => { setInput(next); setTopicError('') }} onError={setTopicError}
          onGenerate={launchGeneration} onFixedQuiz={launchFixed} onRestart={restartQuiz} />
      case 'generation':
        return <GenerationScreen intent={screen.intent} visible={visible} onOpen={openAttempt} onCancel={openHome}
          onInvalidTopic={(error) => { setTopicError(error); openHome() }}
          onStartAgain={() => {
            const { intent } = screen
            if (intent.kind === 'fixed') launchFixed()
            else launchGeneration(intent.kind === 'restart' ? intent.group.topicTitle : intent.topic)
          }} />
      case 'quiz':
        return <QuizScreen attempt={screen.attempt} onRegenerate={() => launchGeneration(screen.attempt.topicTitle)} onAnswer={(receipt) => {
          if (receipt.recordId) recordRestartGroup()
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
      <Text className='body-copy'>退出后会清除本次作答进度。题组仍有效时，可以从第 1 题重开。</Text>
      <Action onClick={() => setConfirmExit(false)}>继续答题</Action>
      <Action secondary onClick={() => { setConfirmExit(false); openHome() }}>退出练习</Action>
    </Sheet>}
  </View>
}
