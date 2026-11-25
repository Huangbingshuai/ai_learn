import { Button, Image, Input, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { messageForError } from '../api/request'
import type { ProfileStats, RecordItem, UserProfile } from '../api/types'
import { Action, ComicRow, ErrorNote, Sheet, StatusPanel } from '../components/comic'
import { formatCompletionDate } from './flow'

const profileStorageKey = (userId: string | undefined, field: 'signature' | 'avatar') =>
  `qingzhi.profile.${userId ?? 'unresolved'}.${field}`

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
  const [signature, setSignature] = useState('')
  const [avatarUrl, setAvatarUrl] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setNickname(profile?.nickname ?? '')
    setSignature(Taro.getStorageSync<string>(profileStorageKey(profile?.userId, 'signature')) || '')
    setAvatarUrl(Taro.getStorageSync<string>(profileStorageKey(profile?.userId, 'avatar')) || '')
  }, [profile?.userId, profile?.nickname])

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

  async function saveProfile() {
    setSaving(true)
    setSaved(false)
    setError('')
    try {
      await onProfile(nickname.trim() || null)
      Taro.setStorageSync(profileStorageKey(profile?.userId, 'signature'), signature.trim())
      setEditing(false)
      setSaved(true)
    } catch (problem) {
      setError(messageForError(problem))
    } finally {
      setSaving(false)
    }
  }

  function chooseAvatar(event: { detail?: { avatarUrl?: string } }) {
    const nextAvatar = event.detail?.avatarUrl
    if (!nextAvatar) return
    setAvatarUrl(nextAvatar)
    Taro.setStorageSync(profileStorageKey(profile?.userId, 'avatar'), nextAvatar)
  }

  const displayName = profile?.nickname || '学习者'
  return <>
    <View className='profile-identity'>
      <Button className='profile-avatar-picker ui-button' openType='chooseAvatar' hoverClass='press-offset' onChooseAvatar={chooseAvatar}>
        {avatarUrl ? <Image className='profile-avatar-image' src={avatarUrl} mode='aspectFill' />
          : <Text className='profile-avatar-letter'>{displayName.slice(0, 1)}</Text>}
        <Text className='profile-avatar-edit' aria-hidden>✎</Text>
      </Button>
      <View className='profile-identity-copy'>
        <View className='profile-name-row'>
          <Text className='profile-display-name'>{displayName}</Text>
          <Button className='profile-edit-link ui-button' hoverClass='none' onClick={() => setEditing(true)}>编辑资料</Button>
        </View>
        <Button className='profile-signature ui-button' hoverClass='none' onClick={() => setEditing(true)}>
          {signature || '写一句签名，介绍一下自己'}<Text className='profile-signature-mark' aria-hidden>✎</Text>
        </Button>
      </View>
    </View>
    {stats ? <View className='profile-stats'>
      <View className='profile-stat'><Text className='profile-stat-value'>{stats.completedSets}</Text><Text className='profile-stat-label'>闯关次数</Text></View>
      <View className='profile-stat'><Text className='profile-stat-value'>{stats.correctAnswers}</Text><Text className='profile-stat-label'>答对题数</Text></View>
      <View className='profile-stat'><Text className='profile-stat-value'>{stats.accuracyPercent}%</Text><Text className='profile-stat-label'>平均正确率</Text></View>
    </View> : <StatusPanel title={error ? '学习统计暂不可用' : '正在加载学习统计…'} />}
    <View className='profile-wallet'>
      <View><Text className='profile-wallet-title'>金币</Text><Text className='profile-wallet-note'>答题积攒的学习奖励</Text></View>
      <View className='profile-wallet-value'><Text className='profile-coin-icon' aria-hidden>★</Text><Text>{profile?.coinBalance ?? 0}</Text></View>
    </View>
    {saved && <Text className='profile-success'>资料已保存</Text>}
    <View className='profile-records-head'>
      <Text className='h2'>最近学习</Text>
      <Button className='profile-link ui-button' hoverClass='none' onClick={onHistory}>全部记录 ›</Button>
    </View>
    {recentRecords.length ? <View className='comic-row-list history-list'>
      {recentRecords.map((record) => <ComicRow key={record.recordId}
        secondary={`${formatCompletionDate(record.completedAt)} · 答对 ${record.correctCount}/${record.totalQuestions}`}
        onClick={() => onRecord(record.recordId)}>{record.topicTitle}</ComicRow>)}
    </View> : <Text className='profile-record-empty'>{error ? '' : '完成练习后，学习足迹会出现在这里。'}</Text>}
    {error && <View className='profile-error'><ErrorNote>{error}</ErrorNote><Action secondary onClick={() => setReload((value) => value + 1)}>重新加载</Action></View>}
    {editing && <Sheet title='编辑个人资料' onClose={() => setEditing(false)}>
      <View className='profile-editor'>
        <Button className='profile-editor-avatar ui-button' openType='chooseAvatar' hoverClass='press-offset' onChooseAvatar={chooseAvatar}>
          {avatarUrl ? <Image className='profile-avatar-image' src={avatarUrl} mode='aspectFill' />
            : <Text className='profile-avatar-letter'>{displayName.slice(0, 1)}</Text>}
          <Text className='profile-avatar-edit'>更换头像</Text>
        </Button>
        <Text className='profile-editor-label'>昵称</Text>
        <Input className='profile-input' type='text' value={nickname} maxlength={30}
          placeholder='给自己起个名字' onInput={(event) => setNickname(event.detail.value)} />
        <Text className='profile-editor-label'>个性签名</Text>
        <Input className='profile-input' type='text' value={signature} maxlength={40}
          placeholder='写一句你想展示的话' onInput={(event) => setSignature(event.detail.value)} />
        <Action disabled={saving} onClick={() => { void saveProfile() }}>{saving ? '保存中…' : '保存资料'}</Action>
      </View>
    </Sheet>}
  </>
}
