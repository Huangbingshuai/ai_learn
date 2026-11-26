import { Button, Image, Text, View } from '@tarojs/components'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { messageForError, RequestError } from '../api/request'
import type { AiSummary, Evidence, LearningRecord, RecordItem, SummaryEntry } from '../api/types'
import happy from '../assets/student-happy.webp'
import thoughtful from '../assets/student-thoughtful.webp'
import discover from '../assets/student-discover.webp'
import confident from '../assets/student-confident.webp'
import celebrate from '../assets/student-celebrate.webp'
import { Action, ComicRow, ErrorNote, StatusPanel } from '../components/comic'
import { FIRST_POLL_DELAY, formatCompletionDate, makeKey, nextPollDelay, questionTypeLabels } from './flow'
import { ProgressMeter, ReceiptView } from './quiz'
import { resultTier, resultTitles } from './result-style'

const resultPortraits = { explore: thoughtful, discover, progress: happy, excellent: confident, perfect: celebrate }

function ResultBanner({ record, historical }: { record: LearningRecord; historical: boolean }) {
  const tier = resultTier(record.correctCount, record.totalQuestions)
  const [imageFailed, setImageFailed] = useState(false)
  useEffect(() => { setImageFailed(false) }, [record.recordId])
  return <View className={`result-banner result-${tier}${historical ? ' is-historical' : ''}`}>
    <View className='result-art' aria-hidden />
    <Text className='result-heading'>{resultTitles[tier]}</Text>
    <Text className='result-line result-topic-line'>{record.topicTitle} · 完成 {record.totalQuestions} 题</Text>
    {historical && <Text className='result-line'>完成时间 · {formatCompletionDate(record.completedAt)}</Text>}
    <Text className='result-score tape paper' aria-label={`答对 ${record.correctCount} 道题，共 ${record.totalQuestions} 道题`}>
      {record.correctCount} / {record.totalQuestions}
    </Text>
    <Text className='result-line'>本次答对</Text>
    {!imageFailed && <Image className='result-student' src={resultPortraits[tier]} mode='widthFix' aria-hidden onError={() => setImageFailed(true)} />}
  </View>
}

export function HistoryScreen({ onHome, onQuestionBank, onRecord }: {
  onHome: () => void
  onQuestionBank: () => void
  onRecord: (id: string) => void
}) {
  type Period = 'all' | '7d' | '30d'
  const periods: { id: Period; label: string }[] = [
    { id: 'all', label: '全部' },
    { id: '7d', label: '近7天' },
    { id: '30d', label: '近30天' },
  ]
  const [items, setItems] = useState<RecordItem[]>([])
  const [cursor, setCursor] = useState<string | null>()
  const [period, setPeriod] = useState<Period>('all')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(true)
  const inFlight = useRef(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    void load()
    return () => { mounted.current = false }
  }, [])

  async function load(after?: string, selectedPeriod: Period = period) {
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    setError('')
    try {
      const since = selectedPeriod === 'all'
        ? undefined
        : new Date(Date.now() - (selectedPeriod === '7d' ? 7 : 30) * 24 * 60 * 60 * 1000).toISOString()
      const page = await api.records(after, since)
      if (!mounted.current) return
      setItems((previous) => after ? [...previous, ...page.items] : page.items)
      setCursor(page.nextCursor ?? null)
    } catch (problem) {
      if (mounted.current) setError(messageForError(problem))
    } finally {
      inFlight.current = false
      if (mounted.current) setPending(false)
    }
  }

  if (!items.length) {
    if (pending) return <StatusPanel title='正在加载学习记录…' />
    if (error) return <StatusPanel title='暂时无法查看记录'>
      <ErrorNote>{error}</ErrorNote>
      <Action onClick={() => { void load() }}>重新加载</Action>
    </StatusPanel>
    if (period !== 'all') return <>
      <HistoryPeriodFilter period={period} periods={periods} onChange={(next) => {
        setPeriod(next)
        setItems([])
        setCursor(null)
        void load(undefined, next)
      }} />
      <StatusPanel title='这段时间还没有记录'>
        <Action secondary onClick={() => {
          setPeriod('all')
          setItems([])
          setCursor(null)
          void load(undefined, 'all')
        }}>查看全部记录</Action>
      </StatusPanel>
    </>
    return <StatusPanel title='还没有学习记录'>
      <View className='empty-actions empty-actions-split'>
        <Action secondary onClick={onQuestionBank}>去题库</Action>
        <Action onClick={onHome}>去学习</Action>
      </View>
    </StatusPanel>
  }

  return <>
    <HistoryPeriodFilter period={period} periods={periods} onChange={(next) => {
      setPeriod(next)
      setItems([])
      setCursor(null)
      void load(undefined, next)
    }} />
    <View className='comic-row-list history-list'>
      {items.map((item, index) => <ComicRow key={item.recordId} mint={index === 0}
        secondary={`${formatCompletionDate(item.completedAt)} · 答对 ${item.correctCount} / ${item.totalQuestions}`}
        onClick={() => onRecord(item.recordId)}>
        {item.topicTitle}
      </ComicRow>)}
    </View>
    {cursor && <View className='actions'>
      {error && <ErrorNote>{error}</ErrorNote>}
      <Action secondary disabled={pending} onClick={() => { void load(cursor) }}>
        {pending ? '正在加载…' : error ? '重试加载' : '加载更多'}
      </Action>
    </View>}
  </>
}

