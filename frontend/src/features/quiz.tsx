import { Button, Image, Text, View } from '@tarojs/components'
import { useEffect, useRef, useState } from 'react'
import Taro from '@tarojs/taro'
import { api } from '../api/client'
import { attemptNeedsRegeneration, messageForError, RequestError } from '../api/request'
import type { AnswerReceipt, Attempt, Evidence, Question } from '../api/types'
import happy from '../assets/student-happy.webp'
import surprised from '../assets/student-wow.webp'
import { Action, ComicRow, ErrorNote, StatusPanel, Tape } from '../components/comic'
import { optionAppearance, questionTypeLabels, selectOption } from './flow'
import { practiceFinished } from './mistakes'

export type ActiveAttempt = Attempt & { question: Question }

function FavoriteStar({ attemptId, questionId, onChange }: { attemptId: string; questionId: string; onChange?: (favorited: boolean) => void }) {
  const [favorite, setFavorite] = useState<{ fingerprint: string; favorited: boolean }>()
  const [pending, setPending] = useState(false)
  useEffect(() => {
    let active = true
    setFavorite(undefined)
    api.favoriteStatus(attemptId, questionId).then((value) => { if (active) setFavorite(value) }).catch(() => {})
    return () => { active = false }
  }, [attemptId, questionId])
  async function toggle() {
    if (!favorite || pending) return
    setPending(true)
    try {
      if (favorite.favorited) await api.removeFavorite(favorite.fingerprint)
      else await api.addFavorite(attemptId, questionId)
      setFavorite({ ...favorite, favorited: !favorite.favorited })
      onChange?.(!favorite.favorited)
      void Taro.showToast({ title: favorite.favorited ? '已取消收藏' : '已收藏', icon: 'none' })
    } catch (problem) { void Taro.showToast({ title: messageForError(problem), icon: 'none' }) }
    finally { setPending(false) }
  }
  return <Button className={`favorite-star ui-button${favorite?.favorited ? ' is-favorited' : ''}`}
    aria-label={favorite?.favorited ? '取消收藏' : '收藏题目'} aria-pressed={Boolean(favorite?.favorited)} disabled={!favorite || pending}
    hoverClass='favorite-star-press' onClick={() => { void toggle() }}><Text className='favorite-star-glyph' aria-hidden>{favorite?.favorited ? '★' : '☆'}</Text></Button>
}

export function AnswerWorkspace({ attemptId, onOpen, onQuestionBank, onHome }: {
  attemptId?: string
  onOpen: (attempt: Attempt) => void
  onQuestionBank: () => void
  onHome: () => void
}) {
  const [loading, setLoading] = useState(true)
  const [hasAttempt, setHasAttempt] = useState(false)
  const [error, setError] = useState('')
  const onOpenRef = useRef(onOpen)
  onOpenRef.current = onOpen

  useEffect(() => {
    let active = true
    async function restore() {
      try {
        if (attemptId) {
          const current = await api.attempt(attemptId).catch(() => undefined)
          if (current?.purpose === 'LEARNING' && current.status === 'ACTIVE' && current.question) {
            if (active) {
              setHasAttempt(true)
              onOpenRef.current(current)
            }
            return
          }
        }
        const page = await api.activeAttempts()
        if (!active) return
        const current = page.items.find((item) => item.purpose === 'LEARNING')
        if (current) {
          setHasAttempt(true)
          onOpenRef.current(current)
        } else {
          setHasAttempt(false)
        }
      } catch (problem) {
        if (active) setError(messageForError(problem))
      } finally {
        if (active) setLoading(false)
      }
    }
    void restore()
    return () => { active = false }
  }, [attemptId])

  if (loading) return <StatusPanel title='正在打开当前题目…' />
  if (error) return <ErrorNote>{error}</ErrorNote>
  if (!hasAttempt) return <StatusPanel title='还没有待答题目'>
    <View className='empty-actions empty-actions-split'>
      <Action secondary onClick={onQuestionBank}>去题库</Action>
      <Action onClick={onHome}>去学习</Action>
    </View>
  </StatusPanel>
  return <StatusPanel title='正在打开当前题目…' />
}

