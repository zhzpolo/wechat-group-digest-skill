# WeChat Group Digest Skill

一个面向 Codex 的本地 Skill：读取本人已登录的 Windows 微信中，指定群聊在指定时间范围内的本地已同步记录，并生成带消息来源的中文总结、离线 HTML、PNG 长图和结构化 JSON。

它不是微信云端历史下载器，也不保证本地记录完整。它不保存数据库密钥、不生成明文数据库、不上传聊天记录。`export` 只负责真实读取；总结由调用此 Skill 的当前 Codex 会话在读完全部批次后完成，因此不需要额外模型 API Key。

## 安装为 Codex Skill

```powershell
git clone https://github.com/zhzpolo/wechat-group-digest-skill.git `
  "$env:USERPROFILE\.codex\skills\wechat-group-digest"
cd "$env:USERPROFILE\.codex\skills\wechat-group-digest"
.\scripts\setup.ps1
```

重启或刷新 Codex 后，可直接说：

> 使用 $wechat-group-digest，总结微信群“完整群名”最近 24 小时的本地记录，并生成网页和长图。

首次真实读取通常需要本人按提示从托盘退出微信，再登录同一账号。程序不会索要密码或手机确认信息。

## 当前验证范围

- Windows 11 x64、Python 3.13、Microsoft Edge。
- Windows 微信 4.1.15.12；已验证的 DLL SHA-256 见 [操作说明](references/OPERATIONS.md)。其他构建必须重新生成并验证本地 anchor。
- 虚构数据库覆盖页级 HMAC、WAL 提交边界、秒/毫秒时间戳、跨分片、去重、引用、媒体标记、HTML 手机/桌面排版和 PNG 分图。
- 一次真实端到端验证覆盖六个加密数据库、五个消息分片、精确群 ID、固定 24 小时窗口、全部批次总结和离线渲染。公开仓库不含真实群名、群 ID、消息、账号路径或数据库摘要。

详细流程由 [SKILL.md](SKILL.md) 驱动。人工运行命令、失败规则和隐私边界见 [OPERATIONS.md](references/OPERATIONS.md)，研究来源见 [RESEARCH.md](references/RESEARCH.md)。

## 输出

每次真实运行创建独立目录：

- `messages.json` / `messages.txt`：完整结构化消息和复核文本；
- `batches/`：确保 Codex 覆盖全部消息的分批文件和哈希清单；
- `report.json`：带实际消息 ID 和连续原文摘录的结构化总结；
- `summary.md`：中文总结；
- `index.html`：无 CDN、远程字体或脚本的离线网页；
- `report.png`：按内容高度渲染；过长时生成编号分图和清单。

## 开发验证

```powershell
.\scripts\setup.ps1
.\scripts\run.ps1 doctor
.\scripts\run.ps1 demo
$env:PYTHONPATH = (Resolve-Path .\scripts)
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
```

## 许可证

Apache-2.0。第三方来源与变更说明见 [NOTICE](NOTICE)。安全问题请阅读 [SECURITY.md](SECURITY.md)。
