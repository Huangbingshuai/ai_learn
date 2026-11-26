import { Button, Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { messageForError } from '../api/request'
import { Action, ErrorNote, StatusPanel } from '../components/comic'

type Favorite = { fingerprint: string; topicKey: string; topicTitle: string }

export function FavoritesScreen({ onOpen, onQuestionBank }: { onOpen: (fingerprints: string[]) => void; onQuestionBank: () => void }) {
  const [items, setItems] = useState<Favorite[]>()
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let active = true
    setError('')
    api.favorites().then((page) => { if (active) setItems(page.items) }).catch((problem) => { if (active) setError(messageForError(problem)) })
    return () => { active = false }
  }, [reload])
  if (error) return <StatusPanel title='暂时无法查看收藏夹'><ErrorNote>{error}</ErrorNote><Action onClick={() => setReload((n) => n + 1)}>重新加载</Action></StatusPanel>
  if (!items) return <StatusPanel title='正在加载收藏夹…' />
  if (!items.length) return <StatusPanel title='还没有收藏题目'><Action onClick={onQuestionBank}>去题库</Action></StatusPanel>
  const groups = new Map<string, Favorite[]>()
  items.forEach((item) => groups.set(item.topicKey, [...(groups.get(item.topicKey) ?? []), item]))
  return <View className='topic-library-list'>{[...groups.values()].map((group) => <Button key={group[0].topicKey}
    className='mistake-topic-card favorite-topic-card ui-button' hoverClass='press-offset' onClick={() => onOpen(group.map((item) => item.fingerprint))}>
    <Text className='mistake-topic-title'>{group[0].topicTitle}</Text><Text className='favorite-topic-badge'>{group.length} 题</Text><Text className='mistake-topic-arrow'>›</Text>
  </Button>)}</View>
}
