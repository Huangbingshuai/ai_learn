export const FIRST_POLL_DELAY = 2000
export const RESTART_CUTOFF_MS = 30 * 60 * 1000

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

export function generationStep(phase?: string | null): number {
  return phase === 'GENERATING' ? 1 : 0
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
