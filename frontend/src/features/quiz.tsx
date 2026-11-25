import { Button, Image, Text, View } from '@tarojs/components'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { attemptNeedsRegeneration, messageForError, RequestError } from '../api/request'
import type { AnswerReceipt, Attempt, Evidence, Question } from '../api/types'
import happy from '../assets/student-happy.webp'
import surprised from '../assets/student-wow.webp'
import { Action, ComicRow, ErrorNote, Tape } from '../components/comic'
import { optionAppearance, questionTypeLabels, selectOption } from './flow'
import { practiceFinished } from './mistakes'

export type ActiveAttempt = Attempt & { question: Question }

export function ProgressMeter({ title, index, total, completed }: {
  title: string; index: number; total: number; completed: number
}) {
  return <View className='quiz-top'>
    <View className='quiz-meta'>
      <Tape>{title}</Tape>
      <Text className='quiz-counter'>{String(index + 1).padStart(2, '0')} / {total}</Text>
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
        <Text className={`answer-letter${index % 2 ? ' even' : ''}`} aria-hidden>{label}</Text>
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

export function QuizScreen({ attempt, onAnswer, onRegenerate }: {
  attempt: ActiveAttempt
  onAnswer: (receipt: AnswerReceipt) => void
  onRegenerate: () => void
}) {
  const { question } = attempt
  const [selected, setSelected] = useState<string[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [unavailable, setUnavailable] = useState(false)
  const frozenSelection = useRef<string[]>()
  const inFlight = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  async function submit() {
    if (!selected.length || inFlight.current) return
    frozenSelection.current ??= selected
    inFlight.current = true
    setPending(true)
    setError('')
    try {
      const receipt = await api.answer(attempt.attemptId, question.questionId, frozenSelection.current)
      if (mounted.current) onAnswer(receipt)
    } catch (problem) {
      if (!mounted.current) return
      if (problem instanceof RequestError && problem.code === 'INVALID_SELECTION') frozenSelection.current = undefined
      const needsRegeneration = attemptNeedsRegeneration(problem)
      setUnavailable(needsRegeneration)
      setError(needsRegeneration ? '这组练习已不可用，请重新生成。' : messageForError(problem))
    } finally {
      inFlight.current = false
      if (mounted.current) setPending(false)
    }
  }

  return <>
    <ProgressMeter title={attempt.topicTitle} index={question.index} total={attempt.totalQuestions} completed={attempt.answeredCount} />
    <View className='question-comic'>
      <View className='question-badges'>
        <Tape tone='yellow'>{questionTypeLabels[question.type]}</Tape>
        {question.aiGenerated && <Tape>AI 生成</Tape>}
      </View>
      <Text className='question-heading'>{question.stem}</Text>
      <Text className='question-note'>{question.type === 'multi' ? '可选多个答案，选完再提交。' : '请选择一个答案。'}</Text>
    </View>
    <AnswerOptions question={question} selected={selected} onSelect={(id) => {
      if (!frozenSelection.current) setSelected((previous) => selectOption(question.type, previous, id))
    }} />
    <View className='quiz-actions'>
      {error && <ErrorNote>{error}</ErrorNote>}
      {unavailable ? <Action onClick={onRegenerate}>重新生成题组</Action> : <Action disabled={!selected.length || pending} onClick={() => { void submit() }}>
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
      <Text className='coin-result'>{receipt.coinDelta > 0 ? `金币 +${receipt.coinDelta}`
        : receipt.coinDelta < 0 ? `金币 ${receipt.coinDelta}`
          : isCorrect ? '本题金币已结算' : receipt.coinBalance === 0 ? '金币为 0，余额不再减少' : '本题金币已结算'}
        {'  ·  余额 '}{receipt.coinBalance}</Text>
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

export function FeedbackScreen({ attempt, receipt, onEvidence, onOpen, onResult, onRegenerate, practiceFingerprint, onPracticeFinished }: {
  attempt: ActiveAttempt
  receipt: AnswerReceipt
  onEvidence: (evidence: Evidence[]) => void
  onOpen: (attempt: Attempt) => void
  onResult: (recordId: string) => void
  onRegenerate: () => void
  practiceFingerprint?: string
  onPracticeFinished?: () => void
}) {
  const [pending, setPending] = useState(false)
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

  return <>
    <ProgressMeter title={attempt.topicTitle} index={receipt.question.index}
      total={receipt.totalQuestions} completed={receipt.answeredCount} />
    <ReceiptView receipt={receipt} onEvidence={onEvidence} />
    <View className='quiz-actions'>
      {error && <ErrorNote>{error}</ErrorNote>}
      {unavailable ? <Action onClick={onRegenerate}>重新生成题组</Action> : <Action disabled={pending} onClick={() => { void proceed() }}>
        {practiceFingerprint && practiceFinished(receipt.answeredCount, receipt.totalQuestions)
          ? '返回错题详情'
          : receipt.recordId ? '查看通关结果' : pending ? '正在加载…' : '下一题'}
      </Action>}
    </View>
  </>
}
