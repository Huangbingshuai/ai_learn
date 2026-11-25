# 错题本功能开发计划

**目标**　为每个登录用户提供跨练习、可持续维护的错题本，涵盖未完成练习的错答、历史记录回填、手动掌握状态和针对单题的再练；不影响现有单条学习记录中的“错题回顾”。

**架构**　FastAPI 在逐题判分事务中，以用户和语义题目指纹为唯一键维护错题快照；从已完成记录及尚未完成练习的答题回执幂等回填历史。小程序通过 `/v1` API 分页读取错题、查看最近一次错答、切换掌握状态和发起单题再练。练习仍由服务端判题，复用现有答题回执及金币结算规则。

**技术栈**　沿用 Taro 4、React、TypeScript、FastAPI、Pydantic、SQLAlchemy 2、PostgreSQL 16、Alembic、pytest；不增加依赖。语义题目指纹抽到共享领域模块，金币与错题本复用同一规则。

**依据**　[最终版本需求分析文档](../../最终版本需求分析文档.md)、[方案设计文档](../../方案设计文档.md)、[漫画融合版原型](../../../prototype/index.html)。

## 已确认需求与产品规则

1. 错题本是用户级全局列表，未完成练习里已产生的错答也会立即出现；相同语义题目跨练习合并为一项，统计累计错答次数，保留最近一次错答回执、来源主题和时间。题目语义身份沿用金币结算的 fingerprint（题型、归一化题干、选项文本），不依赖题库 ID。
2. 完成历史记录中的错题需回填；保留现有记录详情里的单次错题回顾。回填来源为 `Record.answers` 和没有对应完成 `Record` 的 `AttemptAnswer.receipt`，同一已完成练习不重复统计。重复启动回填不会新增计数或覆盖用户状态。
3. 错题有“待复习”和“已掌握”两种手动状态。用户可双向切换；新答错已掌握的题目时重新置为待复习，答对不会自动标记掌握。
4. 底部增加独立“错题本”入口。列表按最近答错时间倒序，可按状态筛选、按主题过滤，并显示待复习数量、错答次数和上次错答时间；详情展示最近一次作答、标准答案、讲解、证据及来源主题。
5. 用户可从详情再次练习该单题；再练由后端判题并生成回执。此答题不触发通关 AI 总结或额外总结额度；答对不自动移出错题本，答错增加次数、刷新最近错答信息并保持待复习。
6. 本轮不加入删除错题、按知识点分组、批量标记、错题导出、自动复习计划或统计图表。
7. 用户追加确认：底部栏提供“学习 / 题库 / 错题本 / 我的”；已保存主题集中在题库，主页只保留主题输入和未完成练习；“我的”展示完成组数、答对题数、平均正确率、金币、最近记录和昵称编辑；示例固定题组仅在开发构建的题库中显示为“体验示例题组”；题库的可删除主题始终提供可见删除按钮并二次确认。

## 共用约束

- 所有列表、详情、状态变更、回填与练习端点均由会话身份确定 `user_id`，拒绝客户端传入或覆盖所有权。错题行唯一约束是 `(user_id, fingerprint)`。
- 答题回执和错题更新在同一事务内提交。现有答题幂等重放不得重复增加错答次数或金币；并发新错答由数据库唯一约束及原子 upsert 保证只保留一个条目并正确累计。
- 快照保存最近一次完整错答回执，避免依赖可能过期清理的 Attempt/Quiz；不能在列表或详情读取时向其他用户暴露题目、回执或来源。
- 回填使用可重复执行的幂等数据流程；已完成 `Record` 是该练习的历史权威来源，只有没有完成记录的尝试才从逐题回执补入。
- 延续首页原型的色板、卡片、导航、安全区和按钮样式；错题本为空、加载、错误、筛选无结果和题目已掌握状态均须有清晰反馈。
- 开发按测试先行，并拆成多次 Git 提交。保留现存 `.idea/` 与 `project.private.config.json` 未跟踪文件，不纳入功能提交。

