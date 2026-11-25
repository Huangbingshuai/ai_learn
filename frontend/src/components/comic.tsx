import { Button, ScrollView, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import type { PropsWithChildren, ReactNode } from 'react'
import type { Evidence } from '../api/types'
import { TAB_DESTINATIONS } from '../features/mistakes'

type ActionProps = {
  children: ReactNode
  onClick: () => void
  secondary?: boolean
  disabled?: boolean
}

export function Action({ children, onClick, secondary = false, disabled = false }: ActionProps) {
  return <Button
    className={`action ui-button${secondary ? ' secondary' : ''}${disabled ? ' is-disabled' : ''}`}
    disabled={disabled}
    hoverClass={disabled ? 'none' : 'press-offset'}
    onClick={onClick}
  >
    <Text className='action-label'>{children}</Text>
    <View className='action-pattern' aria-hidden />
    <View className='action-arrow' aria-hidden />
  </Button>
}

export function Tape({ children, tone = '' }: PropsWithChildren<{ tone?: 'yellow' | 'paper' | 'mint' | '' }>) {
  return <Text className={`tape ${tone}`}>{children}</Text>
}

export function ComicRow({ children, secondary, mint, onClick }: PropsWithChildren<{
  secondary?: string
  mint?: boolean
  onClick: () => void
}>) {
  return <Button className={`comic-row ui-button${mint ? ' mint' : ''}`} hoverClass='row-press' onClick={onClick}>
    <View className='comic-row-copy'>
      <Text>{children}</Text>
      {secondary && <Text className='row-subtitle'>{secondary}</Text>}
    </View>
    <Tape tone='mint'>›</Tape>
  </Button>
}

export function StatusPanel({ title, children }: PropsWithChildren<{ title: string }>) {
  return <View className='empty-panel'>
    <Text className='h1'>{title}</Text>
    {children}
  </View>
}

export function ErrorNote({ children }: PropsWithChildren) {
  return <Text className='field-error' aria-live='polite'>{children}</Text>
}

export function EvidenceSheet({ evidence, onClose }: { evidence: Evidence[]; onClose: () => void }) {
  async function copyLink(url: string) {
    try {
      await Taro.setClipboardData({ data: url })
    } catch {
      void Taro.showToast({ title: '暂时无法复制，请再试。', icon: 'none' })
    }
  }

  return <Sheet title='题目依据' onClose={onClose}>
    {evidence.map((item) => <View className='evidence-item' key={item.evidenceId}>
      <Text className='h3'>{item.sourceTitle}</Text>
      <Text className='source-caption'>来源 · {item.sourceOrganization}</Text>
      <Text className='source-quote' userSelect>{item.text}</Text>
      <Button className='source-link ui-button' hoverClass='none' onClick={() => { void copyLink(item.sourceUrl) }}>
        复制出处链接 ↗
      </Button>
    </View>)}
  </Sheet>
}

export function Sheet({ title, onClose, children }: PropsWithChildren<{ title: string; onClose: () => void }>) {
  return <View className='sheet-overlay'>
    <View className='sheet-mask' onClick={onClose} />
    <View className='sheet' aria-role='dialog' aria-label={title}>
      <View className='sheet-header'>
        <Text className='h2'>{title}</Text>
        <Button className='sheet-close ui-button' hoverClass='row-press' aria-label='关闭' onClick={onClose}>×</Button>
      </View>
      <ScrollView className='sheet-content' scrollY>{children}</ScrollView>
    </View>
  </View>
}

export function getWindowLayout() {
  const { windowWidth, windowHeight, screenHeight, statusBarHeight = 0, safeArea } = Taro.getWindowInfo()
  const menu = Taro.getMenuButtonBoundingClientRect()
  const scale = windowWidth / 375
  const menuAvailable = menu.width > 0 && menu.bottom > menu.top
  const headerHeight = Math.max(statusBarHeight + 53 * scale, menuAvailable ? menu.bottom + 5 * scale : 0)
  return {
    windowHeight,
    headerHeight,
    headerRight: menuAvailable ? Math.max(windowWidth - menu.left + 8 * scale, 17 * scale) : 17 * scale,
    statusBarHeight,
    safeBottom: safeArea ? Math.max(0, screenHeight - safeArea.bottom) : 0,
    tabHeight: 50 * scale,
    screenInset: 17 * scale,
  }
}

export function Header({ title, layout, onBack }: {
  title: string
  layout: ReturnType<typeof getWindowLayout>
  onBack?: () => void
}) {
  return <View className='device-header' style={{
    height: `${layout.headerHeight}px`,
    paddingTop: `${layout.statusBarHeight}px`,
    paddingRight: `${layout.headerRight}px`,
  }}>
    {onBack && <Button className='back ui-button' hoverClass='none' aria-label='返回' onClick={onBack}>‹</Button>}
    {title === '轻知闯关' ? (
      <View className='home-brand'>
        <View className='brand-main'><Text className='brand-letter brand-light'>轻</Text><Text className='brand-letter brand-know'>知</Text></View>
        <Text className='brand-tag'>闯关</Text>
      </View>
    ) : (
      <Text className='screen-title'>{title}</Text>
    )}
  </View>
}

export function Tabs({ current, safeBottom, onNavigate }: {
  current: typeof TAB_DESTINATIONS[number]['key']
  safeBottom: number
  onNavigate: (next: typeof TAB_DESTINATIONS[number]['key']) => void
}) {
  return <View className='tabs' style={{ paddingBottom: `${safeBottom}px` }}>
    {TAB_DESTINATIONS.map(({ key, label }) => <Button key={key}
      className='tab ui-button' hoverClass='none' onClick={() => onNavigate(key)}>
      <Text className={current === key ? 'tab-label current' : 'tab-label'}>{label}</Text>
    </Button>)}
  </View>
}