export function ProgressMeter({ title, index, total, completed, action }: {
  title: string; index: number; total: number; completed: number; action?: React.ReactNode
}) {
  return <View className='quiz-top'>
    <View className='quiz-meta'>
      <Tape>{title}</Tape>
      <View className='quiz-meta-right'>{action}<Text className='quiz-counter'>{String(index + 1).padStart(2, '0')} / {total}</Text></View>
    </View>
    <View className='progress-marks' aria-role='progressbar' aria-label={`第 ${index + 1} 题，共 ${total} 题，已完成 ${completed} 题`}>
      {Array.from({ length: total }, (_, item) => <View key={item}
        className={`progress-mark ${item < completed ? 'done' : item === index ? 'current' : ''}`} />)}
    </View>
  </View>
}

function AnswerOptions({ question, selected, correct, onSelect }: {
  question: Question
  selected: string[]
  correct?: string[]
  onSelect?: (id: string) => void
}) {
  return <View className='answer-list' aria-label={correct ? '首次作答结果' : '选择答案'}>
    {question.options.map(({ optionId, text }, index) => {
      const appearance = optionAppearance(optionId, selected, correct)
      const chosen = selected.includes(optionId)
      const className = `answer ${question.type === 'multi' ? '' : 'single'} ${appearance}`
      const label = String.fromCharCode(65 + index)
      const mark = appearance === 'correct' || appearance === 'selected' ? '✓' : appearance === 'incorrect' ? '×' : ''
      const contents = <>
        <Text className='answer-letter' aria-hidden>{label}</Text>
        <Text className='answer-text'>{text}</Text>
        <Text className='answer-icon' aria-hidden>{mark}</Text>
      </>
      if (correct) {
        return <View key={optionId} className={className}
          aria-label={`${label} ${text}${appearance === 'correct' ? '，正确答案' : ''}${chosen ? '，你的作答' : ''}`}>
          {contents}
        </View>
      }
      return <Button key={optionId} className={`${className} ui-button`} hoverClass='none'
        aria-pressed={chosen} aria-label={`${label} ${text}`}
        onClick={() => onSelect?.(optionId)}>
        {contents}
      </Button>
    })}
  </View>
}

export function QuizScreen({ attempt, onAnswer, onRegenerate, practicePosition, onPreviousMistake, onFavoriteChange }: {
  attempt: ActiveAttempt
  onAnswer: (receipt: AnswerReceipt) => void
  onRegenerate: () => void
  practicePosition?: { index: number; total: number }
  onPreviousMistake?: () => void
  onFavoriteChange?: (favorited: boolean) => void
}) {
  const { question } = attempt
  const [selected, setSelected] = useState<string[]>(attempt.draftOptionIds ?? [])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [unavailable, setUnavailable] = useState(false)
  const frozenSelection = useRef<string[]>()
  const draftTimer = useRef<ReturnType<typeof setTimeout>>()
  const inFlight = useRef(false)
  const mounted = useRef(true)
  const selectedRef = useRef(selected)
  const answerCommitted = useRef(false)
  selectedRef.current = selected

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (draftTimer.current) clearTimeout(draftTimer.current)
      if (!answerCommitted.current && !inFlight.current) {
        void api.saveDraft(attempt.attemptId, selectedRef.current).catch(() => {})
      }
    }
  }, [attempt.attemptId])

  useEffect(() => {
    if (selected.join('|') === (attempt.draftOptionIds ?? []).join('|')) return
    draftTimer.current = setTimeout(() => {
      void api.saveDraft(attempt.attemptId, selected).catch(() => {})
    }, 180)
    return () => { if (draftTimer.current) clearTimeout(draftTimer.current) }
  }, [attempt.attemptId, attempt.draftOptionIds, selected])

  async function submit() {
    if (!selected.length || inFlight.current) return
    if (draftTimer.current) clearTimeout(draftTimer.current)
    frozenSelection.current ??= selected
    inFlight.current = true
    setPending(true)
    setError('')
    try {
      const receipt = await api.answer(attempt.attemptId, question.questionId, frozenSelection.current)
      answerCommitted.current = true
      if (mounted.current) onAnswer(receipt)
    } catch (problem) {
      if (!mounted.current) return
      if (problem instanceof RequestError && problem.code === 'INVALID_SELECTION') frozenSelection.current = undefined
      const needsRegeneration = attemptNeedsRegeneration(problem)
      setUnavailable(needsRegeneration)
      setError(needsRegeneration ? '这组练习已不可用，请重新生成。' : messageForError(problem))
    } finally {
      inFlight.current = false
      if (!mounted.current && !answerCommitted.current) {
        void api.saveDraft(attempt.attemptId, frozenSelection.current ?? selectedRef.current).catch(() => {})
      }
      if (mounted.current) setPending(false)
    }
  }

  return <>
    <ProgressMeter title={attempt.topicTitle} index={practicePosition?.index ?? question.index} total={practicePosition?.total ?? attempt.totalQuestions} completed={practicePosition?.index ?? attempt.answeredCount} action={<FavoriteStar attemptId={attempt.attemptId} questionId={question.questionId} onChange={onFavoriteChange} />} />
    <View className='question-comic'>
      <View className='question-badges'>
        <Tape tone='yellow'>{questionTypeLabels[question.type]}</Tape>
      </View>
      <Text className='question-heading'>{question.stem}</Text>
    </View>
    <AnswerOptions question={question} selected={selected} onSelect={(id) => {
      if (!frozenSelection.current) setSelected((previous) => selectOption(question.type, previous, id))
    }} />
    <View className='quiz-actions'>
      {error && <ErrorNote>{error}</ErrorNote>}
      {onPreviousMistake ? <View className='feedback-navigation'>
        <Action secondary disabled={pending} onClick={onPreviousMistake}>上一题</Action>
        <Action disabled={!selected.length || pending} onClick={() => { void submit() }}>{pending ? '正在提交…' : '提交答案'}</Action>
      </View> : unavailable ? <Action onClick={onRegenerate}>重新生成题组</Action> : <Action disabled={!selected.length || pending} onClick={() => { void submit() }}>
        {pending ? '正在提交…' : error ? '重试提交' : '提交答案'}
      </Action>}
    </View>
  </>
}

