"""对外的「身份」：程序名、仓库地址，以及请求头里的 User-Agent。

MediaWiki 的 [User-Agent 政策](https://meta.wikimedia.org/wiki/User-Agent_policy) 要求
机器人/工具的 UA 里带上**能联系到作者的信息**。所以工具自己的 UA 写的是**仓库地址**，
而不是 voca.wiki：看到这条日志的人能顺着找到这是什么工具、去哪儿反馈，
也不会让人误以为是 voca.wiki 官方在发请求。

分工（改这里之前先想清楚要去哪个站点）：

* `USER_AGENT` —— **只发给用户配置的那个 wiki**（`wiki.api_url`，见 `utils/login.py`
  里的 `WikiSession`，它按主机名强制把关：换个域名就换成 `BROWSER_USER_AGENT`）。
  MediaWiki 的 UA 政策要求工具自报家门，所以这条 UA 里写着仓库地址。
* `BROWSER_USER_AGENT` —— **除那个 wiki 之外的所有站点**（抓 niconico / YouTube / bilibili /
  vocadb / 网易云 / 巴哈姆特… 都走 `utils/helpers.http_get`，它一条请求就直接用这个 UA）。
  它们对非浏览器 UA 常直接 403 或给残缺页面，所以套一个普通的桌面 Chrome UA
  （这是抓取时的通行做法，不代表我们伪装成浏览器去登录 / 提交数据）。
  用户 2026-09 改的规矩：**站外不要先发工具 UA 再降级**，省掉那次多余的请求。
* **例外：`vocadb.net/api/*` 发 `USER_AGENT`**（用户 2026-10-03）。VocaDB 的
  [API 文档](https://wiki.vocadb.net/docs/public-api) 在「API usage rules」里明确要求
  「用自定义 User-Agent 方便我们识别流量来源」；那是公开 JSON 接口，看到这条 UA 的人
  能顺着仓库地址找到这是什么工具。见 `utils/vocadb.py` 的 `_vocadb_headers()`
  （有个 `vocadb_browser_ua` 开关可以换回浏览器 UA）。
* **AI 接口**（`utils/ai_css._post` 等）不带任何 UA，走 requests 默认的
  `python-requests/x.y` —— AI 是密钥鉴权，没必要把工具身份写进第三方日志。
"""

APP_NAME = "Vocawiki-create-tool"
# 反馈、issue、源码都在这里；UA 里写它（**不要**写成 voca.wiki）
REPO_URL = "https://github.com/TimeRen/Vocawiki-create-tool"
# 只出现在 UA 里。打包用的版本号是 build.py 运行时问出来的，程序运行期拿不到，
# 所以这里单独维护一个，发版时顺手改一下就行。
VERSION = "2.4.0"

USER_AGENT = f"{APP_NAME}/{VERSION} (+{REPO_URL})"

# 抓站用的普通桌面 Chrome UA（见模块开头的分工说明）
BROWSER_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

__all__ = ["APP_NAME", "REPO_URL", "VERSION", "USER_AGENT", "BROWSER_USER_AGENT"]
