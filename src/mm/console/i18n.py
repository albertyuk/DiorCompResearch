"""Console i18n — English source strings with a Chinese toggle.

Templates wrap UI chrome in {{ T("…") }}. T is a Jinja context function: it
returns the Chinese translation when the request's mm_lang cookie is "zh",
otherwise the English source string itself. A missing dictionary entry falls
through to English, so an untranslated key can never blank the UI. Post
content, names and pipeline telemetry are never translated (the content is
already Chinese; the activity log is technical logging).

Parametrized strings use {n}/{m}-style slots substituted in the template
AFTER translation, so word order stays free per language.
"""
from __future__ import annotations

import jinja2

LANGS = ("en", "zh")
LANG_COOKIE = "mm_lang"

ZH: dict[str, str] = {
    # ── nav / chrome ─────────────────────────────────────────────────────
    "Runs": "运行",
    "Review · Posts": "审核 · 帖子",
    "Review · Projects": "审核 · 项目",
    "Decks": "幻灯片",
    "Celebs": "名人",
    "Archives": "归档",
    "Learning": "学习",
    "Sign out": "退出",
    "Dark": "深色",
    "Light": "浅色",
    "Toggle dark mode": "切换深色模式",
    # ── shared bits ──────────────────────────────────────────────────────
    "Save": "保存",
    "Confirm": "确认",
    "Download": "下载",
    "Browse": "查看",
    "Resolve": "解析",
    "post ↗": "帖子 ↗",
    "profile ↗": "主页 ↗",
    "evidence ↗": "证据 ↗",
    "source ↗": "来源 ↗",
    "why?": "原因？",
    "rationale": "判定理由",
    "not filtered yet": "尚未过滤",
    "required": "必需",
    "optional": "可选",
    "status: ": "状态：",
    "activity log": "活动日志",
    "(no activity yet)": "（暂无活动）",
    # ── runs page ────────────────────────────────────────────────────────
    "Accounts": "账号",
    ("Accounts marked required must be confirmed before a run can start "
     "(Weibo is the ingestion source). The rest are optional — resolve them "
     "to populate the SOCIAL column, or skip them."):
        "标记为“必需”的账号须在启动前确认（微博是抓取来源）。其余为可选——"
        "解析后可填充 SOCIAL 列，也可以跳过。",
    "Brand": "品牌",
    "Platform": "平台",
    "Lookup query": "搜索词",
    "Start a month": "启动月份",
    "Start month": "启动本月",
    ("Runs ingest (Weibo) then the LLM relevance filter, then pauses at "
     "Review · Posts."):
        "先抓取微博，再运行 LLM 相关性过滤，然后暂停在「审核 · 帖子」。",
    "Months": "月份",
    "No runs yet.": "还没有运行记录。",
    "Open review": "打开审核",
    "Stop": "停止",
    "Archive & reset": "归档并重置",
    "Pause the {m} run at its next safe point?":
        "在下一个安全点暂停 {m} 的运行？",
    ("Archive {m}? All posts, decisions and projects move to the archive "
     "(kept in the database, listed below) and the next Start month searches "
     "completely from scratch."):
        "归档 {m}？所有帖子、决定和项目将移入归档（仍保存在数据库中，"
        "见下方列表），下次「启动本月」将完全重新搜索。",
    "last started {t} by {who}": "最近启动 {t} · 由 {who}",
    "archive #{id} · {t} by {who} · {p} posts · {pr} projects":
        "归档 #{id} · {t} · 由 {who} · {p} 条帖子 · {pr} 个项目",
    "RUNNING": "运行中",
    "INTERRUPTED": "已中断",
    "idle": "空闲",
    "background — ": "后台 — ",
    "posts": "帖子",
    "projects": "项目",
    ("was interrupted (server restart) — press Start month (or re-confirm "
     "the last checkpoint) to resume; everything already fetched and every "
     "paid LLM verdict is kept."):
        "已中断（服务器重启）——按「启动本月」（或重新确认上一个检查点）即可恢复；"
        "已抓取的内容和已付费的 LLM 判定全部保留。",
    # phase names + statuses (pills)
    "ingest": "抓取",
    "filter": "过滤",
    "review_posts": "审核帖子",
    "crosscheck": "跨平台核对",
    "enrich": "整理",
    "review_projects": "审核项目",
    "render": "生成",
    "done": "完成",
    "confirmed": "已确认",
    "waiting": "等待中",
    "running": "运行中",
    # ── login ────────────────────────────────────────────────────────────
    "Your name": "你的名字",
    "Team passphrase": "团队口令",
    "Enter": "进入",
    ("Review decisions are attributed to the name you enter. The passphrase "
     "is shared out-of-band — ask the deck owner."):
        "审核决定会记录在你输入的名字下。口令线下共享——请询问报告负责人。",
    # ── review · posts ───────────────────────────────────────────────────
    "Review checkpoint #1 — posts": "审核检查点 #1 — 帖子",
    ("Confirm locks these decisions and starts cross-platform verification "
     "+ enrichment. Needs review posts are listed first."):
        "确认后将锁定这些决定，并开始跨平台核对与整理。「需人工复核」的帖子排在最前。",
    "Confirm & continue →": "确认并继续 →",
    "Lock post decisions and start cross-check + enrichment?":
        "锁定帖子决定并开始跨平台核对与整理？",
    "A run is working on {m} right now": "{m} 正在运行",
    "started {t} by {who}": "启动于 {t} · 由 {who}",
    ("This page shows the last completed state; freshly ingested posts "
     "appear here after the filter step. It refreshes itself when the run "
     "pauses."):
        "本页显示的是最近一次完成的状态；新抓取的帖子会在过滤后出现。"
        "运行暂停时页面会自动刷新。",
    "weibo posts": "条微博帖子",
    "Nothing ingested.": "尚未抓取到内容。",
    "Post": "帖子",
    "Filter verdict": "过滤判定",
    "Decision": "决定",
    "repost w/ commentary": "带评论转发",
    "needs review": "需人工复核",
    "KEEP": "保留",
    "DROP": "剔除",
    "keep": "保留",
    "drop": "剔除",
    "human: ": "人工：",
    "Restore": "恢复",
    "Delete": "删除",
    "Keep": "保留",
    "Click to preview all images": "点击预览全部图片",
    "Click to preview": "点击预览",
    "Slide images are chosen at Review · Projects, after consolidation.":
        "幻灯片图片在「审核 · 项目」（合并后）中选择。",
    "working…": "运行中…",
    "~{t} left": "预计剩余 {t}",
    "min": "分钟",
    "s": "秒",
    # ── review · projects ────────────────────────────────────────────────
    "Review checkpoint #2 — projects": "审核检查点 #2 — 项目",
    ("Each project lists the posts consolidated into it — tick the images "
     "that should render on the slide (or drop an HQ original). Drag the ⠿ "
     "handle of a project onto another project to merge them; drag a post's "
     "⠿ handle to move just that post. Celeb names, relations and photo "
     "libraries live on the"):
        "每个项目列出并入它的帖子——勾选要放到幻灯片上的图片（或拖入高清原图）。"
        "把项目的 ⠿ 手柄拖到另一个项目上可合并；拖动帖子的 ⠿ 手柄可只移动该帖子。"
        "名人的名字、与品牌的关系及照片库在",
    "Celebs page": "「名人」页面",
    "visuals: live screenshots (weibo) + cards": "视觉素材：实时截图（微博）+ 卡片",
    "visuals: cards only": "视觉素材：仅卡片",
    "Confirm & render →": "确认并生成 →",
    "Confirm projects and render the deck?": "确认项目并生成幻灯片？",
    "posts confirmed by {who} · {t}": "帖子由 {who} 确认 · {t}",
    "last render by {who} · {t}": "最近生成：{who} · {t}",
    ("Projects below are the last completed state; the list refreshes "
     "itself when cross-check / enrichment / render finishes."):
        "以下项目为最近一次完成的状态；跨平台核对/整理/生成结束后列表会自动刷新。",
    "Why these posts are grouped": "为什么这些帖子被归为一组",
    "Title / phase": "标题 / 阶段",
    "Project line (table cell)": "项目描述（表格单元格）",
    "Celebs on this project": "本项目的名人",
    "(name as shown on the slide · relation label)": "（幻灯片上的名字 · 关系标签）",
    "remove": "移除",
    "+ add celeb (slide name)": "+ 添加名人（幻灯片名字）",
    "relation label": "关系标签",
    "Dates": "日期",
    "ongoing (— TBD)": "进行中（— TBD）",
    "Assets": "素材类型",
    "Platforms (SOCIAL column)": "平台（SOCIAL 列）",
    "Hero media": "主视觉",
    "Consolidated posts — {n}": "已合并帖子 — {n}",
    ("· tick an image to put it on the slide · drop an HQ original on the "
     "drop zone · drag ⠿ to move a post to another project · ✕ remove takes "
     "one post out, the rest stay grouped"):
        "· 勾选图片放入幻灯片 · 把高清原图拖到虚线框 · 拖动 ⠿ 将帖子移到其他项目 "
        "· ✕ 移除只取出该帖子，其余保持成组",
    "matched": "已匹配",
    "Drag onto another project to move this post": "拖到另一个项目即可移动该帖子",
    "tick = this image goes on the slide": "勾选 = 此图片放入幻灯片",
    "drop HQ<br>image": "拖入高清<br>图片",
    "No posts attached — drag posts here from another project, or drop the project.":
        "没有帖子——可以从其他项目拖入，或删除该项目。",
    "Drop project": "删除项目",
    "Restore project": "恢复项目",
    "Ungroup ({n} posts)": "拆分（{n} 条帖子）",
    ("Split this project into {n} single-post projects? Matched "
     "cross-platform posts return to the orphan list."):
        "将该项目拆分为 {n} 个单帖项目？已匹配的跨平台帖子会回到未匹配列表。",
    ("Remove just this post from the group? It becomes its own project."):
        "只把这条帖子移出该组？它将成为独立项目。",
    ("Detach this matched {p} post from the group? It returns to the orphan "
     "list."):
        "把这条已匹配的 {p} 帖子移出该组？它会回到未匹配列表。",
    "Remove only this post from the group — the rest stay together":
        "只移除这条帖子——其余保持成组",
    "✕ remove": "✕ 移除",
    "Orphans": "未匹配帖子",
    ("— cross-platform posts matching no kept Weibo post, sifted by the "
     "same filter as review #1"):
        "— 未匹配到任何保留微博帖子的跨平台帖子，已用与审核 #1 相同的过滤器筛选",
    ("Keeps surface first; greyed rows were dropped by the filter (open the "
     "rationale to see why). Drag an orphan's ⠿ handle onto a project to "
     "attach it there, or promote it to its own project."):
        "「保留」排在最前；灰色行是被过滤器剔除的（展开判定理由可见原因）。"
        "把未匹配帖子的 ⠿ 手柄拖到某个项目上可归入该项目，或将其提升为独立项目。",
    "Drag onto a project to attach": "拖到项目上即可归入",
    "Promote to project": "提升为项目",
    "Ignore": "忽略",
    ("Merge the dragged project into this one? Its posts, celebs and "
     "platform evidence move here."):
        "把拖动的项目并入这个项目？其帖子、名人和平台证据都会移过来。",
    "Move failed: ": "移动失败：",
    "Drag onto another project to merge": "拖到另一个项目上可合并",
    ("Projects below are the last completed state; the list refreshes itself "
     "when cross-check / enrichment / render finishes."):
        "以下项目为最近一次完成的状态；跨平台核对/整理/生成结束后列表会自动刷新。",
    "Could not save selection: ": "保存选择失败：",
    "Upload failed: ": "上传失败：",
    "Delete failed: ": "删除失败：",
    # ── lightbox ─────────────────────────────────────────────────────────
    "Use on the slide": "用于幻灯片",
    "✓ On the slide — click to remove": "✓ 已用于幻灯片 — 点击取消",
    "HQ upload": "高清上传",
    "Close (Esc)": "关闭（Esc）",
    "Previous (←)": "上一张（←）",
    "Next (→)": "下一张（→）",
    # ── celebs page ──────────────────────────────────────────────────────
    "Celebrity registry": "名人档案",
    ("Names, brand relationships and photo libraries compound across "
     "months. Relations verified from a caption or a source keep their "
     "label; unverified ones render with a trailing ? on the deck. Photos "
     "uploaded here are used on slides when a project's celeb has no "
     "labeled post image — the first photo wins; click any photo to "
     "preview the library."):
        "名字、与品牌的关系和照片库会跨月份累积。经文案或来源核实的关系保留其标签；"
        "未核实的在幻灯片上带「?」后缀。当项目中的名人没有带标签的帖子图片时，"
        "此处上传的照片会用于幻灯片——取第一张；点击任意照片可预览照片库。",
    "Add celeb": "添加名人",
    "Name (中文 · Latin · occupation)": "名字（中文 · 拉丁 · 职业）",
    "Relationship per brand": "与各品牌的关系",
    "(blank = none; tick verified when a source confirms it)":
        "（留空 = 无；有来源证实时勾选“已核实”）",
    "verified": "已核实",
    "Photos — first one is used on slides": "照片 — 第一张用于幻灯片",
    "Remove from the library": "从照片库移除",
    "drop or click<br>to add photo": "拖入或点击<br>添加照片",
    "Remove this photo from the library?": "从照片库移除这张照片？",
    "No celebs yet — the registry fills itself as enrichment runs, or add one above.":
        "还没有名人——整理阶段会自动填充档案，也可以在上方手动添加。",
    # ── decks page ───────────────────────────────────────────────────────
    "Deck archive": "幻灯片存档",
    "Download database backup": "下载数据库备份",
    ("The backup is the SQLite database (projects, review decisions, "
     "celebrity registry, audit trail) — the part that can't be "
     "regenerated. Media is re-fetchable and decks re-renderable."):
        "备份是 SQLite 数据库（项目、审核决定、名人档案、操作记录）——"
        "这些无法重新生成。媒体可重新抓取，幻灯片可重新渲染。",
    "Nothing rendered yet.": "还没有生成任何文件。",
    "File": "文件",
    "Size": "大小",
    # ── archives ─────────────────────────────────────────────────────────
    "Archived searches": "已归档的搜索",
    ("Each Archive & reset snapshots a month's entire search — posts, "
     "filter verdicts, human decisions, projects — and clears the live "
     "tables so the next Start month searches from scratch. Nothing is "
     "deleted: browse any archive below."):
        "每次「归档并重置」都会为该月的整个搜索建立快照——帖子、过滤判定、"
        "人工决定、项目——并清空在用表，下次「启动本月」将从零开始搜索。"
        "不会删除任何数据：可在下方浏览任意归档。",
    "No archives yet.": "还没有归档。",
    "Month": "月份",
    "Archived": "归档时间",
    "By": "操作人",
    "Posts": "帖子",
    "Projects": "项目",
    "Archive #{id} — {m}": "归档 #{id} — {m}",
    "archived {t} by {who}": "归档于 {t} · 由 {who}",
    ("Read-only snapshot of the search as it was when archived. Greyed "
     "posts were dropped (by the filter or a reviewer) at the time."):
        "归档时搜索状态的只读快照。灰色帖子当时已被剔除（由过滤器或审核人）。",
    "← All archives": "← 全部归档",
    "Verdict at archive time": "归档时的判定",
    "llm: ": "LLM：",
    "no verdict": "无判定",
    "Projects at archive time": "归档时的项目",
    "Title": "标题",
    "Status": "状态",
    "Description": "描述",
    # ── learning ─────────────────────────────────────────────────────────
    "Filter learning": "过滤器学习",
    ("Every keep/drop you make in review is catalogued below with what the "
     "LLM had decided. Before each filter run — or on demand here — the "
     "corrections are distilled into learned guidance appended to the "
     "filter prompt, so the filter gets more accurate over time. The core "
     "rubric (China-market test, perfume/beauty excluded) never changes."):
        "你在审核中的每次保留/剔除都会与 LLM 当时的判定一起记录在下方。"
        "每次过滤运行前——或在此手动触发——这些修正会被提炼为「学习到的准则」"
        "附加到过滤提示词中，使过滤器越来越准确。核心规则（中国市场判断、"
        "剔除香水/美妆）永不改变。",
    "Update learned rules now": "立即更新学习准则",
    "Distill the corrections into updated filter guidance now?":
        "现在将修正提炼为新的过滤准则？",
    "{n} new corrections since last update": "自上次更新以来有 {n} 条新修正",
    "Current learned guidance": "当前学习到的准则",
    "None yet — it appears after the first corrections are distilled.":
        "暂无——首批修正被提炼后会出现在这里。",
    "previous versions": "历史版本",
    "Correction catalogue": "修正记录",
    "— last {n}": "— 最近 {n} 条",
    "No human decisions recorded yet.": "还没有人工决定记录。",
    "When / who": "时间 / 操作人",
    "LLM → human": "LLM → 人工",
    "Post caption": "帖子文案",
    "LLM rationale": "LLM 判定理由",
    "override": "推翻",
    # ── resolve ──────────────────────────────────────────────────────────
    "Confirm {b} — {p}": "确认 {b} — {p}",
    ("Confirming freezes the internal ID into config/brands.yaml and flips "
     "the account to verified."):
        "确认后内部 ID 将写入 config/brands.yaml，账号状态改为已核实。",
    "Candidates for": "候选账号：",
    "Lookup failed: ": "查询失败：",
    "Name": "名字",
    "Followers": "粉丝数",
    "Verification": "认证信息",
    "Manual entry": "手动输入",
    "display name (optional)": "显示名（可选）",
    # ── per-page guides ──────────────────────────────────────────────────
    "How this page works": "本页使用说明",
    "What happens next": "接下来会发生什么",
    # runs
    ("Enter the month to search (YYYY-MM) and press Start month. The system "
     "pulls every brand's Weibo timeline for that month, then the AI filter "
     "marks each post keep or drop."):
        "输入要搜索的月份（YYYY-MM），点击「启动本月」。系统会抓取每个品牌该月的"
        "微博时间线，然后由 AI 过滤器将每条帖子标记为保留或剔除。",
    ("While it runs you'll see RUNNING, a progress bar and the activity log "
     "under the month. Stop pauses at the next safe point; Start month "
     "resumes exactly where it left off."):
        "运行期间月份下方会显示「运行中」、进度条和活动日志。「停止」会在下一个"
        "安全点暂停；再次「启动本月」会从中断处继续。",
    ("When the filter pill shows done and review_posts shows waiting, press "
     "Open review."):
        "当「过滤」显示完成、「审核帖子」显示等待中时，点击「打开审核」。",
    ("The run waits at Review · Posts for your keep/drop decisions — nothing "
     "continues until you confirm there. Archive & reset moves a finished "
     "month into Archives so a fresh search can start from scratch."):
        "运行会停在「审核 · 帖子」等待你的保留/剔除决定——在那里确认之前不会继续。"
        "「归档并重置」把已完成的月份移入归档，下次可以从零开始重新搜索。",
    # posts
    ("Work brand by brand. Greyed rows are dropped; everything else goes "
     "forward. Posts flagged needs review are listed first — decide those."):
        "逐个品牌检查。灰色行为已剔除；其余都会进入下一步。标记「需人工复核」的"
        "帖子排在最前——请优先处理。",
    ("Click any thumbnail to preview the images full-size. Open why? to see "
     "the AI's reasoning for each verdict."):
        "点击任意缩略图可全尺寸预览图片。展开「原因？」可查看 AI 每条判定的理由。",
    ("Use Delete / Keep / Restore to overrule the filter. Every correction "
     "is remembered and makes the filter more accurate next month (see "
     "Learning)."):
        "用「删除 / 保留 / 恢复」推翻过滤器的判定。每次修正都会被记住，让下个月的"
        "过滤更准确（见「学习」页）。",
    "When the keeps look right, press Confirm & continue.":
        "确认保留的帖子无误后，点击「确认并继续 →」。",
    ("Cross-platform verification (Douyin / RED / WeChat) and grouping into "
     "projects run automatically — usually a few minutes. When the run "
     "pauses again, continue on Review · Projects. If you change decisions "
     "here afterwards, press Confirm & continue again to regroup."):
        "跨平台核对（抖音/小红书/微信）与项目归组会自动运行——通常几分钟。再次暂停后，"
        "请前往「审核 · 项目」继续。若之后在本页改动了决定，再点一次「确认并继续」"
        "即可重新归组。",
    # projects
    ("Check each project's grouping — read Why these posts are grouped. Fix "
     "mistakes by dragging the ⠿ handles (merge two projects, or move a "
     "single post), with ✕ remove, or with Ungroup."):
        "检查每个项目的归组——阅读「为什么这些帖子被归为一组」。有误时可拖动 ⠿ 手柄"
        "（合并两个项目，或移动单条帖子）、用「✕ 移除」，或用「拆分」。",
    ("Tick the images that should appear on the slide (click any image to "
     "preview it full-size; drag an HQ original onto the dashed box). "
     "Nothing ticked = an automatic post card is used."):
        "勾选要出现在幻灯片上的图片（点击可全尺寸预览；把高清原图拖到虚线框内）。"
        "全部不勾选 = 使用自动生成的帖子卡片。",
    ("Edit the title, dates, description line, celebs and platform ticks, "
     "then press Save on that project."):
        "编辑标题、日期、描述、名人和平台勾选，然后在该项目上点「保存」。",
    ("Review the Orphans at the bottom — keeps come first. Drag one onto a "
     "project, Promote it to its own project, or Ignore it."):
        "查看底部的「未匹配帖子」——保留的排在最前。可拖到某个项目上、"
        "「提升为项目」，或「忽略」。",
    "Press Confirm & render and watch the progress bar.":
        "点击「确认并生成 →」，然后关注进度条。",
    ("The PPTX deck and the XLSX spreadsheet are generated and appear on "
     "the Decks page. Render as often as you like — every edit here is "
     "picked up by the next render."):
        "系统会生成 PPTX 幻灯片和 XLSX 表格，出现在「幻灯片」页面。可以随时重新"
        "生成——本页的每次修改都会体现在下一次生成中。",
    # decks
    ("Download the newest PPTX (the deck) and XLSX (the project "
     "spreadsheet) — filenames carry the month."):
        "下载最新的 PPTX（幻灯片）和 XLSX（项目表格）——文件名包含月份。",
    ("Download database backup regularly — it holds every decision, project "
     "and the celeb registry, the parts that cannot be regenerated."):
        "请定期「下载数据库备份」——其中包含所有决定、项目和名人档案，"
        "这些内容无法重新生成。",
    ("To change the deck's content, adjust the projects on Review · "
     "Projects and render again — a new file appears here."):
        "要修改幻灯片内容，请在「审核 · 项目」调整项目后重新生成——"
        "新文件会出现在这里。",
    # celebs
    ("Correct names (中文 + Latin) and each brand relationship. Tick "
     "verified only when a source confirms it — unverified labels render "
     "with a trailing ? on the deck."):
        "修正名字（中文 + 拉丁）及与各品牌的关系。只有在有来源证实时才勾选"
        "「已核实」——未核实的标签在幻灯片上会带「?」后缀。",
    ("Upload photos per celeb — the first one is used on slides whenever a "
     "project's celeb has no labeled post image."):
        "为每位名人上传照片——当项目中的名人没有带标签的帖子图片时，"
        "幻灯片会使用第一张照片。",
    "Press Save on the card you edited.": "在编辑过的卡片上点「保存」。",
    ("Changes apply to the next enrichment run and the next render, and the "
     "registry carries over to every future month."):
        "修改会应用于下一次整理和下一次生成；档案会延续到之后的每个月份。",
    # archives
    ("Browse any archived month — its posts, decisions and projects exactly "
     "as they were, read-only."):
        "浏览任意已归档的月份——帖子、决定和项目保持原样，只读。",
    ("Nothing here affects live data. To archive the current month and "
     "start it over, use Archive & reset on the Runs page."):
        "本页不影响在用数据。要归档当前月份并重新开始，请在「运行」页使用"
        "「归档并重置」。",
    # learning
    ("Read the correction catalogue — every human keep/drop beside what the "
     "AI had decided."):
        "查看修正记录——每次人工保留/剔除都与 AI 当时的判定并列显示。",
    ("Press Update learned rules now to distill fresh corrections "
     "immediately (this also happens automatically before every filter "
     "run)."):
        "点击「立即更新学习准则」可马上提炼新的修正（每次过滤运行前也会自动进行）。",
    ("The updated guidance is appended to the filter prompt, so next "
     "month's filtering starts smarter. The core rules (China-market test, "
     "no perfume/beauty) never change."):
        "更新后的准则会附加到过滤提示词中，下个月的过滤会更聪明。核心规则"
        "（中国市场判断、剔除香水/美妆）永不改变。",
}


@jinja2.pass_context
def T(ctx, s: str) -> str:
    request = ctx.get("request")
    lang = getattr(getattr(request, "state", None), "lang", "en")
    return ZH.get(s, s) if lang == "zh" else s


def lang_of(request) -> str:
    v = request.cookies.get(LANG_COOKIE, "en")
    return v if v in LANGS else "en"
