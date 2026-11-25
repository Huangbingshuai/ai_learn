import { defineConfig, type UserConfigExport } from '@tarojs/cli'

export default defineConfig<'webpack5'>(() => {
  const development = process.env.NODE_ENV === 'development'
  const apiBaseUrl = (process.env.QINGZHI_API_BASE_URL || process.env.TARO_APP_API_BASE_URL
    || (development ? 'http://127.0.0.1:8000' : '')).trim().replace(/\/+$/, '')
  if (apiBaseUrl) {
    const url = new URL(apiBaseUrl)
    if ((url.protocol !== 'https:' && (!development || url.protocol !== 'http:'))
      || url.username || url.password || url.search || url.hash) {
      throw new Error('QINGZHI_API_BASE_URL must be an HTTPS base URL (development also accepts HTTP).')
    }
  } else if (!development) {
    throw new Error('QINGZHI_API_BASE_URL is required for a production build.')
  }
  const devAuth = development && (process.env.QINGZHI_DEV_AUTH || process.env.TARO_APP_DEV_AUTH) === '1'

  return {
    projectName: 'qingzhi-weapp',
    date: '2026-11-25',
    designWidth: 375,
    deviceRatio: { 375: 2 },
    sourceRoot: 'src',
    outputRoot: 'dist',
    defineConstants: {
      __QINGZHI_API_BASE_URL__: JSON.stringify(apiBaseUrl),
      __QINGZHI_DEV_AUTH__: JSON.stringify(devAuth),
    },
    framework: 'react',
    compiler: 'webpack5',
    cache: { enable: false },
    logger: { quiet: false, stats: false },
    mini: {
      webpackChain(chain) {
        chain.plugin('providerPlugin').tap((args) => {
          delete args[0].requestAnimationFrame
          delete args[0].cancelAnimationFrame
          return args
        })
      },
      imageUrlLoaderOption: { limit: true },
      postcss: {
        pxtransform: { enable: true, config: {} },
        cssModules: { enable: false },
      },
    },
  } satisfies UserConfigExport<'webpack5'>
})
