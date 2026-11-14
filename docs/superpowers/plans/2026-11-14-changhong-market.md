# 长虹行情 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将现有视频通知网站改为长虹财经关注站，并显示九项行情和上一交易日同期成交额比较。

**Architecture:** 独立行情适配、计算和缓存模块；worker 拉取，FastAPI 只读缓存，Jinja 与原生 JS 展示。沿用现有 SQLite 数据卷，新增表使用幂等建表，不改变原记录数据。

**Tech Stack:** Python 3.12 / FastAPI / httpx / SQLite / Jinja / 原生 JS / Docker。

**Spec:** `docs/superpowers/specs/2026-11-14-changhong-market-design.md`

## Global Constraints

- 名称“长虹”；低饱和枣红与中性黑白灰，明暗模式均无绿色。
- 成交额以亿元表示，盘中与上一交易日同一时刻比较，收盘比较全天。
- 不将九项重叠指数相加作为沪深总额；明确独立统计覆盖，缺失显示不可用。
- 原六字段顺序和时间倒序不变，前台无后台入口；后台密码、CSRF 和密钥机制不变。
- 服务保持停止；只能为验证使用临时隔离测试服务，并在完成后停止。
- 不新增大依赖、不发送真实邮件，不推送或发布。
- Shell 命令前加 rtk。Windows HTTP TestClient 在沙箱内会卡住，须 require_escalated 运行相关测试。

### Task 1: 品牌、配色与行情界面

**Files:** Modify `app/templates/{base,index,record,admin,login}.html`, `app/static/site.css`, `app/integrations.py` (仅邮件品牌文字), `README.md` 标题; Create `app/templates/_market.html`, `app/static/market.js`; Test `tests/test_web.py`。

**Interfaces:** 消费设计文档中的完整 `GET /api/market` 契约。Task 2 实现该端点。前端不依赖具体供应商，不调用外网。输出桌面右侧/手机上方行情栏，包含始终可见合计和可折叠九项卡片。

- [x] 在 `tests/test_web.py` 写新增首页结构断言，验证长虹、九个固定名称、行情栏、没有后台入口，运行观察特性未实现失败。避免为颜色字符串写镜像测试。

```python
def test_finance_home(client):
    from bs4 import BeautifulSoup
    page = BeautifulSoup(client.get('/').text, 'html.parser')
    assert '长虹' in page.title.text
    assert page.select_one('[data-market-panel]')
    for name in ('上证指数','深成指','创业板指','科创综指','科创50','沪深300','中证1000','证券板块','银行板块'):
        assert name in page.get_text()
    assert not page.select('a[href="/admin"]')
```

- [x] 修改品牌标题/标记/文案及 light、dark、系统 dark 三套 CSS 变量。使用枣红 `#8b3a42`，暗色使用可读的柔和玫瑰红，中性背景和灰色文字。
- [x] 新建模板包含静态九项占位和合计；首页两列布局，窄屏折叠显示。保持正文顺序和导航。
- [x] 实现 JS 安全 DOM 更新（textContent / createElementNS），不使用外来 HTML。finite 数值才格式化，0 有效，null 为“—”；正负 delta 为放量/缩量，零为持平，明确基准日期/时间。SVG 图有基准和时刻标签/可访问名称，午休不画成交易时长，部分时段按交易分钟定位。首次 fetch 和每 60 秒刷新、隐藏时暂停、有超时、禁止重叠请求；失败保留旧画面并标注。
- [x] `rtk proxy node --check app/static/market.js` 和 `rtk proxy .venv/Scripts/python.exe -m pytest tests/test_web.py -q` 验证；自检字段顺序和 no admin link 回归。
- [x] 仅提交此任务文件；报告变更、验证、未完成事项。API 在 Task 2 接入后整体浏览器验证。

### Task 2: 行情采集、计算、持久化与 API

**Files:** Create `app/market.py`, `app/market_provider.py`, `tests/test_market.py`; Modify `app/db.py`, `app/main.py`, `app/worker.py`, `README.md`, `docs/verification.md`; Create `docs/market.md`。

**Interfaces:** 输出设计中的 `/api/market` 契约。`MarketService(store, provider=None).refresh(now=None)` 拉取、计算和保存；`.snapshot(now=None)` 无外网只读公开视图；worker 调用 refresh。适配器提供固定证券的双会话分钟序列（时间、点位、元成交额、前收、来源/口径），不能信任上游声明直接当完整。

- [x] 写失败测试覆盖真实金额算法。用东方财富分钟增量 fixture 样本：前日 09:30=1亿、09:31=2亿，当日=2亿、4亿，应输出累计6亿、放量3亿、100%；腾讯输入同样场景为累计1亿/3亿、2亿/6亿，不能再次求和。只到09:31时不能和昨日15:00比较。缺失中间分钟或前日无对应分钟时 delta=null；0基数 delta可有但百分比null；周一应比较输入中的周五。合计只在独立沪深数据日期/截止分钟对齐时生成。
- [x] 运行新测试并观察失败，再实现解析/计算。数值拒绝非有限/负成交额，重复时间、错误证券身份和畸形数据不能污染缓存；分钟金额按会话累计。前收只用于它对应的最新交易日，不用它算历史日涨跌。数据源返回的最新日缺失不能随意回退使用更旧日前日。
- [x] 固定公开行情适配：有限超时/响应大小/重试；九项名称代码校验，来源写入输出。遵照 spec 的腾讯七指数及独立 A 股合计两序列、东方财富两板块映射。总额采用经实测核验有全市场覆盖的独立统计，不拿成分金额冒充；在 `docs/market.md` 给实际来源及口径证据。公开源失败保留缓存并显示未知，不伪造行情。供应商适配器可替换，暂不新增不必要的后台设置。
- [x] 新增成功缓存和状态/租约表，refresh 获得短租约后拉取，完成时核验所有权；不要在网络请求时持有 DB 事务。每项成功可独立保存，失败保留旧项并明确 stale；合计不能混用日期或时刻。请求端只读快照；返回带明确缓存时间与更新状态的 JSON，不暴露内部异常文本。
- [x] worker 新增独立行情 lane，停机响应、错误隔离，交易时间60秒、其他时间较低频率，初次立即拉取。持久化保证web/worker隔离仍可共享缓存；默认部署不需要额外环境变量。
- [x] 验证纯计算、数据库迁移/租约/失败保留、公开 API 离线响应、worker不影响其他lane。运行全套 pytest，JS syntax，Docker build并用隔离项目验证健康和API，随后停止测试服务。实测公开数据源并如实记载覆盖/失败限制。
- [x] 文档说明字段与统计口径、休市/滞后/缺历史、worker必要性、数据源稳定性、升级命令；提交实现和验证记录。