function HistoryPeriodFilter({ period, periods, onChange }: {
  period: 'all' | '7d' | '30d'
  periods: { id: 'all' | '7d' | '30d'; label: string }[]
  onChange: (period: 'all' | '7d' | '30d') => void
}) {
  return <View className='history-filter' aria-label='按时间筛选学习记录'>
    <View className='history-filter-options'>
      {periods.map((option) => <Button key={option.id}
        className={`ui-button history-filter-option${period === option.id ? ' selected' : ''}`}
        hoverClass='row-press' aria-pressed={period === option.id}
        onClick={() => onChange(option.id)}>
        <Text>{option.label}</Text>
      </Button>)}
    </View>
  </View>
}

export type RecordMode = 'result' | 'record' | 'mistakes' | 'review'
type RecordScreenProps = {
  recordId: string
  mode: RecordMode
  questionId?: string
  visible: boolean
  cache: Map<string, LearningRecord>
  onMistakes: () => void
  onReview: (id: string) => void
  onEvidence: (evidence: Evidence[]) => void
  onHome: () => void
  onHistory: () => void
  onBack: () => void
}

function pendingSummary(summary: AiSummary) {
  return summary.status === 'QUEUED' || summary.status === 'RUNNING'
}

export function RecordScreen({ recordId, mode, questionId, visible, cache,
  onMistakes, onReview, onEvidence, onHome, onHistory, onBack }: RecordScreenProps) {
  const [record, setRecord] = useState(() => cache.get(recordId))
  const [loadError, setLoadError] = useState('')
  const [retryError, setRetryError] = useState('')
  const [reload, setReload] = useState(0)
  const [retrying, setRetrying] = useState(false)
  const retryKey = useRef<string>()
  const retryInFlight = useRef(false)
  const mounted = useRef(true)
  const version = useRef(0)
  const shouldPoll = mode === 'result' || mode === 'record'

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    if (!visible || (!shouldPoll && cache.has(recordId))) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const currentVersion = version.current
    setLoadError('')

    async function fetchRecord(delay: number) {
      try {
        const result = await api.record(recordId)
        if (!active || version.current !== currentVersion) return
        cache.set(recordId, result)
        setRecord(result)
        if (shouldPoll && pendingSummary(result.aiSummary)) {
          timer = setTimeout(() => { void fetchRecord(nextPollDelay(delay)) }, delay)
        }
      } catch (problem) {
        if (active && version.current === currentVersion) setLoadError(messageForError(problem))
      }
    }
    void fetchRecord(FIRST_POLL_DELAY)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [cache, recordId, reload, shouldPoll, visible])

  async function retrySummary() {
    if (!record || retryInFlight.current) return
    retryInFlight.current = true
    retryKey.current ??= makeKey()
    setRetrying(true)
    setRetryError('')
    try {
      const aiSummary = await api.retrySummary(recordId, retryKey.current)
      if (!mounted.current) return
      version.current += 1
      retryKey.current = undefined
      const result = { ...record, aiSummary }
      cache.set(recordId, result)
      setRecord(result)
      setReload((revision) => revision + 1)
    } catch (problem) {
      if (!mounted.current) return
      if (problem instanceof RequestError && !problem.retryable && problem.status > 0) {
        retryKey.current = undefined
        setReload((revision) => revision + 1)
      }
      setRetryError(messageForError(problem))
    } finally {
      retryInFlight.current = false
      if (mounted.current) setRetrying(false)
    }
  }

  if (!record) return <StatusPanel title={loadError ? '暂时无法查看记录' : '正在加载学习结果…'}>
    {loadError && <>
      <ErrorNote>{loadError}</ErrorNote>
      <Action onClick={() => setReload((revision) => revision + 1)}>重新加载</Action>
    </>}
  </StatusPanel>

  if (mode === 'mistakes') {
    const mistakes = record.answers.filter((answer) => !answer.isCorrect)
    return <>
      <Text className='h1'>错题回顾</Text>
      <Text className='body-copy'>{record.topicTitle} · 共 {mistakes.length} 题</Text>
      <View className='comic-row-list review-list'>
        {mistakes.map(({ question }) => <ComicRow key={question.questionId}
          secondary={questionTypeLabels[question.type]} onClick={() => onReview(question.questionId)}>
          第 {question.index + 1} 题 · {question.stem}
        </ComicRow>)}
      </View>
      <View className='actions'><Action secondary onClick={onBack}>返回本次回顾</Action></View>
    </>
  }

  if (mode === 'review') {
    const receipt = record.answers.find(({ question }) => question.questionId === questionId)
    if (!receipt) return <StatusPanel title='暂时无法查看这道题'>
      <Action secondary onClick={onBack}>返回本次回顾</Action>
    </StatusPanel>
    return <>
      <ProgressMeter title={record.topicTitle} index={receipt.question.index}
        total={record.totalQuestions} completed={record.totalQuestions} />
      <ReceiptView receipt={receipt} onEvidence={onEvidence} />
      <View className='actions'><Action secondary onClick={onBack}>返回本次回顾</Action></View>
    </>
  }

  const wrongCount = record.totalQuestions - record.correctCount
  return <>
    <ResultBanner record={record} historical={mode === 'record'} />
    <View className='result-stats-card'>
      <View className='result-stat'><Text className='result-stat-label'>正确率</Text><Text className='result-stat-value'>{record.totalQuestions ? Math.round(record.correctCount / record.totalQuestions * 100) : 0}%</Text></View>
      <View className='result-stat'><Text className='result-stat-label'>本轮金币</Text><Text className='result-stat-value'>{record.coinDelta == null ? '—' : record.coinDelta > 0 ? `+${record.coinDelta}` : record.coinDelta}</Text></View>
      <View className='result-stat'><Text className='result-stat-label'>金币余额</Text><Text className='result-stat-value'>{record.coinBalance ?? '—'}</Text></View>
    </View>
    {wrongCount > 0 ? <ComicRow onClick={onMistakes}>错题回顾 · {wrongCount} 题</ComicRow>
      : <Text className='review-record-empty'>这次全部答对，没有错题。</Text>}
    <Summary record={record} wrongCount={wrongCount} onReview={onReview} retrying={retrying}
      loadError={loadError} retryError={retryError} onEvidence={onEvidence} onRetry={() => { void retrySummary() }}
      onRefresh={() => setReload((revision) => revision + 1)} />
    <View className='actions'>
      {mode === 'result' && <Action onClick={onHome}>再学一个主题</Action>}
      <Action secondary onClick={onHistory}>{mode === 'result' ? '查看学习记录' : '返回学习记录'}</Action>
    </View>
  </>
}

