# Plus One Resume Project Bullets

Use these bullets for a resume or LinkedIn project section. They are written to be evidence-based: public demo, implemented product flow, DeepSeek integration, role-play validation, and automated checks are separated from future real-user validation.

## Chinese Version

**Plus One | AI-assisted Campus Matching App | Independent Product Design and Full-stack Implementation**

- Independently defined the campus instant companion problem around meals, study, sports, and short campus breaks, positioning the product as an anonymous, short-lived, low-commitment one-to-one matching tool rather than a general social feed or group chat.
- Designed and implemented the full discovery-to-meeting loop: temporary activity cards, Interested/Pass decisions, one-to-one match creation, five-minute anonymous chat, mutual Agree, meet handoff, and Dashboard state management.
- Integrated DeepSeek through an OpenAI-compatible client for natural-language card drafting, icebreakers, and safety moderation, with rule-based fallback and `LLMLog` audit records to keep the core flow usable when external model calls fail.
- Built and deployed a working Django product with PostgreSQL-ready configuration, Render deployment, WhiteNoise static serving, GitHub Actions CI, public README demo GIF, screenshots, deployment docs, security policy, and product validation documentation.
- Conducted role-play validation across five target scenarios and executed server-side product-flow tests; all 5/5 role-play flows reached mutual-agreement handoff, while DeepSeek runtime evaluation showed 5/5 safety moderation accuracy and exposed parser gaps on mixed-intent cards.

## Short Chinese Version

- 独立完成 Plus One，一款面向校园临时吃饭、学习、运动等场景的 AI 辅助匿名即时结伴产品。
- 设计并实现临时卡片、Interested/Pass、一对一匹配、五分钟匿名短聊、双方 Agree 后 handoff 和 Dashboard 状态管理。
- 接入 DeepSeek，用于自然语言建卡、破冰话术和安全审核，并保留规则 fallback 与 `LLMLog` 追踪。
- 完成 Django 全栈实现、Render 部署、GitHub Actions CI、README demo GIF、产品验证文档和安全说明。
- 通过五类角色化验证跑通完整流程，5/5 到达 handoff；AI 实测安全审核 5/5 通过，同时识别出混合意图建卡解析问题。

## English Version

**Plus One | AI-assisted Campus Matching App | Independent Product Design and Full-stack Implementation**

- Defined the campus instant-companion problem across meals, study, sports, and short campus gaps, positioning Plus One as an anonymous, short-lived, low-commitment one-to-one matching tool rather than a social feed or group chat.
- Designed and implemented the end-to-end flow: temporary activity cards, Interested/Pass decisions, one-to-one match creation, five-minute anonymous chat, mutual Agree, meet handoff, and Dashboard state management.
- Integrated DeepSeek through an OpenAI-compatible client for natural-language card drafting, icebreakers, and safety moderation, with deterministic fallback and `LLMLog` records for reliability and auditability.
- Built and deployed a working Django product with PostgreSQL-ready configuration, Render deployment, WhiteNoise static serving, GitHub Actions CI, public demo GIF, screenshots, deployment docs, security policy, and validation docs.
- Ran five scenario-based role-play validation flows through the actual server-side product path; all 5/5 reached mutual-agreement handoff, while runtime AI evaluation showed 5/5 safety moderation accuracy and identified parser gaps for mixed-intent cards.

## Claims Not To Make Yet

Do not claim these until they are true:

- "Interviewed real students" unless actual student interviews are completed and documented.
- "Validated with real users" unless the usability tests are run with recruited participants.
- "AI parsing is production-ready" because role-play parsing exposed mixed-intent errors.
- "Launched to a campus" unless the product is used by a real campus audience.
