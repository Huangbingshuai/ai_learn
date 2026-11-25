> **后续变更（2026-11-25）**：默认生成模型已从 DeepSeek Flash 切换为阿里云百炼 `qwen-flash`，可通过 `AI_PROVIDER=deepseek` 回退；本文的 DeepSeek 描述保留为当时的开发计划记录。当前方案以[方案设计文档](../../方案设计文档.md)和[运行与验收](../../运行与验收.md)为准。

# 轻知闯关 MVP 开发计划

**目标**　交付微信小程序、FastAPI 服务和独立生成进程，跑通选主题、12 题作答、证据讲解、通关、历史和 AI 学习总结。

**架构**　前端以原型小程序区域为视觉基准，复用漫画插图，通过 REST API 交互。API 在 PostgreSQL 中保存会话、答题和作业状态。独立进程使用 LangChain 调用 DeepSeek，保存通过校验的题组和学习总结。

**技术栈**　Taro 4、React、TypeScript、FastAPI、Uvicorn、Pydantic 2、PostgreSQL、SQLAlchemy 2、Alembic、LangChain、DeepSeek、pytest。

**依据**　[最终版本需求分析文档](../../最终版本需求分析文档.md)、[方案设计文档](../../方案设计文档.md)、[漫画融合版原型](../../../prototype/index.html)。

## 已确认的产品决策

