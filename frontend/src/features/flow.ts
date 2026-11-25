import type { PendingGeneration } from '../api/client'

export const FIRST_POLL_DELAY = 2000
export const RESTART_CUTOFF_MS = 30 * 60 * 1000
export const PENDING_AGE = 24 * 60 * 60 * 1000
export const PENDING_UNSUBMITTED_AGE = 10 * 60 * 1000

export function pendingIdentity(entry: PendingGeneration): string {
  return 'topicKey' in entry.input
    ? `key:${entry.input.topicKey}`
    : `topic:${entry.input.topic.trim().replace(/\s+/g, ' ').toLowerCase()}`
}

export function prunePendingGenerations(entries: PendingGeneration[], now = Date.now()): PendingGeneration[] {
  const seen = new Set<string>()
  return entries
    .filter((entry) => entry.requestedAt > now - PENDING_AGE && entry.status !== 'FAILED')
    .sort((left, right) => right.requestedAt - left.requestedAt)
    .filter((entry) => {
      const identity = pendingIdentity(entry)
      if (seen.has(identity)) return false
      seen.add(identity)
      return true
    })
}

export const questionTypeLabels = { single: '单选题', multi: '多选题', judge: '判断题' }

export function selectOption(type: keyof typeof questionTypeLabels, selected: string[], optionId: string): string[] {
  if (type !== 'multi') return [optionId]
  return selected.includes(optionId) ? selected.filter((id) => id !== optionId) : [...selected, optionId]
}

export function optionAppearance(optionId: string, selected: string[], correct?: string[]) {
  if (correct?.includes(optionId)) return 'correct'
  if (selected.includes(optionId)) return correct ? 'incorrect' : 'selected'
  return ''
}

export function restartAvailable(expiresAt: string, now = Date.now()): boolean {
  return Date.parse(expiresAt) - now > RESTART_CUTOFF_MS
}

export function topicLength(input: string): number {
  return Array.from(input.trim()).length
}

export function topicInputError(input: string): string | undefined {
  const length = topicLength(input)
  if (length === 0) return '请输入想学的主题，或在下方选一个。'
  if (length < 2 || length > 80) return '主题需要 2–80 字，请调整后再试。'
}

export const generationPhases = {
  RESOLVING_TOPIC: '理解主题',
  READING_SOURCES: '查找和核对资料',
  GENERATING: '生成题目',
  VERIFYING: '核验答案与讲解',
} as const

export function formatElapsed(startedAt: number, now = Date.now()): string {
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes} 分 ${seconds % 60} 秒` : `${seconds} 秒`
}

export function nextPollDelay(delay: number): number {
  return Math.min(Math.round(delay * 1.5), 8000)
}

export function formatCompletionDate(timestamp: string): string {
  const date = new Date(timestamp)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function makeKey(): string {
  // A collision-resistant request identifier, never an authentication credential.
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}