function answerLabels(question: Question, ids: string[]) {
  return question.options.flatMap((option, index) => ids.includes(option.optionId)
    ? [question.type === 'judge' ? option.text : String.fromCharCode(65 + index)]
    : []).join('、')
}

export function ReceiptView({ receipt, onEvidence }: { receipt: AnswerReceipt; onEvidence: (evidence: Evidence[]) => void }) {
  const { question, isCorrect, selectedOptionIds, correctOptionIds } = receipt
  const incomplete = !isCorrect && question.type === 'multi'
    && selectedOptionIds.every((id) => correctOptionIds.includes(id))

  return <>
    <View className={`feedback${isCorrect ? ' is-correct' : ''}`}>
      <Text className='feedback-heading'>{isCorrect ? '答对啦！' : incomplete ? '还差选项' : '这题答错了'}</Text>
      <Image className='feedback-art' src={isCorrect ? happy : surprised} mode='widthFix' aria-hidden />
      <View className='feedback-answers'>
        <Text>你的作答 {answerLabels(question, selectedOptionIds)}</Text>
        <Text className='correct-label'>正确答案 {answerLabels(question, correctOptionIds)}</Text>
      </View>
      {receipt.coinSettlementNew && <Text className='coin-result'>
        {receipt.coinDelta > 0 ? `金币 +${receipt.coinDelta}`
          : receipt.coinDelta < 0 ? `金币 ${receipt.coinDelta}` : '金币 -0'}
      </Text>}
    </View>
    <Text className='review-stem'>{question.stem}</Text>
    <AnswerOptions question={question} selected={selectedOptionIds} correct={correctOptionIds} />
    <View className='speech'>
      <View className='speech-title'>
        <Text className='h2'>{isCorrect ? '为什么是这个答案？' : '原来如此！'}</Text>
        {question.aiGenerated && <Tape>AI 生成</Tape>}
      </View>
      <Text className='speech-copy'>{receipt.explanation}</Text>
    </View>
    {receipt.evidence.length > 0 && <ComicRow mint onClick={() => onEvidence(receipt.evidence)}>题目依据 · 查看原文与出处</ComicRow>}
  </>
}