function EvidenceLink({ evidence, onEvidence }: { evidence: Evidence[]; onEvidence: (evidence: Evidence[]) => void }) {
  if (!evidence.length) return null
  return <Button className='summary-link ui-button' hoverClass='none'
    onClick={() => onEvidence(evidence)}>查看出处</Button>
}

function Summary({ record, wrongCount, onReview, onEvidence, retrying, loadError, retryError, onRetry, onRefresh }: {
  record: LearningRecord
  wrongCount: number
  onReview: (id: string) => void
  onEvidence: (evidence: Evidence[]) => void
  retrying: boolean
  loadError: string
  retryError: string
  onRetry: () => void
  onRefresh: () => void
}) {
  const { aiSummary } = record
  const ready = aiSummary.status === 'SUCCEEDED' && aiSummary.content
  return <View className='result-summary' aria-live='polite'>
    <View className='summary-heading'>
      <Text className='h2'>AI 学习总结</Text>
    </View>
    {pendingSummary(aiSummary) && <View className='summary-loading'><View className='summary-loading-dot' /><Text>正在整理学习总结</Text></View>}
    {aiSummary.status === 'FAILED' && <>
      <Text className='summary-copy'>{aiSummary.error?.retryable
        ? '这次总结暂时没生成。可以重试，基础结果已保留。'
        : '这次总结没有生成。基础结果和错题仍然可以查看。'}</Text>
      {retryError && <ErrorNote>{retryError}</ErrorNote>}
      {aiSummary.error?.retryable && <Action secondary disabled={retrying} onClick={onRetry}>
        {retrying ? '正在重试…' : '重试生成总结'}
      </Action>}
    </>}
    {loadError && <View className='summary-error'>
      <ErrorNote>{loadError}</ErrorNote><Action secondary onClick={onRefresh}>重新检查总结</Action>
    </View>}
    {ready && <>
      <SummaryList title='知识回顾' entries={ready.knowledgeReview} record={record} onReview={onReview} onEvidence={onEvidence} />
      {wrongCount > 0
        ? <SummaryList title='易错点' entries={ready.mistakeNotes} record={record} onReview={onReview} onEvidence={onEvidence} />
        : <View className='summary-section'><Text className='h3'>作答回顾</Text>
          <Text className='summary-copy review-record-empty'>这组题全部答对，可以用下面的建议再巩固一下。</Text>
        </View>}
      <SummaryList title={wrongCount > 0 ? '复习建议' : '巩固建议'} entries={ready.reviewSuggestions}
        record={record} onReview={onReview} onEvidence={onEvidence} />
    </>}
  </View>
}

