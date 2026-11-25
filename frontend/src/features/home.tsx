import { Button, Image, Input, Text, View } from '@tarojs/components'
import { useEffect, useState } from 'react'
import { api, type PendingGeneration, type RestartGroup } from '../api/client'
import { messageForError } from '../api/request'
import type { Topic } from '../api/types'
import cover from '../assets/cover.webp'
import { Action, ErrorNote, Sheet } from '../components/comic'
import type { UserProfile } from '../api/types'
import { generationPhases, RESTART_CUTOFF_MS, restartAvailable, topicInputError, topicLength } from './flow'

export function HomeScreen({
  input, error, restart, pending, profile, onProfile, onEdit, onError, onGenerate, onTopic, onPending, onFixedQuiz, onRestart,
}: {
  input: string
  error: string
  restart?: RestartGroup
  pending: PendingGeneration[]
  profile?: UserProfile
  onProfile: (nickname: string | null) => Promise<UserProfile>
  onEdit: (input: string) => void
  onError: (message: string) => void
  onGenerate: (topic: string) => void
  onTopic: (topic: Topic) => void
  onPending: (intent: PendingGeneration) => void
  onFixedQuiz: () => void
  onRestart: (group: RestartGroup) => void
}) {
  const [topics, setTopics] = useState<Topic[]>()
  const [topicLoadError, setTopicLoadError] = useState('')
  const [nextCursor, setNextCursor] = useState<string>()
  const [loadingMore, setLoadingMore] = useState(false)
  const [reload, setReload] = useState(0)
  const [now, setNow] = useState(Date.now)
  const [profileOpen, setProfileOpen] = useState(false)
  const [nickname, setNickname] = useState(profile?.nickname ?? '')
  const [profileError, setProfileError] = useState('')
  const [savingProfile, setSavingProfile] = useState(false)
  const [expandedTopics, setExpandedTopics] = useState(false)
  const [managingTopics, setManagingTopics] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Topic>()
  const [deletingTopic, setDeletingTopic] = useState(false)
  const canRestart = restart && restartAvailable(restart.quizExpiresAt, now)

  useEffect(() => setNickname(profile?.nickname ?? ''), [profile?.nickname])

  async function saveNickname() {
    setSavingProfile(true)
    setProfileError('')
    try {
      await onProfile(nickname.trim() || null)
      setProfileOpen(false)
    } catch {
      setProfileError('保存失败，请检查网络后重试。')
    } finally {
      setSavingProfile(false)
    }
  }

  useEffect(() => {
    let active = true
    setTopicLoadError('')
    api.topics().then(({ items, nextCursor }) => {
      if (!active) return
      setTopics(items)
      setNextCursor(nextCursor ?? undefined)
    }).catch((problem: unknown) => {
      if (active) setTopicLoadError(messageForError(problem))
    })
    return () => { active = false }
  }, [reload])

  useEffect(() => {
    if (!restart) return
    const wait = Date.parse(restart.quizExpiresAt) - Date.now() - RESTART_CUTOFF_MS
    if (wait <= 0) return
    const timer = setTimeout(() => setNow(Date.now()), wait)
    return () => clearTimeout(timer)
  }, [restart])

  function submitTopic() {
    const problem = topicInputError(input)
    if (problem) onError(problem)
    else onGenerate(input.trim())
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return
    setLoadingMore(true)
    setTopicLoadError('')
    try {
      const result = await api.topics(nextCursor)
      setTopics((previous) => {
        const shown = new Set(previous?.map((entry) => entry.topicKey))
        return [...(previous ?? []), ...result.items.filter((entry) => !shown.has(entry.topicKey))]
      })
      setNextCursor(result.nextCursor ?? undefined)
    } catch (problem) {
      setTopicLoadError(messageForError(problem))
    } finally {
      setLoadingMore(false)
    }
  }

  async function deleteTopic() {
    if (!deleteTarget || deletingTopic) return
    setDeletingTopic(true)
    setTopicLoadError('')
    try {
      await api.deleteTopic(deleteTarget.topicKey)
      setDeleteTarget(undefined)
      setReload((version) => version + 1)
    } catch (problem) {
      setTopicLoadError(messageForError(problem))
    } finally {
      setDeletingTopic(false)
    }
  }

  const visibleTopics = topics?.slice(0, expandedTopics ? undefined : 4)

  return <>
    <View className='comic-cover'>
      <Image className='cover-art' src={cover} mode='widthFix' aria-label='翻开的书里跳出一颗地球，戴眼镜的学生在旁边发问' />
      <View className='cover-title'>
        <Text>今天想弄懂</Text>
        <Text className='cover-emphasis'>什么？</Text>
      </View>
      <View className='cover-dialogue'>
        <Text>地球忘带</Text>
        <Text>校服啦？</Text>
      </View>
    </View>
    <Text className='home-caption'>一组 12 道题，每题都有讲解</Text>
    {profile && <Button className='account-strip ui-button' hoverClass='press-offset' onClick={() => setProfileOpen(true)}>
      <View className='account-avatar' aria-hidden>知</View>
      <View className='account-name'><Text>{profile.nickname || '学习者'}</Text><Text className='account-hint'>个人资料</Text></View>
      <Text className='coin-balance'>◈ {profile.coinBalance}</Text>
    </Button>}
    <View className='topic-form'>
      <View className='topic-field'>
        <Input
          className={`topic-input${error ? ' invalid' : ''}`}
          type='text'
          value={input}
          maxlength={-1}
          confirmType='go'
          placeholder='输入主题，例如「为什么会有四季」'
          placeholderStyle='color: #60566c'
          aria-label='学习主题'
          onInput={(event) => onEdit(event.detail.value)}
          onConfirm={submitTopic}
        />
        <Text className='char-count'>{topicLength(input)} / 80</Text>
      </View>
      {error && <ErrorNote>{error}</ErrorNote>}
      <Action onClick={submitTopic}>生成练习</Action>
    </View>
    {(pending.length > 0 || restart) && <View className='home-continue'>
      {pending.map((intent) => <Button
        className='continue-row ui-button'
        hoverClass='press-offset'
        key={intent.key}
        onClick={() => onPending(intent)}
      >
        <View className='continue-copy'>
          <Text className='continue-title'>{intent.title}</Text>
          <Text className='continue-meta'>
            {intent.status === 'SUCCEEDED'
              ? '已生成 · 开始答题'
              : intent.phase ? generationPhases[intent.phase] : '正在出题'}
          </Text>
        </View>
        <Text className='continue-arrow' aria-hidden>›</Text>
      </Button>)}
      {restart && <Button
        className='continue-row is-restart ui-button'
        hoverClass='press-offset'
        onClick={() => onRestart(restart)}
      >
        <View className='continue-copy'>
          <Text className='continue-title'>{restart.topicTitle}</Text>
          <Text className='continue-meta'>
            {!canRestart ? '练习已到期' : restart.attemptId && typeof restart.answeredCount === 'number' && typeof restart.totalQuestions === 'number'
              ? `已答 ${restart.answeredCount}/${restart.totalQuestions} 题` : '点击开始练习'}
          </Text>
        </View>
        <Text className='continue-arrow' aria-hidden>›</Text>
      </Button>}
    </View>}
    <View className='topic-picker'>
      <View className='topic-heading'>
        <Text className='h2'>已保存主题</Text>
        {topics && <Button className='topic-manage ui-button' hoverClass='none'
          onClick={() => setManagingTopics((value) => !value)}>{managingTopics ? '完成' : '管理'}</Button>}
      </View>
      {topics && <View className='topic-options'>
        {visibleTopics?.map((topic) => <View className='topic-card' key={topic.topicKey}>
          <Button
            className={`topic ui-button ${topic.art === 'topic' ? 'saved-topic' : topic.art}${managingTopics ? ' is-managing' : ''}`}
            hoverClass='press-offset'
            onClick={() => onTopic(topic)}
          >
            <View className='topic-symbol' aria-hidden />
            <Text className='topic-title'>{topic.title}</Text><Text className='topic-arrow' aria-hidden>›</Text>
          </Button>
          {managingTopics && topic.canDelete && <Button className='topic-delete ui-button' hoverClass='row-press'
            aria-label={`删除主题${topic.title}`} onClick={() => setDeleteTarget(topic)}>×</Button>}
        </View>)}
      </View>}
      {!topics && (topicLoadError ? <View className='topic-load-error'>
        <ErrorNote>{topicLoadError}</ErrorNote>
        <Action secondary onClick={() => setReload((version) => version + 1)}>重新加载主题</Action>
      </View> : <Text className='stage-note'>正在加载主题…</Text>)}
      {topics?.length === 0 && <Text className='stage-note'>出题成功后，主题会出现在这里。</Text>}
      {topics && topicLoadError && <ErrorNote>{topicLoadError}</ErrorNote>}
      {topics && topics.length > 4 && <View className='topic-load-more'>
        <Action secondary onClick={() => setExpandedTopics((value) => !value)}>
          {expandedTopics ? '收起主题' : `展开全部 ${topics.length}${nextCursor ? '+' : ''} 项`}
        </Action>
      </View>}
      {expandedTopics && nextCursor && <View className='topic-load-more'>
        <Action secondary disabled={loadingMore} onClick={() => { void loadMore() }}>
          {loadingMore ? '正在加载…' : '查看更多主题'}
        </Action>
      </View>}
    </View>
    {deleteTarget && <Sheet title='删除已保存主题？' onClose={() => setDeleteTarget(undefined)}>
      <Text className='body-copy'>将从主题列表中移除「{deleteTarget.title}」。已有学习记录会保留。</Text>
      <View className='topic-delete-actions'>
        <Action secondary disabled={deletingTopic} onClick={() => setDeleteTarget(undefined)}>取消</Action>
        <Action disabled={deletingTopic} onClick={() => { void deleteTopic() }}>{deletingTopic ? '正在删除…' : '确认删除'}</Action>
      </View>
    </Sheet>}
    {__QINGZHI_DEV_FIXED_QUIZ__ && <Action secondary onClick={onFixedQuiz}>打开开发固定题组</Action>}
    {profileOpen && <Sheet title='我的资料' onClose={() => setProfileOpen(false)}>
      <View className='profile-form'>
        <Text className='profile-label'>昵称</Text>
        <Input className='profile-input' type='nickname' value={nickname} maxlength={30}
          placeholder='可选，默认显示「学习者」' onInput={(event) => setNickname(event.detail.value)} />
        <View className='profile-wallet'><Text>金币余额</Text><Text className='coin-balance'>◈ {profile?.coinBalance ?? 0}</Text></View>
        {profileError && <ErrorNote>{profileError}</ErrorNote>}
        <Action disabled={savingProfile} onClick={() => { void saveNickname() }}>{savingProfile ? '正在保存…' : '保存昵称'}</Action>
        <Text className='profile-note'>金币按账号累计。每道相同题目只结算首次作答。</Text>
      </View>
    </Sheet>}
  </>
}