export function FeedbackScreen({ attempt, receipt, onEvidence, onOpen, onResult, onRegenerate, onPrevious, practiceFingerprint, onPracticeFinished, onMastered, practicePosition, onFavoriteChange, practiceReturnLabel = '返回错题本' }: {
  attempt: ActiveAttempt
  receipt: AnswerReceipt
  onEvidence: (evidence: Evidence[]) => void
  onOpen: (attempt: Attempt) => void
  onResult: (recordId: string) => void
  onRegenerate: () => void
  onPrevious: (questionIndex: number) => void
  practiceFingerprint?: string
  onPracticeFinished?: () => void
  onMastered?: () => Promise<void>
  practicePosition?: { index: number; total: number }
  practiceReturnLabel?: string
  onFavoriteChange?: (favorited: boolean) => void
}) {
  const [pending, setPending] = useState(false)
  const [mastering, setMastering] = useState(false)
  const [removed, setRemoved] = useState(false)
  const [error, setError] = useState('')
  const [unavailable, setUnavailable] = useState(false)
  const inFlight = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  async function proceed() {
    if (practiceFingerprint && practiceFinished(receipt.answeredCount, receipt.totalQuestions)) {
      onPracticeFinished?.()
      return
    }
    if (receipt.recordId) {
      onResult(receipt.recordId)
      return
    }
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    setError('')
    try {
      const next = await api.attempt(attempt.attemptId)
      if (mounted.current) onOpen(next)
    } catch (problem) {
      if (mounted.current) {
        const needsRegeneration = attemptNeedsRegeneration(problem)
        setUnavailable(needsRegeneration)
        setError(needsRegeneration ? '这组练习已不可用，请重新生成。' : messageForError(problem))
      }
    } finally {
      inFlight.current = false
      if (mounted.current) setPending(false)
    }
  }

  async function markMastered() {
    if (!onMastered || mastering) return
    const result = await Taro.showModal({ title: '已掌握这道题？', content: '确认后，这道题会从错题本移除。', confirmText: '确认', cancelText: '继续练习' })
    if (!result.confirm) return
    setMastering(true)
    try { await onMastered(); setRemoved(true) } catch (problem) { setError(messageForError(problem)); setMastering(false) }
  }

  return <>
    <ProgressMeter title={attempt.topicTitle} index={practicePosition?.index ?? receipt.question.index}
      total={practicePosition?.total ?? receipt.totalQuestions} completed={practicePosition ? practicePosition.index + 1 : receipt.answeredCount}
      action={<><FavoriteStar attemptId={attempt.attemptId} questionId={receipt.question.questionId} onChange={onFavoriteChange} />{practiceFingerprint && onMastered && !removed && <Button className='mistake-mastered-button ui-button' hoverClass='row-press' disabled={mastering}
        onClick={() => { void markMastered() }}><Text>{mastering ? '正在移除…' : '已掌握'}</Text></Button>}</>} />
    <ReceiptView receipt={receipt} onEvidence={onEvidence} />
    <View className='quiz-actions'>
      {error && <ErrorNote>{error}</ErrorNote>}
      {(practicePosition ? practicePosition.index > 0 : receipt.question.index > 0) ? <View className='feedback-navigation'>
        <Action secondary onClick={() => onPrevious(receipt.question.index - 1)}>上一题</Action>
        {unavailable ? <Action onClick={onRegenerate}>重新生成题组</Action> : <Action disabled={pending} onClick={() => { void proceed() }}>
          {practiceFingerprint && practiceFinished(receipt.answeredCount, receipt.totalQuestions)
            ? (practicePosition && practicePosition.index + 1 < practicePosition.total ? '下一题' : practiceReturnLabel)
            : receipt.recordId ? '查看通关结果' : pending ? '正在加载…' : '下一题'}
        </Action>}
      </View> : unavailable ? <Action onClick={onRegenerate}>重新生成题组</Action> : <Action disabled={pending} onClick={() => { void proceed() }}>
        {practiceFingerprint && practiceFinished(receipt.answeredCount, receipt.totalQuestions)
          ? (practicePosition && practicePosition.index + 1 < practicePosition.total ? '下一题' : practiceReturnLabel)
          : receipt.recordId ? '查看通关结果' : pending ? '正在加载…' : '下一题'}
      </Action>}
    </View>
  </>
}