## 技术设计

### 数据与回填

- 新增 `MistakeItem` 表：`id`、`user_id`、`fingerprint`、题目 JSON 快照、最近错答 receipt JSON、最近来源 `topic_key/topic_title`、`wrong_count`、`first_wrong_at`、`last_wrong_at`、`status`、`mastered_at`、创建/更新时间。添加 `(user_id, fingerprint)` 唯一约束、`(user_id, last_wrong_at DESC, fingerprint DESC)` 列表索引，以及用户与状态、主题的查询索引。
- 新增 `MistakeAnswerEvent` 归集事件表，以 `(user_id, attempt_id, question_id)` 为唯一键，记录本次错答归一化出的 fingerprint 和作答时间。完成记录保存有 `attempt_id`，所以实时答题、`Record.answers` 与 `AttemptAnswer.receipt` 共用同一个业务事件键；只有 `ON CONFLICT DO NOTHING` 确认是新事件时才增加 `MistakeItem.wrong_count`，回填重跑、实时答题与回填并发以及两类历史快照重叠均不会重复计数。
- Alembic migration 只负责创建错题表和事件表；独立的可重试回填命令按用户和 `(attempt_id, question_id)` keyset 分批读取完成记录及未完成回执。回填通过事件唯一键保障幂等，按时间保留最近 receipt/来源，且不能覆盖已经由用户设置的掌握状态。错误题快照或回执无效时跳过并记录可诊断信息，不能使整个用户回填中断。
- 从 `backend/app/api/attempts.py` 提取 `_question_fingerprint` 到 `backend/app/domain/questions.py`，金币结算和错题本共用。先通过现有指纹行为回归测试，确保当前金币去重键不变。

### API 与业务规则

