# Plus One 产品审查：2026-09-19

审查基线：`deepseek-api`，提交 `324c0f4d94857aeeab568972990059c853bc62b8`。

## 当前验证结果与边界

- 本地提交和通过 GitHub API 查询的远端分支 SHA 一致；审查开始时工作区干净。
- 本轮重跑原有 127 项 Django 测试，全部通过，系统检查无报错。
- 该提交的 [GitHub CI](https://github.com/luneth8437-cmd/Plus-One/actions/runs/35050593168) 为 success。
- 正式 Discover 页面刷新时出现 Render 唤醒页，随后可以打开；Create 页面可以打开，原始描述输入框实际带有 `maxlength=2000`。
- `/healthz/` 实测返回 HTTP 200 和 `ok`。这证明对应功能已在站点出现，不等于核实了 Render 的精确构建 SHA、数据库套餐或整个双用户线上闭环。
- 使用临时脚本和内存测试数据库完成 8 个额外行为复现；另使用真实前端 JS 函数和最小 DOM 模型验证消息游标问题。这些是“确认问题存在”的测试，不是修复后的回归通过。
- 没有在正式站点发布活动、发送测试聊天、运行清理、切换套餐或更改部署。本轮只新增审查文档及进度记录。

## 应先修复的业务问题

### 1. 聊天消息游标可能跳过尚未显示的对方消息

**优先级：高；证据：源码及 JS 模型复现。**

`appendChatMessage` 对发送返回和轮询返回使用同一个 `lastMessageId`，每追加一条就覆盖它。

复现顺序：界面已同步到 10 → 对方消息 11 入库但还未轮询 → 自己发送的消息 12 返回并显示 → 游标变成 12 → 下次请求 `after=12`，消息 11 不再被取回。消息没有从数据库丢失，但该界面可能一直看不到，直到刷新。如果较早的轮询响应迟到，还会把游标从 12 降回 11，已显示消息的去重分支又阻止它前进。

建议：将“服务端连续同步进度”与“已显示消息”分开管理；本地发送响应不应直接跳过尚未同步的区间。处理乱序响应，并为同一轮询通道增加在途请求约束。

位置：[app.js:105](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/static/plusone/app.js:105)、[app.js:160](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/static/plusone/app.js:160)、[app.js:202](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/static/plusone/app.js:202)。

### 2. 聊天到期后，卡片可能无法重新进入 Discover

**优先级：高；证据：Django 隔离复现。**

聊天轮询先把 Match 改成 `expired`，但没有同步卡片容量；后续全局过期扫描只有在本轮新过期了 Match 时才重开卡片。已经被轮询置为过期的 Match 不再触发该条件。

实测：Match 已过期、卡片剩余占用为零、活动本身尚未过期，但 Post 仍为 `matched`，Discover 查不到它。它可能一直滞留到另一场聊天触发全局重开，或自身过期。重置匿名身份也会留下类似的关联状态问题；发布者重置身份时，其卡片应按明确规则取消，不能之后意外重新开放。

建议：让到期、拒绝、身份重置等入口使用一致的状态转换服务，同步处理 Match 和 Post，并补足各入口的回归测试。

位置：[views.py:383](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/views.py:383)、[models.py:198](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/models.py:198)、[expiration.py:19](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/services/expiration.py:19)、[identity.py:71](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/services/identity.py:71)。

### 3. 编辑审核期间被匹配，保存仍会覆盖已匹配活动

**优先级：高；证据：Django 隔离复现。**

编辑入口只在调用审核前确认卡片为 active。审核过程中若另一人匹配，旧表单仍能保存，并且 `save_for_user` 将状态强制改回 active。

实测：审核期间创建 Match，审核完成后编辑成功，标题发生变化，Post 变回 active，而 Match 仍在聊天。容量统计目前还能阻挡另一个匹配，不应把这一结果夸大为已经复现了“一张卡同时匹配多人”；已确认的问题是对方刚接受的计划能被悄悄修改，以及关联状态不一致。

建议：保存前锁定并重新读取 Post，确认仍允许编辑；把“新发布”和“修改已有卡片”的保存语义拆开，编辑不能无条件重置状态。

位置：[views.py:167](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/views.py:167)、[forms.py:57](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/forms.py:57)。

### 4. 发布和发送缺少可靠的重复请求处理

**优先级：中高；证据：发布重复请求已复现，发送错误路径为源码确认。**

同一个发布 POST 连续提交两次，会生成两张活动卡。发布按钮没有提交中锁定；聊天虽然暂时禁用按钮，但断网、非 JSON 错误响应会进入无 `catch` 的异常路径，界面没有明确的发送失败反馈。响应丢失后的用户重试也没有服务端去重标识。

建议：发布显示进行中状态，并使用一次性请求标识避免网络重试重复创建。聊天区分发送中、已发送、失败/结果未知；对重试使用稳定的客户端消息 ID。不能只依赖禁用按钮来保证不重复。

位置：[create_post.html:50](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/templates/plusone/create_post.html:50)、[views.py:113](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/views.py:113)、[app.js:187](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/static/plusone/app.js:187)。

## 直接影响匹配成功率和安全感的缺口

### 5. 五分钟聊天缺少可靠的提醒和同意状态同步

**优先级：高；证据：源码及单方同意的响应复现。**

匹配建立即开始五分钟倒计时，但站点导航上的新聊天角标只在页面请求时计算，用户停留在旧页面不一定知道有人加入。聊天轮询只返回消息和总体状态，未返回双方同意标记。

实测：一方点 Agree 前后，另一方的轮询响应完全相同，因为总体状态仍为 chatting。页面上的“对方是否同意”会停留在旧值。

建议：优先增加站内新匹配提醒和双方同意状态同步；再评估是否需要对方进入后开始计时、有限等待或浏览器通知。涉及五分钟产品机制的改变应另作产品决策，不能直接视为确定的代码修复。

位置：[context_processors.py:6](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/context_processors.py:6)、[matching.py:107](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/services/matching.py:107)、[views.py:418](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/views.py:418)、[chat.html:14](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/templates/plusone/chat.html:14)。

### 6. 同意见面之后无法举报，举报处理流程也不完整

**优先级：高；证据：源码和服务调用复现。**

Report 按钮只在 chatting 状态显示，服务同样拒绝非 chatting 状态。双方同意后，页面仍建议“遇到不适可以举报”，但此时已经不能记录举报。

现有举报只在 Match 上保存关闭原因/操作者，并写一条系统消息。没有独立的举报处理状态、处置流程或报告原因采集；后台 Match 列表也没有专门的举报筛选。

建议：把举报与关闭聊天分开，允许在聊天结束或见面后提交；至少提供原因、处理状态及管理员查看入口。先做可用的最小处置流程，再考虑更复杂的风控。

位置：[chat.html:18](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/templates/plusone/chat.html:18)、[chat.html:65](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/templates/plusone/chat.html:65)、[chat.py:59](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/services/chat.py:59)、[admin.py:32](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/admin.py:32)。

### 7. 内容审核存在误拦截和降级漏检

**优先级：中高；证据：规则函数复现。**

规则使用子串匹配。`Whatever time works for you` 会因为 whatever 中包含 hate 而被拦截；这些规则结果是硬约束，即使模型放行也仍会拦截。模型不可用时，`Call me at 13800138000` 不会触发现有规则；本结论针对规则降级，不代表已测试线上模型的判断结果。

建议：用词边界和语义处理减少误报，对联系方式及目标语言补充明确规则与对抗用例；明确模型异常时允许哪些操作，并给用户可理解的提示。

位置：[moderation.py:10](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/ai_services/moderation.py:10)、[moderation.py:34](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/ai_services/moderation.py:34)、[moderation.py:81](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/ai_services/moderation.py:81)。

## 数据与上线维护

### 8. 自动清理尚未接通，接通前必须先修正“活跃”的定义

**优先级：高；证据：配置检查和 Django 隔离复现。**

仓库内清理仍为手动命令，没有自动执行计划；AI 日志和事件的 30/90 天是命令运行时的筛选条件，不是目前已自动兑现的保留承诺。

更重要的是，匿名用户用 `last_login` 判断过期，正常持续访问不会更新该值；保护条件只包括有效活动和 chatting，不保护作为参与者的 agreed 见面。

实测：把一个已登录参与者的 last_login 设为十天前，访问 Dashboard 200 成功，且他有未来的 agreed 约会，仍被清理查询选中。执行清理会级联删除该用户关联的 Match/聊天；因此不能先把当前命令直接接上定时任务。

建议：先加入节流更新的最近活跃时间、未来有效约会保护和清理边界测试，再安排清理及过期 Django session 清理。日志保留/模型处理方式也应在用户能看到的隐私说明中交代。

位置：[cleanup.py:10](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/services/cleanup.py:10)、[identity.py:53](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/services/identity.py:53)、[models.py:156](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/models.py:156)。

### 9. 保活不等于稳定在线，数据库方案仍需确认

**优先级：高（正式长期使用时）；证据：本轮线上观察、GitHub 记录及官方文档。**

这次实测出现 Render 唤醒页。keepalive 配置是每十分钟，但查询到最近几次启动时间相隔约 2–3 小时；这只能证明实际记录并未呈现稳定的十分钟间隔，不能据此断定根因。工作流的 `|| echo` 还会把 curl 失败变成 job 成功，所以绿色并不代表应用健康。现有 `/healthz/` 不查询数据库，也无法发现数据库故障。

仓库的 web 和 PostgreSQL 仍配置为 free。Render 当前说明：免费 web 空闲十五分钟会休眠；免费数据库创建三十天后过期，且无备份。未读取 Render 账户账单及数据库详情，实际套餐和准确到期日尚未核实。

建议：决定长期使用时的实例/数据库方案，设置备份和独立可用性告警；保留轻量 liveness，另增数据库 readiness 检查，并让监控失败可见。

证据：[keepalive.yml:18](/Users/fillun/Desktop/plus-one/deploy_checkout/.github/workflows/keepalive.yml:18)、[render.yaml:1](/Users/fillun/Desktop/plus-one/deploy_checkout/render.yaml:1)、[最近保活运行](https://github.com/luneth8437-cmd/Plus-One/actions/runs/35413057644)、[Render 官方限制](https://render.com/docs/free)、[GitHub 调度说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

### 10. AI 调用预算和最终输入限制仍有缺口

**优先级：中高；证据：源码及发布输入复现。**

原始自然语言输入的 2,000 字符限制已生效，但最终发布/编辑的 description 没有相同的长度限制，且会直接送审核、存入日志。测试在模拟审核通过时成功提交了 10,000 字符描述。代码中未发现针对发帖、AI 请求和身份创建的限流。

15 秒是 SDK 超时配置，带一次重试；不是整个用户请求的十五秒硬截止。草稿路径还顺序执行审核、解析，两次调用都可能耗时。未做线上压测或真实付费 API 延迟试验，不应声称已测出生产最坏延迟。

建议：限制所有实际进入 AI 的字段长度，给身份/IP/动作设置适当频率与费用预算，并对完整操作设置时间预算、降级和耗时指标。

位置：[forms.py:23](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/forms.py:23)、[posts.py:6](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/services/posts.py:6)、[views.py:94](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/views.py:94)、[client.py:70](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/ai_services/client.py:70)。

## 后续质量与体验改进

1. **PostgreSQL 并发测试**：目前 CI 使用 SQLite；本轮状态复现不等同真实 PostgreSQL 双事务锁竞争测试。应补双人同时匹配、关闭/发送、编辑/匹配测试，以及生产配置检查。见 [.github/workflows/ci.yml:15](/Users/fillun/Desktop/plus-one/deploy_checkout/.github/workflows/ci.yml:15)。
2. **统计口径**：修改现有卡片也会调用发布服务并产生 `publish_card`。漏斗把事件条数当发布量，清理又会将关联事件的 Match 设空，影响按 Match 去重的历史指标。应区分创建、编辑，并为保留后的统计设计稳定口径。见 [posts.py:14](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/services/posts.py:14)、[funnel_report.py:27](/Users/fillun/Desktop/plus-one/deploy_checkout/plusone/management/commands/funnel_report.py:27)。
3. **移动端与无障碍**：当前窄屏导航/大标题占据首屏较多空间；聊天计时、同意、举报区在手机端排在输入区之后。可提高关键状态的可见性，补充详情页 ×/♥ 的辅助标签。它们是源码/截图支持的设计改进建议，未做用户可用性实验。
4. **空队列和启动方式**：本次现有会话看到零张可选卡片，仅是一次快照，不能推算全站 DAU 或断言没有真实用户。可针对真实试点设置固定校园地点、参与时间段和发布引导，改善同时在线密度；无需恢复自动机器人演示。
5. **依赖可复现**：保留精简的直接依赖声明，同时引入经过测试的部署锁定清单，避免同一提交每次部署解析到不同版本。见 [requirements.txt](/Users/fillun/Desktop/plus-one/deploy_checkout/requirements.txt)。

## 建议实施顺序

1. 消息游标、到期/重置状态联动、编辑匹配竞争、重复发布和错误反馈。
2. 新匹配提醒、双方同意同步、见面后举报及审核规则。
3. 修正活跃/约会保护，再安排定时清理；确认生产数据库、备份和监控。
4. PostgreSQL CI、AI 预算、统计口径；最后做移动端细节及真实试点验证。

独立前端和运维审查用于扩大覆盖；以上关键结论由主审查者通过源码、临时复现或线上观察核对。
