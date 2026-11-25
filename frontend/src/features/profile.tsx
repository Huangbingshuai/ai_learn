import { Button, Input, Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { messageForError } from '../api/request'
import type { ProfileStats, RecordItem, UserProfile } from '../api/types'
import { Action, ComicRow, ErrorNote, StatusPanel } from '../components/comic'
import { formatCompletionDate } from './flow'

export function ProfileScreen({ profile, onProfile, onHistory, onRecord }: {
  profile?: UserProfile
  onProfile: (nickname: string | null) => Promise<UserProfile>
  onHistory: () => void
  onRecord: (recordId: string) => void
}) {
  const [stats, setStats] = useState<ProfileStats>()
  const [recentRecords, setRecentRecords] = useState<RecordItem[]>([])
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [nickname, setNickname] = useState(profile?.nickname ?? '')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => setNickname(profile?.nickname ?? ''), [profile?.nickname])

  useEffect(() => {
    let active = true
    setError('')
    Promise.all([api.profileStats(), api.records()]).then(([nextStats, page]) => {
      if (!active) return
      setStats(nextStats)
      setRecentRecords(page.items.slice(0, 3))
    }).catch((problem) => {
      if (active) setError(messageForError(problem))
    })
    return () => { active = false }
  }, [reload])

  async function saveNickname() {
    setSaving(true)
    setSaved(false)
    setError('')
    try {
      await onProfile(nickname.trim() || null)
      setSaved(true)
    } catch (problem) {
      setError(messageForError(problem))
    } finally {
      setSaving(false)
    }
  }

  const displayName = profile?.nickname || '学习者'
  return <>
    <View className='profile-hero'>
      <View className='profile-avatar'>{displayName.slice(0, 1)}</View>
      <Text className='profile-display-name'>{displayName}</Text>
      <Text className='profile-motto'>每天闯关一点点，进步看得见</Text>
    </View>
    {stats ? <View className='profile-stats'>
      <View className='profile-stat'><Text className='profile-stat-value'>{stats.completedSets}</Text><Text className='profile-stat-label'>闯关次数</Text></View>
      <View className='profile-stat'><Text className='profile-stat-value'>{stats.correctAnswers}</Text><Text className='profile-stat-label'>答对题数</Text></View>
      <View className='profile-stat'><Text className='profile-stat-value'>{stats.accuracyPercent}%</Text><Text className='profile-stat-label'>平均正确率</Text></View>
    </View> : <StatusPanel title={error ? '学习统计暂不可用' : '正在加载学习统计…'} />}
    <View className='profile-wallet'>
      <View><Text className='profile-wallet-title'>金币</Text><Text className='profile-wallet-note'>金币按账号累计</Text></View>
      <Text className='profile-wallet-value'>◈ {profile?.coinBalance ?? 0}</Text>
    </View>
    <View className='profile-edit-card'>
      <Text className='h2'>个人资料</Text>
      <View className='profile-edit-row'>
        <Input className='profile-input' type='nickname' value={nickname} maxlength={30}
          placeholder='设置昵称（可选）' onInput={(event) => setNickname(event.detail.value)} />
        <Button className='profile-save ui-button' hoverClass='press-offset' disabled={saving} onClick={() => { void saveNickname() }}>
          {saving ? '保存中' : '保存'}
        </Button>
      </View>
      {saved && <Text className='profile-success'>昵称已保存</Text>}
    </View>
    <View className='profile-records-head'>
      <Text className='h2'>最近学习</Text>
      <Button className='profile-link ui-button' hoverClass='none' onClick={onHistory}>全部记录 ›</Button>
    </View>
    {recentRecords.length ? <View className='comic-row-list history-list'>
      {recentRecords.map((record) => <ComicRow key={record.recordId}
        secondary={`${formatCompletionDate(record.completedAt)} · 答对 ${record.correctCount}/${record.totalQuestions}`}
        onClick={() => onRecord(record.recordId)}>{record.topicTitle}</ComicRow>)}
    </View> : <Text className='profile-record-empty'>{error ? '' : '完成练习后，记录会显示在这里。'}</Text>}
    {error && <View className='profile-error'><ErrorNote>{error}</ErrorNote><Action secondary onClick={() => setReload((value) => value + 1)}>重新加载</Action></View>}
  </>
}