function SummaryList({ title, entries, record, onReview, onEvidence }: {
  title: string
  entries: SummaryEntry[]
  record: LearningRecord
  onReview: (id: string) => void
  onEvidence: (evidence: Evidence[]) => void
}) {
  return <View className='summary-section'>
    <Text className='h3'>{title}</Text>
    <View className='summary-list'>
      {entries.map((entry, index) => {
        const references = record.answers.filter(({ question }) => entry.questionIds.includes(question.questionId))
        const evidenceReference = references.length === 0
          ? record.answers.find(({ evidence }) => evidence.some((item) => entry.evidenceIds.includes(item.evidenceId)))
          : undefined
        const answers = evidenceReference ? [evidenceReference] : references
        const questionSourceIds = new Set(answers.flatMap(({ evidence }) => evidence.map(({ evidenceId }) => evidenceId)))
        const evidence = (record.takeawayEvidence ?? []).filter(({ evidenceId }) =>
          entry.evidenceIds.includes(evidenceId) && !questionSourceIds.has(evidenceId))
        return <View className='summary-item' key={index}>
          <Text className='bullet' aria-hidden>•</Text>
          <View className='summary-item-copy'>
            <Text>{entry.text}</Text>
            {answers.map(({ question }) => <Button className='summary-link ui-button' key={question.questionId}
              hoverClass='none' onClick={() => onReview(question.questionId)}>查看第 {question.index + 1} 题</Button>)}
            <EvidenceLink evidence={evidence} onEvidence={onEvidence} />
          </View>
        </View>
      })}
    </View>
  </View>
}