- `GET /v1/mistakes?status=to_review|mastered&topic_key=...&cursor=...&limit=...`：用户级 keyset 分页，稳定排序 `last_wrong_at DESC, fingerprint DESC`；响应含条目、下一页游标、当前用户全局待复习总数及其错题当前来源主题的去重筛选项。计数和主题选项不受当前列表状态/主题过滤影响。
- `GET /v1/mistakes/{fingerprint}`：返回快照、状态、统计、来源和最近一次错答回执。采用统一响应模型，前端由 OpenAPI 生成类型。
- `PATCH /v1/mistakes/{fingerprint}`：只接受 `status`，状态更新可幂等重试；状态切换时设置或清除 `mastered_at`。
- `POST /v1/mistakes/{fingerprint}/practice`：创建仅含该题的练习并返回标准练习标识。新增明确的练习用途标记，使完成它不入正常学习历史、不派发总结作业；仍走已验证的服务端判分、回执持久化和金币结算路径。若再练期间题目被移除/快照损坏，返回可理解的领域错误。
- `submit_answer` 在新答错回执落库的同一事务插入 `MistakeAnswerEvent`；只有新插入事件的事务继续对 `MistakeItem` 做 PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`。同一事务按现有流程结算金币；重复请求先取原回执，不触发事件插入或错题更新。
- 事务并发中，同一用户首次并发错误写入只有一个条目；不同练习的不同真实作答各累计一次。练习题重放不计第二次。

### 前端

- 在首页的 `Tabs` 中加入“错题本”第三个入口，并新增错题本列表与详情屏幕状态，沿用既有首页路由/状态切换模式。
- 列表提供待复习/已掌握筛选、主题筛选、分页加载、总待复习数、最近错答和错答次数；卡片布局适应窄屏和较长主题名称。
- 详情复用现有回执和证据展示组件，允许切换“已掌握/重新加入待复习”以及“再练一道”。再练反馈结束后允许回详情，状态与金币余额保持服务端最新值。
- 增加空列表、筛选空结果、请求失败重试、加载更多和返回列表后保留筛选/滚动位置体验。首页其他页面及单次错题回顾不改变。

## 开发步骤与提交

### Task 1: 共用题目指纹与错题数据模型

- **文件**：`backend/app/domain/questions.py`（新增）、`backend/app/api/attempts.py`、`backend/app/models/` 下现有 ORM 模型、数据库迁移与模型测试。
- **接口**：领域函数 `question_fingerprint(question) -> str`；`MistakeItem` ORM 模型、状态枚举和唯一/查询索引。
- **先写失败测试**：验证归一化后相同题目指纹稳定、改题型/选项后不同；现有金币结算仍使用原 fingerprint；错题模型约束和状态字段正确。
- **红灯命令**：`cd backend && pytest tests/domain/test_questions.py tests/models/test_mistake_item.py -q`。
- **实现后绿灯命令**：运行上述命令及 Alembic 空库升级/降级检查。
- **提交**：`feat: 建立错题领域模型与共享题目指纹`。

### Task 2: 答错原子归集与历史回填

- **文件**：答题 API/service/repository、`MistakeItem` 数据访问、数据迁移或回填命令、相关 API 集成测试。
- **接口**：内部 `record_wrong_answer(...)` 原子 upsert；可重复执行的 `backfill_mistakes(...)` 命令。
- **先写失败测试**：未完成练习错答立即入库；完成历史和未完成回执均回填；完成记录不与 AttemptAnswer 重复；回填重跑不重复计数；回填和实时答题并发不丢失或重复；并发首次写入只创建一条；同题多次实际作答计数正确；回执重放不累计；他人数据不混入。
- **红灯命令**：`cd backend && pytest tests/api/test_attempts_mistakes.py tests/services/test_mistake_backfill.py -q`。
- **实现后绿灯命令**：运行上述测试及真实 PostgreSQL 并发集成测试。
- **提交**：`feat: 原子归集错题并回填历史记录`。

### Task 3: 错题本查询、详情和掌握状态 API

- **文件**：`backend/app/api/mistakes.py`（新增）、API router、`backend/app/schemas/` 响应/请求 schema、API 测试。
- **接口**：列表分页、详情、状态 PATCH；所有数据从认证主体绑定用户。
- **先写失败测试**：按状态/主题过滤；稳定游标分页；待复习数；本人详情/列表；跨用户 404；无效状态、伪造 user id 和非法游标拒绝；掌握状态双向切换及幂等。
- **红灯命令**：`cd backend && pytest tests/api/test_mistakes.py -q`。
- **实现后绿灯命令**：运行 API 测试、OpenAPI schema 验证及相邻记录/作答回归测试。
- **提交**：`feat: 提供错题本查询与掌握状态 API`。

### Task 4: 单题再练与金币/总结隔离

- **文件**：attempt/quiz 领域模型与创建/完成流程、总结任务创建逻辑、错题 practice API、对应单元和集成测试。
- **接口**：`POST /v1/mistakes/{fingerprint}/practice` 返回标准 attempt 数据；内部 practice 标记用于跳过正常记录和 AI 总结。
- **先写失败测试**：再练仍服务端判分且 receipt 完整；正确答案依当前金币规则只结算一次且不自动掌握；错答刷新次数和回执并重开待复习；practice 不创建正式 Record/summary 任务、不消耗总结额度；普通练习通关/金币/总结行为完全保留；并发/重试幂等。
- **红灯命令**：`cd backend && pytest tests/api/test_mistake_practice.py tests/api/test_attempts.py -q`。
- **实现后绿灯命令**：运行上述测试及完整答题、金币和总结相关后端套件。
- **提交**：`feat: 支持错题单题再练`。

### Task 5: 小程序错题本列表与详情

- **文件**：`frontend/src/components/comic.tsx`、首页页面/屏幕状态、API 层和生成类型、错题列表/详情组件及对应前端测试。
- **接口**：从 OpenAPI 生成请求/响应类型；调用列表、详情和状态更新 API。
- **先写失败测试**：第三个入口可达；待复习/已掌握、主题筛选、分页与计数正确；长列表、空状态、错误重试可用；详情展示最近回执/证据且状态操作正确；切屏返回后筛选保留。
- **红灯命令**：`cd frontend && npm test -- --run`（若当前测试脚本不同，以 `package.json` 中已有脚本为准）。
- **实现后绿灯命令**：运行错题本前端测试、TypeScript 检查、OpenAPI 类型生成/差异检查和 `weapp` 构建。
- **提交**：`feat: 添加小程序错题本列表与详情`。

### Task 6: 再练交互、整体验收与文档

- **文件**：错题详情练习组件、反馈复用、运行说明和端到端/前后端集成测试。
- **接口**：接入 practice API、既有提交答案/金币余额更新流程；详情提供练习完成回流。
- **先写失败测试**：单题正确、错误、断网重试；金币余额全局更新；已掌握题答对仍保留掌握状态，答错重新开放；不影响普通学习入口与单条记录错题回顾。
- **红灯命令**：`cd frontend && npm test -- --run` 与 `cd backend && pytest tests/api/test_mistake_practice.py -q`。
- **实现后绿灯命令**：执行后端全套 TDD 回归、前端检查/构建，并在微信开发者工具核对原型布局及真实小程序流程。
- **提交**：`feat: 完成错题再练体验与验收`。

### Task 7: 个人主页、题库导航与主题管理体验

- **文件**：用户统计 API、`frontend/src/features/home.tsx`、新增题库/个人主页屏幕、底部导航和对应测试/运行说明。
- **接口**：`GET /v1/me/stats` 返回认证用户已完成组数、作答数、答对数和正确率；最近记录复用现有记录列表 API。
- **先写失败测试**：统计按当前用户隔离、空用户返回零值；底部导航四入口及正确标签；题库主题和删除确认可见。
- **实现后绿灯命令**：运行 profile stats API 测试、后端完整测试、前端测试/类型检查和小程序生产构建。
- **提交**：`feat: 增加个人主页与独立题库入口`。

## 交付验收

- 新库迁移可执行，旧库回填可重复安全执行；所有错题只属于当前微信用户，未完成错题无需等待通关即可出现。
- 并发答题、幂等重放、分页边界、金币结算、总结跳过和掌握状态规则通过测试。
- 小程序构建成功；首页第三入口、筛选/分页、错题详情、来源证据、状态切换、再练和余额更新符合原型风格；原有历史记录错题回顾继续工作。
- 后端测试按 TDD 先红后绿记录；所有功能提交分开提交，不混入 `.idea/` 和 `project.private.config.json`。

## Review Focus

- 核对用户身份始终来自服务端会话，错题、金币和个人统计不会跨用户泄漏。
- 核对错答事件幂等键、并发更新、历史回填、金币边界以及再练不生成正式记录/总结。
- 核对学习、题库、错题本、个人主页之间的导航；删除主题必须可见并二次确认；既有学习记录错题回顾和普通练习保持可用。
- 核对迁移与回填步骤可重复执行，前端 OpenAPI 类型来自当前后端契约。
- 复核提交只包含功能代码、测试和文档，不含本地密钥、`.idea/` 或 `project.private.config.json`。

## 已核对的官方技术资料

- SQLAlchemy 官方 PostgreSQL dialect 记录 `INSERT ... ON CONFLICT DO UPDATE/NOTHING`，适用于数据库级并发冲突处理：[SQLAlchemy PostgreSQL dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert)。
- FastAPI 官方响应模型用于响应校验、文档生成和字段过滤：[FastAPI response models](https://fastapi.tiangolo.com/tutorial/response-model/)。
- 本项目目标 PostgreSQL 16 支持 `ON CONFLICT`，文档：[PostgreSQL 16 INSERT](https://www.postgresql.org/docs/16/sql-insert.html)。