1. 首发主题为「四季成因」和「月相变化」，沿用原型题卡、显示名与常见别名，并维护带出处的版本化中文证据。[NASA 四季成因](https://spaceplace.nasa.gov/seasons/en/) · [NASA 月相](https://science.nasa.gov/moon/moon-phases/)
2. 保留「题目依据」弹层，在小程序内显示证据片段、来源机构及标题，并提供「复制出处链接」。
3. 使用官方 Taro 脚手架的 React TypeScript 模板生成初始工程。PostgreSQL 由 Docker Compose 运行。Taro、FastAPI 和 Python 工作进程在宿主机运行。
4. 本轮用真实 DeepSeek 验证出题与总结。微信登录通过模拟供应商响应验证，真实微信登录留待后续联调。启动和验收命令见 [运行与验收](../../运行与验收.md)。

## 共用约束

- 前端复用封面和学生插图，以原型的色板、字体层级、边框、阴影、槽位、间距、按钮、导航及状态作为截图基准。应用头部适配微信真实胶囊及安全区。浏览器工作台、评审面板、模拟开关和 P1 草图用于原型评审。题目、讲解与总结的 AI 标识随真实产物展示。
- 产品规则由后端执行。新生成题组恰好 12 题，至少各有一道单选、多选和判断。答完即通关。首次作答回执不可改写。结果、历史与总结仅归属用户本人。历史依赖完成快照，题组过期后仍可回看。
- 题组按方案建议保留 24 小时，距到期不足 30 分钟提示重新生成。退出时同设备保留重开所需的题组标识与到期时间，重开从第 1 题开始。服务器仍对有效期作最终校验。
- 每个核心功能的提交都包含实现和相应测试。应用骨架单独提交一次。提交按路径核对，保留现有暂存的 IDE 文件。自动化测试使用独立的真实 PostgreSQL，外部微信与模型接口使用可控替身覆盖成功、超时和失败。真实供应商冒烟单独记录。
- 评审通过后再创建应用、安装依赖和提交功能代码。接口按方案的 `/v1` 契约，从 FastAPI 导出 OpenAPI 生成前端类型。

## 开发顺序与提交

### 0. 工程骨架、数据库和登录

- 建立 `frontend/`、`backend/`，提交依赖锁、环境模板、开发启动入口和首批 Alembic 迁移。FastAPI 统一错误体与请求 ID，Taro 接通构建、路由、请求层和会话管理。
- 实现本地开发会话与微信登录适配。微信一次性 code 由后端交换身份，按 `(app_id, openid)` 建用户，签发有效 12 小时的随机会话令牌，数据库保存摘要。客户端遇到过期合并重登，并保留写操作原幂等键。
- 验证空库迁移、登录成功、无效 code、供应商超时、令牌过期和非开发环境关闭开发入口。运行 `weapp` 构建和接口类型检查。
- 提交 **搭建小程序工程与微信会话**。

### 1. 固定题组、逐题作答与进度（M06）

- 在 `backend/app/domain/` 定义题组、题目和选项结构，用本地导入脚本及开发路由建立固定 12 题。接通尝试创建和当前题接口。开发题组与 AI 题组使用同一判分数据结构。
- 在前端迁移题卡、三类选择控件、提交按钮和 20 格进度条。提交前可改选，当前题的读取接口只返回题干、题型、选项和进度。
- 验证三种题型、未选提交、题序、重复开始、题组过期、鉴权和答题前响应投影。
- 提交 **实现固定题组与逐题作答**。

### 2. 首次判分与逐题反馈（M07）

- 在尝试事务中锁定记录，先读已有回执，再检查题序并判分。单选和判断要求一个有效选项，多选至少一个有效选项，归一化后按正确选项集合完全一致判分。
- 按原型实现锁定选项、答对、答错和漏选状态，展示用户答案、全部正确答案与讲解。下一题必须在用户点击后展示。回执预留证据字段和原型来源弹层入口。
- 验证选项归属、漏选、错选、全部选对、重复请求、并发不同答案、超前题序和网络中断后的同题重试。
- 提交 **实现首次判分与逐题讲解**。

### 3. 通关与即时基础结果（M08、M09）

- 末题在同一事务内保存回执、通关状态和唯一学习记录快照。结果展示主题、完成题数、实际得分、知识要点和错题。按原型实现退出确认、重开及临近到期提示。
- 验证第 19 题没有历史记录，末题重复或并发提交只产生一个 `recordId`，模拟写入失败时事务回滚，全对或低分都能通关，完整 12 题闭环可运行。
- 提交 **实现通关与即时学习结果**。

### 4. 学习记录与错题回看（M10）

- 实现本人历史游标分页、记录详情、错题列表和单题回看。历史和通关页复用基础结果、作答回执和证据展示组件。
- 验证不同用户数据隔离、同时间记录的分页顺序、空态、未完成记录不可见、原题组过期后仍可按快照查看。
- 提交 **实现学习记录与错题回顾**。

### 5. 主题输入与可核对来源（M01、M03、M04）

- 在 `backend/data/topics/` 建立主题、别名和分版本证据。实现主题列表、长度校验、归一化匹配和可选主题提示。前端迁移首页、字数提示和主题卡。
- 答题反馈及历史展示服务端保存的证据片段和出处。出题任务只引用已经绑定的资料版本，历史沿用当时的来源快照。
- 验证空输入、2–80 字边界、别名、标点和空白处理、未匹配、资料不足、版本变更和来源快照。
- 提交 **实现主题匹配与证据查看**。

### 6. 异步生成 12 题（M05）

- 生成请求入队时原子预留额度，通过相同幂等键取回原作业。独立进程使用 `FOR UPDATE SKIP LOCKED` 领取任务，写入可续期租约和令牌，在事务外调用模型。最终保存时核对租约并确保一个作业只产生一个题组。
- 通过 `ChatDeepSeek` 调用默认可配置的 `deepseek-flash`。以 Pydantic 和领域规则检查题量、题型、选项、答案与证据 ID，再调用受约束的质量核验。有限重试和至多一次质量修正共用预算。
- 实现原型的生成进度与失败状态，按 2 到 8 秒退避轮询，离开页面停止，成功进入答题。恢复网络时沿用同一幂等键，用户主动重新生成时使用新键。
- 验证并发入队配额、幂等冲突、双进程抢占、租约失效回收、迟到结果、耗尽预算、空输出、截断、无效引用、重复考点、来源不足、内容拦截及模型暂时故障。
- 提交 **实现有证据的异步 AI 出题**。

### 7. 个性化 AI 学习总结（M16）

- 通关事务原子创建唯一的总结任务，工作进程从本次记录快照生成知识回顾、真实错题的易错点和复习建议，校验题目与证据引用归属。全对提供巩固建议。追加迁移时为已存在的完成记录建立缺失任务。
- 通关页和历史详情先显示基础结果，只在总结待处理时轮询。成功显示 AI 标识与回看链接。失败时保留基础结果，可恢复时允许幂等重试。
- 验证全对、部分错题、虚构引用、把答对题写成错题、摘要任务竞态、跨用户访问、不可恢复错误以及同键重试。
- 提交 **实现通关后的 AI 学习总结**。

## 交付验收

- 从全新数据库迁移到当前版本，运行后端全部单元、API、真实 PostgreSQL 集成、事务并发和工作进程测试，并提供测试数量与覆盖率报告。对关键失败分支增加用例，交付时测试全部通过。
- 生成并校验 OpenAPI 类型，运行前端类型检查和 `weapp` 构建，在微信开发者工具对照原型检查首页、生成成功和失败、三类答题、讲解、出处弹层、通关、历史、错题与总结状态。
- 使用测试配置完成一组真实出题、12 题作答、基础结果、总结及历史回看，另验证微信真实登录。记录测试结果、运行说明、环境配置和 Git 提交清单。

## 已查阅的官方资料

- Taro、React、TypeScript 和微信小程序组件示例。Taro 官方仓库标记的最新发布版为 4.3.3，实际依赖随兼容性验证锁定。[Taro 官方仓库](https://github.com/NervJS/taro) · [React Quick Start](https://react.dev/learn) · [TypeScript strict](https://www.typescriptlang.org/tsconfig/strict.html) · [微信官方小程序示例](https://github.com/wechat-miniprogram/miniprogram-demo)
- FastAPI 使用响应模型提供 OpenAPI，测试走 TestClient 和 pytest。OpenAPI 的 TypeScript 类型由前端生成并检查。[FastAPI 测试](https://fastapi.tiangolo.com/tutorial/testing/) · [FastAPI 与 Uvicorn](https://fastapi.tiangolo.com/deployment/manually/) · [Pydantic Models](https://docs.pydantic.dev/latest/concepts/models/) · [OpenAPI TypeScript](https://openapi-ts.dev/introduction)
- SQLAlchemy 显式事务用于作答和作业更新，PostgreSQL 支持以 `SKIP LOCKED` 领取作业，Alembic 管理迁移。[SQLAlchemy 事务](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html) · [PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html) · [Alembic 教程](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- DeepSeek 官方列出 `deepseek-flash`，JSON Output 使用 `json_object`，需要处理空输出和截断。[DeepSeek 模型](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) · [DeepSeek JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode/) · [LangChain ChatDeepSeek](https://docs.langchain.com/oss/python/integrations/chat/deepseek)
- 微信 `code2Session` 官方接口页在本次 Chrome 会话中被浏览器策略阻止，接口细节还需在真实微信登录开发前核对。[微信 code2Session](https://developers.weixin.qq.com/miniprogram/dev/OpenApiDoc/user-login/code2Session.html)
