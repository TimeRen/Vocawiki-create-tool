# Vocawiki-create-tool Vocawiki歌曲条目辅助工具

Automatically generates Wikitext for Japanese VOCALOID songs, tailored specifically for the [Vocawiki](https://voca.wiki/). If you are not a Chinese speaker, this project is probably useless to you.

本程序用于生成自动[Vocawiki](https://voca.wiki/)的日语VOCALOID歌曲条目。本仓库主体代码来自于[MGP-VJ-tool](https://github.com/lihaohong6/MGP-VJ-tool)，并在此基础上修正了一些bug并添加新功能。

## 启动方法

从[releases](https://www.github.com/TimeRen/Vocawiki-create-tool/releases) 下载程序。将压缩包解压后运行`Vocawiki-create-tool`即可使用。

## 使用方法

程序会自动输出需要的信息，往里面填就行。

正常流程：
1. 提供日语曲名（必填）
2. 提供中文曲名（如果与日语曲名一样或没有翻译则留空）
3. 提供B站视频链接（如有）。如果填了，程序会询问视频是否是亲自投稿。
4. 提供The VOCALOID Collection所属赛道及其排名（如有）。

通过修改`config.yaml`文件，可以解锁以下功能：
1. 询问是否有投稿文。如果输入投稿文，则需要输入多行内容，结束时输入空行告诉程序输入停止。
2. 启用可视化样式编辑器：自动载入封面，可直接用吸管在封面上取色，并实时修改`VOCALOID_Songbox`三行颜色；面板分为 Songbox / Introduction / 歌词 三段——后两段支持与矩形同样的背景、边框、圆角、阴影、文字样式等设置，分别写入`VOCALOID Songbox Introduction`（`lbgcolor` / `ltcolor`，带额外声明时自动补`rbdcolor`）与`LyricsKai`（`lstyle` / `rstyle` / `containerstyle`，每项都可用「输出」开关控制是否写入代码）。
3. 自动处理中日对照的翻译。
4. 自动下载分辨率最大的封面图（niconico 的 OGP 大图优先级最高）。
5. 生成条目后弹出提交窗口，可实时预览、修改并直接提交到 Vocawiki，同时一并上传封面（见下）。
6. 以及其它默认打开的功能，如裁剪封面图片的黑边。

## AI 参考封面生成 CSS

样式编辑器面板底部有一个「**AI 参考封面生成 CSS**」按钮：把封面图与当前样式发给多模态（能看图）的 AI，让它按封面的配色与气质给出 CSS 并直接应用在编辑器里（可点「撤销这次生成」回退）。

- **生成范围**：当前编辑对象，或一次生成全部（Songbox + Introduction + 歌词）；
- **只改颜色**：勾选后只允许 AI 写颜色相关属性，不碰尺寸 / 字号 / 间距；
- **补充要求**：可写一句额外要求，例如「以封面主色为底色，做成圆角胶囊」。

密钥与 Vocawiki 密码放在同一个文件 `wiki_credentials.yaml` 里（预设 DeepSeek-V4.1-Flash，也可填 `anthropic` 用 Anthropic 消息接口）：

```yaml
ai_provider: "openai"                          # openai = OpenAI 兼容接口
ai_base_url: "https://api.deepseek.com/v1"     # 换服务商改这里
ai_model: "deepseek-flash"                     # = DeepSeek-V4.1-Flash，必须支持看图
ai_api_key: "sk-..."
ai_thinking: false                             # 仅 DeepSeek：是否开思考模式（默认关，更快更省）
```

`ai_api_key` 为空时按钮会置灰；把`config.yaml`里的`color.ai_css`设为`false`则整个 AI 面板不显示（完全断网使用）。打包（`build.py`）时会自动清空该文件里的 `username` / `password` / `ai_api_key`，不会把密钥带到分发包里。

## 歌词整理窗口（手动提取歌词）

自动找不到中文翻译时会询问是否手动输入；选「是」后弹出**歌词整理窗口**（HTML 界面，与颜色编辑器、提交窗口同一套风格）：

- 左边粘贴整段歌词（日语 / 中文 / 罗马音混在一起也行），点「**自动识别并填入**」：
  1. 日语栏已有内容时，就以它为参照把中文行挑出来；
  2. 否则按语言逐行分类（含假名=日语、纯 ASCII=罗马音、纯汉字行按段落结构判断）；
  3. 都认不出来时，按重复段结构推测「每组行数 + 各语言行号」，再点「**按行号转换**」切分。
- 四栏都可以手动修改，栏标题右侧实时显示行数；下方可填翻译者 / 翻译链接 / 来源 / 来源链接，这些会写进`LyricsKai`上方的翻译说明与`<ref>`。
- 「完成」返回整理好的歌词（日语与中文不能同时为空），「取消」放弃编辑；快捷键：`Ctrl+Enter` 完成、`Ctrl+B` 自动识别。

也可以单独运行`python parse_lyrics.py`，只打开这个窗口并打印生成的歌词段落。

## 提交窗口（实时预览 / 编辑 / 提交条目与封面）

将`config.yaml`中`wiki.submit_window`设为`true`后，程序生成 wikitext 后**不再用 VS Code 打开**输出文件，而是弹出一个提交窗口：

- 左侧可直接编辑生成的 wikitext；
- 右侧实时调用 Vocawiki 的 `action=parse` 接口渲染预览（编辑后自动刷新）：使用皮肤自带文档骨架，自动通过 API 内联 `MediaWiki:Common.css`、`MediaWiki:<皮肤>.css` 等站点 CSS（若 wiki 有配置），并把预览里尚未上传的封面替换为本地已下载的封面图；
- 点击「提交到 Vocawiki」会依次**上传封面 → 创建/更新条目 → 创建重定向**，并把编辑结果同步写回本地 wikitext 文件；
- 所有编辑请求结束后，**右下角会弹出结果通知**：成功时逐条列出封面/条目/重定向/模板同步的结果，并显示「3 秒后自动关闭窗口」倒计时，倒计时结束自动关窗（通知里也可点「在浏览器中打开条目」）；
- **提交失败时不会关窗**，错误报告同样显示在右下角并提示「窗口保持打开，可修改后按 Ctrl+Enter 重试提交」，改完可以直接再交一次；
- 若同时将`wiki.create_redirect`设为`true`，提交时会额外创建「日文原名 → 中文条目」的重定向（若该页面已存在则不会覆盖）；
- 顶部「同步大家族模板」开关（默认开启）会在条目提交成功后，把这首歌写进`== 注释 ==`里那些模板的对应小节：
  - 达到殿堂（10 万播放）及以上的加入**殿堂曲 / 传说曲 / 神话曲 / 破亿曲目**荣誉小节（同一首歌会同时写进已达成的各档，分站点 / 分年份的子分组会自动定位）；
  - 未达殿堂的写进**部分非殿堂曲**小节；
  - **P主模板**（`{{Chinozo}}`、`{{Dixie Flatline}}`…）写进**投稿年份**那一格（年份标签带不带「年」都认，`{{links|…}}` 与普通列表会照邻居的写法写）；
  - 参加了 The VOCALOID Collection 时，同步 `The VOCALOID Collection20XX季`模板里 **TOP100 / ROOKIE** 榜单对应名次的段落（榜外写「未上榜歌曲」）。
  勾选后会先显示一份「准备怎么改」的只读说明；结构认不出（没有对应小节、缺名次、模板里没有该年份等）时跳过并在状态栏说明，模板同步失败也不影响条目本身。

提交窗口是本工具**唯一**的图片上传入口：只有开启`wiki.submit_window`，封面才会随条目一并上传到 Vocawiki。

```yaml
wiki: !WikiConfig
  submit_window: true
  create_redirect: true
```

### 账号配置

在`wiki_credentials.yaml`中填写你的 Vocawiki 账号信息。出于安全考虑，建议使用**机器人密码**而非主账户密码：

1. 登录 Vocawiki，打开「参数设置 → 机器人密码」（或访问`Special:BotPasswords`）。
2. 创建一个机器人密码，授权至少勾选「上传新文件」「编辑现有页面」等权限。
3. 在`wiki_credentials.yaml`中填写：
   - `username`：`你的账户名@机器人名`（例如`TimeRen@tool`）
   - `password`：机器人密码（不是主账户密码）

```yaml
# wiki_credentials.yaml
username: "你的账户名@机器人名"
password: "机器人密码"
```

未登录时窗口仍可预览与编辑，只是无法提交。若已下载封面，程序会在打开窗口前询问封面中出现的歌姬，用于生成文件说明页的分类。

如果出现无法解决的问题，程序会
1. 询问用户该怎么办。例如：在vocadb上找到多个同名歌时让用户做决定；找不到歌曲的中文翻译时要求用户提供。
2. 崩溃。遇到这种情况请联系作者修复bug。

## 可选功能

如有需求，请在Issues催更。没有列出来的功能和未修复的bug也可以催更。如果已经有人写了Issue，请点赞让开发者知道哪些功能更受欢迎。

尽量详细描述产生需要的功能或者产生bug的原因。

错误范例："用不了，总是出错"

正确范例：输入曲名xxx，视频链接xxx之后，程序输出了以下错误信息"..."
