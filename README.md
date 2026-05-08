# WeChat Plog Sync · 微信日常Plog → Obsidian

[中文](#中文) | [English](#english)

---

## 中文

### 这是我的个人复盘工具

我建了一个叫「日常plog」的微信群——只有我一个人。每天有什么想法、发生了什么、拍了什么照片，随手就发在里面。

但群消息会慢慢沉掉，过几天就找不到了。所以写了这个工具：把群里的消息自动同步到 Obsidian 日记里，每天一篇 `YYYY-MM-DD自动爬取.md`，方便复盘。

配合 [日记整理 prompt](prompts/日记整理-prompt.md)，可以把当天的零散记录一键整理成结构化的日记。

**你也可以用它来同步任何微信群的消息到 Obsidian**——不限于个人群，工作群、兴趣群都可以。

### 功能

- 文字、图片自动同步到 Obsidian 每日笔记
- 微信 4.x 图片加密格式（V2 DAT + WXGF/HEVC）自动解码为 JPEG
- 增量追加，不重复
- Windows 定时任务（每天 23:00）或 Obsidian 一键手动同步
- 每条消息以六级标题打时间戳，Obsidian 大纲可跳转
- 支持补同步历史消息

### 效果

```
# 2026-05-08自动爬取

###### 10:55 - me
![照片](附件/2026-05-08-10-55-00.jpg)

---

###### 13:01 - me
完成了日记Agent60%

---

###### 13:49 - me
（一段多行提示词……）

---

###### 14:02 - me
![照片](附件/2026-05-08-14-02-00.jpg)
```

### 快速开始

```bash
pip install pycryptodome av
git clone https://github.com/nicedayfor/wechat-cli.git wechat-cli-src
pip install -e ./wechat-cli-src
wechat-cli init
python extract_aes_key.py
# 编辑 config.json，然后：
python plog_sync.py
```

详细配置见 `config.example.json`。

### 项目结构

```
wechat-plog-sync/
├── plog_sync.py              # 同步脚本
├── extract_aes_key.py        # 微信 AES 密钥提取器
├── config.example.json       # 配置模板
├── README.md
└── prompts/
    ├── 日记整理-prompt.md     # Obsidian 日记整理 prompt
    └── 效果图/               # 使用效果截图
```

### 常见问题

**图片全是同一张？**

不同时间发的图片被指向了同一个文件——因为微信图片文件有加密，工具原先对所有图片都返回了同一个 .dat 文件。修复方法：按 .dat 文件的修改时间匹配消息的时间戳，取最接近的那个文件，同时排除 _t.dat 缩略图文件。现在每张图片都对应各自的文件，不会再弄混了。

**图片是 .hevc 文件打不开？**

微信 4.x 的图片用 HEVC 格式存储，解码后会自动转换为 JPEG。如果转换失败会留下 .hevc 文件。运行 `python plog_sync.py --fix-hevc` 可以批量转换遗留的 .hevc 文件。如果仍然失败，检查 PyAV 是否安装正确（`pip install av`）。

**长文字消息没有出现在日记里？**

多行文字消息之前会被错误地过滤掉，因为正则表达式没有处理换行符。现已修复。

**日记格式被消息里的符号冲乱了？**

消息原文中的 Markdown 符号（如 `---`、代码块标记）会被 Obsidian 渲染，破坏整篇笔记的格式。多行文本会自动用 HTML 包裹，阻止 Markdown 解析。现在消息原文怎么发的就怎么显示，不影响笔记的其他部分。

**怎么设置自动同步？**

运行 `python plog_sync.py --install` 安装 Windows 定时任务，默认每天 23:00 自动同步。也可以安装 Obsidian Shell Commands 插件，在左侧栏添加一个刷新按钮，点击即同步。

**开机后要做什么？**

登录微信，打开 Obsidian。不需要额外操作——定时任务会自动运行，或者点一下 Obsidian 左侧的同步按钮。

**支持哪些消息类型？**

文字、图片、链接、文件、表情、语音、视频、位置、通话记录。所有类型都会保留原文或对应提示。

**如何补同步某几天的历史消息？**

`python plog_sync.py --backfill 2026-04-01 2026-04-30`

### 致谢

- [wechat-cli](https://github.com/nicedayfor/wechat-cli) — 微信数据库查询工具
- PyAV 团队 — HEVC 解码能力

### License

MIT

---

## English

### A Personal Journaling Tool

I created a WeChat group called "日常plog" (daily plog) — just me in it. Throughout the day I jot down thoughts, share photos, record what happened. But messages sink in chat history and become hard to find after a few days.

This tool syncs those messages to Obsidian daily notes — one note per day (`YYYY-MM-DD-auto.md`), making it easy to review and reflect.

Pair it with the [journaling prompt](prompts/日记整理-prompt.md) to organize scattered daily records into structured diary entries with AI.

**You can use it to sync any WeChat group to Obsidian** — personal, work, hobby groups, anything.

### Features

- Sync text and images from WeChat group to Obsidian daily notes
- Decodes WeChat 4.x proprietary image encryption (V2 DAT + WXGF/HEVC) to standard JPEG
- Incremental append — no duplicates
- Windows Task Scheduler (daily 23:00) or one-click sync from Obsidian sidebar
- H6 timestamps for each message — navigable in Obsidian Outline panel
- Backfill support for historical messages

### Quick Start

```bash
pip install pycryptodome av
git clone https://github.com/nicedayfor/wechat-cli.git wechat-cli-src
pip install -e ./wechat-cli-src
wechat-cli init
python extract_aes_key.py
# Edit config.json, then:
python plog_sync.py
```

### Project Structure

```
wechat-plog-sync/
├── plog_sync.py              # Main sync script
├── extract_aes_key.py        # AES key extractor (memory scanner)
├── config.example.json       # Configuration template
├── README.md
└── prompts/
    ├── 日记整理-prompt.md     # Obsidian journaling prompt
    └── 效果图/               # Screenshots
```

### FAQ

**All images are the same?**

The tool previously returned the same .dat file for all image messages. Fixed by matching each message to the .dat file whose modification time is closest to the message timestamp, filtering out thumbnail files. Now each image correctly maps to its own file.

**Images show as .hevc and can't be viewed?**

WeChat 4.x stores images in HEVC format. They're automatically converted to JPEG after decoding. If conversion fails, run `python plog_sync.py --fix-hevc` to retry. Check PyAV installation (`pip install av`) if issues persist.

**Long text messages missing?**

Multi-line messages were previously filtered out due to regex not handling newlines. Fixed.

**Markdown formatting in messages breaks the note?**

Message content containing Markdown syntax (like `---` or code fences) is now HTML-wrapped to prevent Obsidian from rendering it. The note stays clean.

**How to set up automatic sync?**

Run `python plog_sync.py --install` to install a Windows scheduled task (default daily at 23:00). Or install Obsidian Shell Commands plugin for a one-click sidebar button.

**What to do after reboot?**

Log into WeChat, open Obsidian. That's it — the scheduled task runs automatically, or click the sync button in Obsidian's sidebar.

**Supported message types?**

Text, image, link, file, sticker, voice, video, location, call records. All types preserved with original content or placeholders.

### Credits

- [wechat-cli](https://github.com/nicedayfor/wechat-cli) — WeChat database query tool
- PyAV team — HEVC decoding capabilities

### License

MIT
