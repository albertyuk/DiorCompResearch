"""Console i18n — English source strings with a Chinese toggle.

Templates wrap UI chrome in {{ T("…") }}. T is a Jinja context function: it
returns the Chinese translation when the request's mm_lang cookie is "zh",
otherwise the English source string itself. A missing dictionary entry falls
through to English, so an untranslated key can never blank the UI. Post
content, names and pipeline telemetry are never translated (the content is
already Chinese; the activity log is technical logging).

Translations are meaning-first, not word-for-word (owner directive): each
value says what the control DOES the way a native Chinese product would,
under one consistent glossary — 筛选 (the relevance filter), 整合
(enrichment/consolidation), 归组 (grouping), 报告 (the PPT deliverable),
明星 (celebs), 任务 (runs), 未匹配帖子 (orphans), AI (the model).

Parametrized strings use {n}/{m}-style slots substituted in the template
AFTER translation, so word order stays free per language.
"""
from __future__ import annotations

import jinja2

LANGS = ("en", "zh")
LANG_COOKIE = "mm_lang"

ZH: dict[str, str] = {
    # ── nav / chrome ─────────────────────────────────────────────────────
    "Runs": "任务",
    "Review · Posts": "审核 · 帖子",
    "Review · Projects": "审核 · 项目",
    "Decks": "报告",
    "Celebs": "明星",
    "Archives": "归档",
    "Learning": "学习",
    "Sign out": "退出登录",
    "Dark": "深色",
    "Light": "浅色",
    "Toggle dark mode": "切换深色模式",
    # ── first-visit welcome card ─────────────────────────────────────────
    "Guide": "使用指南",
    "How the monthly workflow works": "每月工作流程说明",
    "Welcome to Maison Monitor": "欢迎使用 Maison Monitor",
    ("One month of competitor social activity goes in; the finished monthly "
     "competitive deck comes out. You review at two checkpoints — everything "
     "else runs by itself. The whole flow:"):
        "输入一个月的竞品社媒动态，产出当月的竞品分析报告。你只需在两个审核"
        "节点把关，其余环节全部自动完成。完整流程如下：",
    "Pick the year and month": "选择年份和月份",
    ("On the Runs page, enter the month to search (like 2026-07) and press "
     "Start month. The system pulls every brand's Weibo posts for that month "
     "and the AI filter sorts them."):
        "在「任务」页输入要搜索的月份（如 2026-07），点击「开始搜索」。系统会"
        "抓取各品牌当月的全部微博，并由 AI 自动筛选。",
    "Confirm the Weibo filter": "确认微博筛选结果",
    "AI: highly reliable": "AI 判断：非常可靠",
    ("On Review · Posts the AI has marked every post keep or drop — it is "
     "very dependable at this, so a quick skim is usually enough. Overrule "
     "anything odd (each correction teaches next month's filter), then press "
     "Confirm & continue."):
        "在「帖子审核」页，AI 已把每条帖子标为保留或删除——这一步它非常可靠，"
        "快速浏览一遍即可。发现不对就手动改判（每次纠正都会让下个月的筛选更准），"
        "然后点击「确认并继续」。",
    "Check grouping, names, platforms & photos": "核对归组、命名、平台覆盖与配图",
    "AI: double-check its work": "AI 判断：需要人工核对",
    ("Posts are grouped into projects and matched across Xiaohongshu, Douyin "
     "and WeChat automatically — this is the step that most needs your eye. "
     "On Review · Projects: fix the grouping (drag posts, or use the Move "
     "to… buttons), correct each project's title, and verify the platform "
     "ticks against their evidence links. Also make sure the photos are "
     "right — click to preview, tick the ones that should reach the slide, "
     "and upload HQ originals whenever you have better."):
        "系统会自动把帖子归组成项目，并在小红书、抖音、微信中寻找对应内容——"
        "这是最需要人工把关的一步。在「项目审核」页：调整归组（拖拽帖子，或用"
        "「移动到…」按钮）、改正每个项目的标题、并对照证据链接核实各平台的勾选。"
        "同时确认配图无误——点击可预览大图，勾选要放进报告的图片；有更好的图就"
        "直接上传高清原图。",
    "Render the deck": "生成报告",
    ("Press Confirm & render and watch the progress bar. The PPTX deck and "
     "the XLSX spreadsheet appear on the Decks page — render as often as you "
     "like; every edit is picked up by the next render."):
        "点击「确认并生成报告」，通过进度条查看进度。PPT 报告和 Excel 表格会"
        "出现在「报告」页——可以反复生成，本页的每次修改都会体现在新文件里。",
    ("Reopen this any time with the Guide button at the top — and every page "
     "explains itself in its own How this page works box."):
        "随时可点顶部的「使用指南」重新打开本说明；每个页面也都有自己的"
        "「操作指南」。",
    "Got it — start": "明白了，开始吧",
    # ── shared bits ──────────────────────────────────────────────────────
    "Save": "保存",
    "Confirm": "确认",
    "Download": "下载",
    "Browse": "查看",
    "Resolve": "查找账号",
    "post ↗": "查看原帖 ↗",
    "profile ↗": "主页 ↗",
    "evidence ↗": "匹配帖子 ↗",
    "source ↗": "来源 ↗",
    "why?": "判定依据",
    "rationale": "判定依据",
    "not filtered yet": "尚未筛选",
    "required": "必需",
    "optional": "可选",
    "status: ": "状态：",
    "activity log": "运行日志",
    "(no activity yet)": "（暂无日志）",
    # ── runs page ────────────────────────────────────────────────────────
    "Accounts": "品牌账号",
    ("Accounts marked required must be confirmed before a run can start "
     "(Weibo is the ingestion source). The rest are optional — resolve them "
     "to populate the SOCIAL column, or skip them."):
        "标记为「必需」的账号需要先确认，任务才能启动（微博是主要数据来源）。"
        "其余账号可选——确认后可用于填充报告中的 SOCIAL 列，也可以先跳过。",
    "Brand": "品牌",
    "Platform": "平台",
    "Lookup query": "搜索词",
    "Start a month": "开始月度搜索",
    "Start month": "开始搜索",
    ("Runs ingest (Weibo) then the LLM relevance filter, then pauses at "
     "Review · Posts."):
        "系统会先抓取微博内容，再由 AI 筛选相关帖子，然后停在「审核 · 帖子」"
        "等待人工审核。",
    "Months": "各月任务",
    "No runs yet.": "还没有任务记录。",
    ("Enter a month above and press Start month — the search appears here "
     "with live progress."):
        "在上方输入月份并点击「开始搜索」——任务会出现在这里，并实时显示进度。",
    "Open review": "进入审核",
    "Stop": "停止",
    "Archive & reset": "归档并重置",
    "Pause the {m} run at its next safe point?":
        "要暂停 {m} 的任务吗？系统会在安全点停下，之后可随时继续。",
    ("Archive {m}? All posts, decisions and projects move to the archive "
     "(kept in the database, listed below) and the next Start month searches "
     "completely from scratch."):
        "确定归档 {m}？该月的全部帖子、审核决定和项目将存入归档"
        "（数据仍保留在数据库中，可在下方查看），下次搜索该月时将完全重新开始。",
    "last started {t} by {who}": "上次启动：{t} · {who}",
    "archive #{id} · {t} by {who} · {p} posts · {pr} projects":
        "归档 #{id} · {t} · {who} · 帖子 {p} 条 · 项目 {pr} 个",
    "RUNNING": "运行中",
    "INTERRUPTED": "已中断",
    "idle": "空闲",
    "background — ": "后台任务 — ",
    "posts": "帖子",
    "projects": "项目",
    ("was interrupted (server restart) — press Start month (or re-confirm "
     "the last checkpoint) to resume; everything already fetched and every "
     "paid LLM verdict is kept."):
        "因服务器重启而中断——点击「开始搜索」（或重新确认上一个审核步骤）即可继续；"
        "已抓取的内容和已完成的 AI 判定全部保留，不会重复计费。",
    # phase names + statuses (pills)
    "ingest": "抓取",
    "filter": "筛选",
    "review_posts": "帖子审核",
    "crosscheck": "跨平台核对",
    "enrich": "整合",
    "review_projects": "项目审核",
    "render": "生成报告",
    "done": "完成",
    "confirmed": "已确认",
    "waiting": "待处理",
    "running": "运行中",
    # project statuses
    "draft": "草稿",
    "dropped": "已删除",
    "rendered": "已生成",
    # ── login ────────────────────────────────────────────────────────────
    "Your name": "姓名",
    "Team passphrase": "团队口令",
    "Enter": "登录",
    ("Review decisions are attributed to the name you enter. The passphrase "
     "is shared out-of-band — ask the deck owner."):
        "你的审核操作将记录在这个姓名下。口令请向报告负责人索取。",
    # ── review · posts ───────────────────────────────────────────────────
    "Review checkpoint #1 — posts": "帖子审核（第 1 步）",
    ("Confirm locks these decisions and starts cross-platform verification "
     "+ enrichment. Needs review posts are listed first."):
        "点击确认后，当前的保留/剔除决定将生效，并自动开始跨平台核对与整合。"
        "「待人工确认」的帖子排在最前。",
    "Confirm & continue →": "确认并继续 →",
    "Lock post decisions and start cross-check + enrichment?":
        "确定锁定当前决定，并开始跨平台核对与整合？",
    "A run is working on {m} right now": "{m} 的任务正在运行",
    "started {t} by {who}": "{t} 由 {who} 启动",
    ("This page shows the last completed state; freshly ingested posts "
     "appear here after the filter step. It refreshes itself when the run "
     "pauses."):
        "本页显示的是最近一次完成的结果；新抓取的帖子要等筛选完成后才会出现。"
        "任务暂停时页面会自动刷新。",
    "weibo posts": "条微博帖子",
    "Nothing ingested.": "暂无抓取内容。",
    "Post": "帖子",
    "Filter verdict": "AI 判定",
    "Decision": "人工决定",
    "repost w/ commentary": "带评转发",
    "needs review": "待人工确认",
    "KEEP": "保留",
    "DROP": "剔除",
    "keep": "保留",
    "drop": "剔除",
    "human: ": "人工：",
    "Restore": "撤销",
    "Delete": "剔除",
    "Keep": "保留",
    "Click to preview all images": "点击查看全部大图",
    "Click to preview": "点击查看大图",
    "Slide images are chosen at Review · Projects, after consolidation.":
        "报告用图请在整合完成后，前往「审核 · 项目」页选择。",
    "working…": "处理中…",
    "~{t} left": "预计剩余 {t}",
    "min": "分钟",
    "s": "秒",
    # ── review · projects ────────────────────────────────────────────────
    "Review checkpoint #2 — projects": "项目审核（第 2 步）",
    ("Each project lists the posts consolidated into it — tick the images "
     "that should render on the slide (or drop an HQ original). Drag the ⠿ "
     "handle of a project onto another project to merge them; drag a post's "
     "⠿ handle to move just that post. Celeb names, relations and photo "
     "libraries live on the"):
        "每个项目下方列出了归入它的帖子——勾选要放进报告的图片（也可以直接拖入"
        "高清原图）。拖动项目的 ⠿ 手柄到另一个项目上可将两者合并；拖动某条帖子的"
        " ⠿ 手柄可以只移动这一条。明星的姓名、代言关系和照片库统一在",
    "Celebs page": "「明星」页管理",
    "image-less posts: live weibo screenshot": "无图帖子的兜底：微博实拍截图",
    "image-less posts: post card": "无图帖子的兜底：帖子卡片",
    "Confirm & render →": "确认并生成报告 →",
    "Confirm projects and render the deck?": "确认所有项目并生成报告？",
    "posts confirmed by {who} · {t}": "帖子已由 {who} 确认 · {t}",
    "last render by {who} · {t}": "上次生成：{who} · {t}",
    ("Projects below are the last completed state; the list refreshes "
     "itself when cross-check / enrichment / render finishes."):
        "以下是最近一次完成的结果；跨平台核对、整合或生成结束后，"
        "列表会自动刷新。",
    "Why these posts are grouped": "归组理由",
    "Title / phase": "标题 / 阶段",
    "Project line (table cell)": "项目简介（用于报告表格）",
    "Celebs on this project": "参与明星",
    "(name as shown on the slide · relation label)": "（报告中显示的姓名 · 关系标签）",
    "remove": "移除",
    "+ add celeb (slide name)": "＋ 添加明星（报告中的姓名）",
    "relation label": "关系标签",
    "Dates": "日期",
    "ongoing (— TBD)": "持续进行中（显示为 — TBD）",
    "Assets": "素材类型",
    "Platforms (SOCIAL column)": "发布平台（对应 SOCIAL 列）",
    "Hero media": "主视觉",
    "Consolidated posts — {n}": "组内帖子 — {n} 条",
    ("· tick an image to put it on the slide · drop an HQ original on the "
     "drop zone · drag ⠿ to move a post to another project · ✕ remove takes "
     "one post out, the rest stay grouped"):
        "· 勾选图片即用于报告 · 高清原图可拖入虚线框 · 拖动 ⠿ 可把帖子移到其他项目 "
        "· 「✕ 移出」只移走这一条，其余不受影响",
    "matched": "自动匹配",
    "Drag onto another project to move this post": "拖到另一个项目即可移动这条帖子",
    "tick = this image goes on the slide": "勾选后，这张图片将用于报告",
    "drop HQ<br>image": "拖入高清<br>原图",
    "No posts attached — drag posts here from another project, or drop the project.":
        "该项目下没有帖子——可以从其他项目拖入，或者删除该项目。",
    "Drop project": "删除项目",
    "Restore project": "恢复项目",
    "Ungroup ({n} posts)": "拆分该组（{n} 条帖子）",
    ("Split this project into {n} single-post projects? Each matched "
     "cross-platform post follows its weibo post; the rest return to the "
     "orphan list."):
        "确定把该项目拆分成 {n} 个单帖项目？其他平台的匹配帖子会跟随各自"
        "对应的微博帖子；没有对应关系的会回到「未匹配帖子」列表。",
    "matched weibo post ↗": "对应的微博帖子 ↗",
    "placed by a reviewer": "由审核人手动归入",
    ("Remove just this post from the group? It becomes its own project."):
        "只把这条帖子移出该组？移出后它会单独成为一个项目。",
    ("Detach this matched {p} post from the group? It returns to the orphan "
     "list."):
        "把这条 {p} 匹配帖子移出该组？移出后它会回到「未匹配帖子」列表。",
    "Remove only this post from the group — the rest stay together":
        "只移出这一条，组内其余帖子不受影响",
    "✕ remove": "✕ 移出",
    "Orphans": "未匹配帖子",
    ("— cross-platform posts matching no kept Weibo post, sifted by the "
     "same filter as review #1"):
        "— 其他平台上没有对应微博帖子的内容，已用同一套 AI 筛选预先分好保留/剔除",
    ("Keeps surface first; greyed rows were dropped by the filter (open the "
     "rationale to see why). Drag an orphan's ⠿ handle onto a project to "
     "attach it there, or promote it to its own project."):
        "标为「保留」的排在前面；灰色的是被 AI 剔除的（点开「判定依据」可看原因）。"
        "拖动 ⠿ 手柄到某个项目上即可归入，也可以让它单独成为一个项目。",
    "Drag onto a project to attach": "拖到项目上即可归入",
    "Promote to project": "单独成项",
    "Ignore": "忽略",
    ("Merge the dragged project into this one? Its posts, celebs and "
     "platform evidence move here."):
        "确定把拖来的项目并入这个项目？它的帖子、明星和平台匹配记录都会一并转移。",
    "Move failed: ": "移动失败：",
    "Drag onto another project to merge": "拖到另一个项目上可合并两个项目",
    # ── destination picker (click-based move) ────────────────────────────
    "Cancel": "取消",
    "Move to project…": "移入项目…",
    "Move to…": "移动到…",
    "Merge into…": "并入其他项目…",
    "Choose a destination for this post": "这条帖子要移到哪里？",
    "Choose a destination for this project": "这个项目要移到哪里？",
    "＋ New project (from this post)": "＋ 用这条帖子新建项目",
    "Discard — mark dropped, leaves the report":
        "删除 — 标记为「已删除」，不再进入报告",
    "Drop this project — it leaves the report":
        "删除该项目 — 不再进入报告",
    "Return to the orphan list": "退回「未匹配帖子」列表",
    "No other project yet — create one first.":
        "还没有其他项目——请先新建一个。",
    "Could not save selection: ": "选择未能保存：",
    "Upload failed: ": "上传失败：",
    "Delete failed: ": "删除失败：",
    # ── lightbox ─────────────────────────────────────────────────────────
    "Use on the slide": "用于报告",
    "✓ On the slide — click to remove": "✓ 已选入报告 — 点击取消",
    "HQ upload": "高清原图",
    "Close (Esc)": "关闭（Esc）",
    "Previous (←)": "上一张（←）",
    "Next (→)": "下一张（→）",
    # ── celebs page ──────────────────────────────────────────────────────
    "Celebrity registry": "明星档案",
    ("Names, brand relationships and photo libraries compound across "
     "months. Relations verified from a caption or a source keep their "
     "label; unverified ones render with a trailing ? on the deck. Photos "
     "uploaded here are used on slides when a project's celeb has no "
     "labeled post image — the first photo wins; click any photo to "
     "preview the library."):
        "明星的姓名、代言关系和照片库会逐月累积，长期沿用。有帖子原文或可靠来源"
        "佐证的关系会正常显示；未经核实的，在报告中会带「?」标记。当项目里的明星"
        "没有合适的帖子配图时，报告会自动使用这里的第一张照片；点击任意照片"
        "可放大浏览。",
    "Add celeb": "添加明星",
    "Name (中文 · Latin · occupation)": "姓名（中文 · 拼音/英文 · 职业）",
    "Relationship per brand": "与各品牌的合作关系",
    "(blank = none; tick verified when a source confirms it)":
        "（留空表示无合作；有来源佐证时再勾选「已核实」）",
    "verified": "已核实",
    "Photos — first one is used on slides": "照片——第一张将用于报告",
    "Remove from the library": "从照片库中删除",
    "drop or click<br>to add photo": "拖入或点击<br>添加照片",
    "Remove this photo from the library?": "确定从照片库中删除这张照片？",
    "No celebs yet — the registry fills itself as enrichment runs, or add one above.":
        "暂无明星记录——整合运行后会自动补充，也可以在上方手动添加。",
    # ── decks page ───────────────────────────────────────────────────────
    "Deck archive": "报告文件",
    "Download database backup": "下载数据库备份",
    ("The backup is the SQLite database (projects, review decisions, "
     "celebrity registry, audit trail) — the part that can't be "
     "regenerated. Media is re-fetchable and decks re-renderable."):
        "备份内容是数据库本身（项目、审核决定、明星档案、操作记录）——"
        "这些数据一旦丢失无法找回。图片可以重新抓取，报告可以重新生成，"
        "唯独这些不行，请定期备份。",
    "Nothing rendered yet.": "还没有生成过报告。",
    "Decks appear here after Confirm & render on Review · Projects.":
        "在「项目审核」页点击「确认并生成」后，报告就会出现在这里。",
    "File": "文件",
    "Size": "大小",
    # ── archives ─────────────────────────────────────────────────────────
    "Archived searches": "历史归档",
    ("Each Archive & reset snapshots a month's entire search — posts, "
     "filter verdicts, human decisions, projects — and clears the live "
     "tables so the next Start month searches from scratch. Nothing is "
     "deleted: browse any archive below."):
        "每次「归档并重置」都会把该月的完整搜索结果——帖子、AI 判定、人工决定、"
        "项目——原样封存，并清空当前工作区，下次搜索该月时从零开始。"
        "所有数据都还在，可在下方随时查看。",
    "No archives yet.": "暂无归档。",
    "Use Archive & reset on the Runs page to move a finished month here.":
        "在「任务」页对已完成的月份执行「归档并重置」，记录就会出现在这里。",
    "Month": "月份",
    "Archived": "归档时间",
    "By": "操作人",
    "Posts": "帖子",
    "Projects": "项目",
    "Archive #{id} — {m}": "归档 #{id} — {m}",
    "archived {t} by {who}": "{t} 由 {who} 归档",
    ("Read-only snapshot of the search as it was when archived. Greyed "
     "posts were dropped (by the filter or a reviewer) at the time."):
        "这是归档当时的完整记录，仅供查看，不可修改。灰色帖子是当时被"
        "（AI 或审核人）剔除的。",
    "← All archives": "← 返回归档列表",
    "Verdict at archive time": "归档时的判定",
    "llm: ": "AI：",
    "no verdict": "无判定",
    "Projects at archive time": "归档时的项目",
    "Title": "标题",
    "Status": "状态",
    "Description": "简介",
    # ── learning ─────────────────────────────────────────────────────────
    "Filter learning": "筛选学习",
    ("Every keep/drop you make in review is catalogued below with what the "
     "LLM had decided. Before each filter run — or on demand here — the "
     "corrections are distilled into learned guidance appended to the "
     "filter prompt, so the filter gets more accurate over time. The core "
     "rubric (China-market test, perfume/beauty excluded) never changes."):
        "你在审核中的每一次保留/剔除，都会和 AI 当时的判定一起记录在下方。"
        "每次筛选运行前（也可以在这里手动触发），系统会从这些纠正中总结出"
        "新的筛选规则，让 AI 越用越准。核心标准（只看中国市场相关内容、"
        "排除香水美妆）始终不变。",
    "Update learned rules now": "立即更新筛选规则",
    "Distill the corrections into updated filter guidance now?":
        "现在就根据最新的纠正更新筛选规则？",
    "{n} new corrections since last update": "上次更新后新增 {n} 条纠正",
    "Current learned guidance": "当前生效的学习规则",
    "None yet — it appears after the first corrections are distilled.":
        "暂无——积累第一批纠正并总结后，会显示在这里。",
    "previous versions": "历史版本",
    "Correction catalogue": "纠正记录",
    "— last {n}": "— 最近 {n} 条",
    "No human decisions recorded yet.": "还没有人工审核记录。",
    "Keep or drop posts on Review · Posts — every correction lands here.":
        "在「帖子审核」页做出保留/删除的判断后，每一次纠正都会记录在这里。",
    "When / who": "时间 / 操作人",
    "LLM → human": "AI → 人工",
    "Post caption": "帖子内容",
    "LLM rationale": "AI 判定依据",
    "override": "推翻",
    # ── resolve ──────────────────────────────────────────────────────────
    "Confirm {b} — {p}": "确认账号：{b} — {p}",
    ("Confirming freezes the internal ID into config/brands.yaml and flips "
     "the account to verified."):
        "确认后，该账号的内部 ID 会写入配置文件并标记为已核实，"
        "此后抓取将一直使用这个账号。",
    "Candidates for": "候选账号：",
    "Lookup failed: ": "查询失败：",
    "Name": "名称",
    "Followers": "粉丝数",
    "Verification": "认证信息",
    "Manual entry": "手动填写",
    "display name (optional)": "显示名称（选填）",
    # ── grouping board ───────────────────────────────────────────────────
    "Grouping": "分组",
    "Grouping board": "分组看板",
    "Projects": "项目",
    "New project — drop a post here": "新建项目——把帖子拖到这里",
    "empty": "（无帖子）",
    "No projects yet — drop a post on the New project zone.":
        "还没有项目——把帖子拖到「新建项目」区即可创建。",
    "Unplaced posts": "待归组帖子",
    "filter…": "搜索…",
    "Orphans (other platforms)": "未匹配帖子（其他平台）",
    "Unassigned keeps": "已保留、未归组",
    "Dropped by the filter / a reviewer": "已剔除（AI 或人工）",
    "Nothing unplaced — every post is either in a project or was never ingested.":
        "没有待归组的帖子——现有帖子都已归入项目。",
    ("Pick a brand tab. Projects sit on the left; every unplaced post sits "
     "on the right — orphans from other platforms, dropped weibo posts, and "
     "kept posts not yet in any project."):
        "先选品牌。左边是这个品牌的项目，右边是所有还没归组的帖子——"
        "其他平台的未匹配帖子、被剔除的微博帖子，以及保留了但还没归入项目的帖子。",
    ("Drag a post from the right onto a project to place it there. Dragging "
     "a dropped post in also marks it kept; dragging an orphan in resolves "
     "it."):
        "把右边的帖子拖到左边的项目上即可归组。被剔除的帖子拖入项目后会自动"
        "改为保留；未匹配帖子拖入后即视为已匹配。",
    ("Drag a post onto the New project zone to start a fresh project from "
     "it. Drag a post from a project back to the right side to un-place it "
     "(weibo posts are marked dropped, platform posts return to orphans)."):
        "把帖子拖到「新建项目」区，会以它为基础新建一个项目。把项目里的帖子"
        "拖回右边即取消归组（微博帖子会改为剔除，其他平台的帖子回到未匹配列表）。",
    ("Fine-tune titles, dates, celebs and slide images on Review · Projects "
     "afterwards."):
        "标题、日期、明星和报告用图等细节，之后在「审核 · 项目」页调整。",
    ("Every drag saves immediately. When the grouping looks right, go to "
     "Review · Projects and press Confirm & render."):
        "每一次拖拽都会立即生效。分组满意后，前往「审核 · 项目」点击"
        "「确认并生成报告」。",
    # ── workflow stepper / next-step CTA ─────────────────────────────────
    "Next": "下一步",
    "Search & filter": "抓取与筛选",
    "Review posts": "帖子审核",
    "Cross-check & consolidate": "核对与整合",
    "Review projects & grouping": "项目审核与分组",
    "Render the report": "生成报告",
    "Download": "下载报告",
    ("The pipeline is running — progress shows below; this updates when it "
     "pauses."):
        "任务正在运行——进度见下方，暂停后此处会自动更新。",
    "Resume the search (Start month)": "继续搜索（点「开始搜索」）",
    "The search hit an error — press Start month to retry":
        "搜索出错——点「开始搜索」重试",
    "Decide keeps & drops, then Confirm & continue":
        "审核保留/剔除，然后点「确认并继续」",
    "Cross-check was interrupted — re-Confirm posts to resume":
        "核对被中断——重新点「确认并继续」即可恢复",
    "Check grouping & images, then Confirm & render":
        "检查分组和配图，然后点「确认并生成报告」",
    "Render failed — Confirm & render again":
        "报告生成失败——请再次「确认并生成报告」",
    "Download the report from the Decks page": "前往「报告」页下载",
    "Render the report (Confirm & render)": "生成报告（点「确认并生成报告」）",
    "Start the month's search": "开始本月搜索",
    # ── per-page guides ──────────────────────────────────────────────────
    "How this page works": "操作指南",
    "What happens next": "后续流程",
    # runs
    ("Enter the month to search (YYYY-MM) and press Start month. The system "
     "pulls every brand's Weibo timeline for that month, then the AI filter "
     "marks each post keep or drop."):
        "输入要搜索的月份（格式 YYYY-MM），点击「开始搜索」。系统会抓取各品牌"
        "该月发布的全部微博，再由 AI 初步判定每条帖子是保留还是剔除。",
    ("While it runs you'll see RUNNING, a progress bar and the activity log "
     "under the month. Stop pauses at the next safe point; Start month "
     "resumes exactly where it left off."):
        "运行期间，月份下方会显示「运行中」标志、进度条和运行日志。点「停止」"
        "会在安全点暂停，再点「开始搜索」即可从暂停处继续，不会丢失进度。",
    ("When the filter pill shows done and review_posts shows waiting, press "
     "Open review."):
        "当「筛选」显示完成、「帖子审核」显示待处理时，点击「进入审核」。",
    ("The run waits at Review · Posts for your keep/drop decisions — nothing "
     "continues until you confirm there. Archive & reset moves a finished "
     "month into Archives so a fresh search can start from scratch."):
        "任务会停在「审核 · 帖子」，等你审核完并确认后才继续后面的步骤。"
        "「归档并重置」会把整个月的结果封存进归档，之后可以重新搜索这个月。",
    # posts
    ("Work brand by brand. Greyed rows are dropped; everything else goes "
     "forward. Posts flagged needs review are listed first — decide those."):
        "按品牌逐一检查。灰色的帖子是已剔除的，其余都会进入后续步骤。"
        "标着「待人工确认」的排在最前，请优先处理。",
    ("Click any thumbnail to preview the images full-size. Open why? to see "
     "the AI's reasoning for each verdict."):
        "点击缩略图可查看大图；点开「判定依据」可以看到 AI 为什么这样判。",
    ("Use Delete / Keep / Restore to overrule the filter. Every correction "
     "is remembered and makes the filter more accurate next month (see "
     "Learning)."):
        "AI 判错时，用「剔除 / 保留 / 撤销」纠正它。每次纠正都会被系统记住，"
        "下个月的筛选会因此更准（详见「学习」页）。",
    "When the keeps look right, press Confirm & continue.":
        "确认保留的帖子都没问题后，点击「确认并继续 →」。",
    ("Cross-platform verification (Douyin / RED / WeChat) and grouping into "
     "projects run automatically — usually a few minutes. When the run "
     "pauses again, continue on Review · Projects. If you change decisions "
     "here afterwards, press Confirm & continue again to regroup."):
        "接下来系统会自动完成跨平台核对（抖音/小红书/微信）并把帖子归组成项目，"
        "一般需要几分钟。完成后请前往「审核 · 项目」继续。如果之后又在本页改了"
        "决定，再点一次「确认并继续」即可重新归组。",
    # projects
    ("Check each project's grouping — read Why these posts are grouped. Fix "
     "mistakes by dragging the ⠿ handles (merge two projects, or move a "
     "single post), with ✕ remove, or with Ungroup."):
        "逐个检查项目的归组是否合理——可参考「归组理由」。分错了就拖 ⠿ 手柄调整"
        "（合并两个项目，或移动单条帖子），也可以用「✕ 移出」或「拆分该组」。",
    ("Tick the images that should appear on the slide (click any image to "
     "preview it full-size; drag an HQ original onto the dashed box). "
     "Nothing ticked = the post's first photo is used, exactly as you see "
     "it in the preview — never a screenshot of the whole post."):
        "勾选要放进报告的图片（点击可看大图；高清原图可直接拖入虚线框）。"
        "一张都不勾时，报告会使用该帖子的第一张照片——和预览里看到的一模一样，"
        "不会截取整条帖子。",
    ("Edit the title, dates, description line, celebs and platform ticks, "
     "then press Save on that project."):
        "修改标题、日期、项目简介、参与明星和发布平台后，记得点该项目的「保存」。",
    ("Review the Orphans at the bottom — keeps come first. Drag one onto a "
     "project, Promote it to its own project, or Ignore it."):
        "最后看一下底部的「未匹配帖子」——「保留」的排在前面。可以拖进某个项目、"
        "让它「单独成项」，或者「忽略」。",
    ("Untick include in render on any project you want to leave out, then "
     "press Confirm & render — or Render all to ignore the ticks. Watch the "
     "progress bar."):
        "把不想放进报告的项目取消勾选「纳入本次生成」，再点「确认并生成报告」；"
        "点「生成全部」则忽略勾选、全部生成。生成进度看进度条。",
    "include in render": "纳入本次生成",
    "untick to leave this project out of the next render":
        "取消勾选后，下次生成的报告不含该项目",
    "Render all": "生成全部",
    "Render every project, ignoring the render ticks":
        "生成全部项目（忽略勾选）",
    ("Nothing is ticked — tick include in render on at least one project, "
     "or press Render all."):
        "一个项目都没有勾选——请至少勾选一个「纳入本次生成」，"
        "或点击「生成全部」。",
    "Confirm projects and render only the {n} ticked ones?":
        "确认所有项目，但本次只生成勾选的 {n} 个？",
    ("The PPTX deck and the XLSX spreadsheet are generated and appear on "
     "the Decks page. Render as often as you like — every edit here is "
     "picked up by the next render."):
        "系统会生成 PPT 报告和 Excel 表格，完成后出现在「报告」页。"
        "可以反复生成——本页的每次修改都会体现在新生成的文件里。",
    # decks
    ("Download the newest PPTX (the deck) and XLSX (the project "
     "spreadsheet) — filenames carry the month and the render time."):
        "下载最新的 PPT 报告和 Excel 项目表格，文件名里标注了月份和生成时间。",
    ("Every render creates a NEW file; older versions of the same month "
     "stay listed here (newest first, PARTIAL marks a selective render) "
     "until you delete them."):
        "每次生成都会产生一个新文件；同一月份的旧版本会保留在这里"
        "（最新的排在最上面，带 PARTIAL 的表示只生成了部分项目），"
        "直到你手动删除。",
    "Last changed": "最后更新",
    ("Delete {f}? The file is removed from the server — this cannot be "
     "undone."):
        "确定删除 {f}？文件将从服务器移除，无法恢复。",
    ("Download database backup regularly — it holds every decision, project "
     "and the celeb registry, the parts that cannot be regenerated."):
        "请定期点击「下载数据库备份」——里面是所有审核决定、项目和明星档案，"
        "这些数据丢了就找不回来了。",
    ("To change the deck's content, adjust the projects on Review · "
     "Projects and render again — a new file appears here."):
        "想调整报告内容，请回「审核 · 项目」页修改后重新生成，"
        "新文件会自动出现在这里。",
    # celebs
    ("Correct names (中文 + Latin) and each brand relationship. Tick "
     "verified only when a source confirms it — unverified labels render "
     "with a trailing ? on the deck."):
        "核对明星姓名（中文 + 拼音/英文）和各品牌的合作关系。只有找到可靠依据时"
        "才勾选「已核实」——未核实的关系在报告中会带「?」标记。",
    ("Upload photos per celeb — the first one is used on slides whenever a "
     "project's celeb has no labeled post image."):
        "可以为每位明星上传照片——当项目里没有合适的明星配图时，"
        "报告会自动使用第一张。",
    "Press Save on the card you edited.": "修改后记得点击该卡片的「保存」。",
    ("Changes apply to the next enrichment run and the next render, and the "
     "registry carries over to every future month."):
        "修改会在下一次整合和下一次生成报告时生效；明星档案长期沿用，"
        "以后每个月都不用重新填。",
    # archives
    ("Browse any archived month — its posts, decisions and projects exactly "
     "as they were, read-only."):
        "可以查看任意已归档月份的帖子、审核决定和项目，内容保持归档时的原样，"
        "仅供查看。",
    ("Nothing here affects live data. To archive the current month and "
     "start it over, use Archive & reset on the Runs page."):
        "本页的浏览不会影响当前数据。想封存当前月份并重新来过，"
        "请在「任务」页点「归档并重置」。",
    # learning
    ("Read the correction catalogue — every human keep/drop beside what the "
     "AI had decided."):
        "查看纠正记录——每一次人工保留/剔除都和 AI 当时的判定放在一起对照。",
    ("Press Update learned rules now to distill fresh corrections "
     "immediately (this also happens automatically before every filter "
     "run)."):
        "点击「立即更新筛选规则」可马上把最新的纠正总结进规则"
        "（每次筛选运行前系统也会自动更新一次）。",
    ("The updated guidance is appended to the filter prompt, so next "
     "month's filtering starts smarter. The core rules (China-market test, "
     "no perfume/beauty) never change."):
        "更新后的规则会用于之后的 AI 筛选，下个月的判定会更符合你的标准。"
        "核心标准（只看中国市场相关内容、排除香水美妆）始终不变。",
}


@jinja2.pass_context
def T(ctx, s: str) -> str:
    request = ctx.get("request")
    lang = getattr(getattr(request, "state", None), "lang", "en")
    return ZH.get(s, s) if lang == "zh" else s


def lang_of(request) -> str:
    v = request.cookies.get(LANG_COOKIE, "en")
    return v if v in LANGS else "en"
