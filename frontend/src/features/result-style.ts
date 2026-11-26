export type ResultTier = 'explore' | 'discover' | 'progress' | 'excellent' | 'perfect'

export function resultTier(correct: number, total: number): ResultTier {
  if (total <= 0 || correct < 0 || correct > total) return 'explore'
  if (correct === total) return 'perfect'
  const ratio = correct / total
  if (ratio < .25) return 'explore'
  if (ratio < .5) return 'discover'
  if (ratio < .75) return 'progress'
  return 'excellent'
}

export const resultTitles: Record<ResultTier, string> = {
  explore: '新知识，慢慢来！',
  discover: '已经有收获啦！',
  progress: '越来越有感觉！',
  excellent: '这轮答得很棒！',
  perfect: '全部答对啦！',
}
