"""单元测试包。

⚠️ 下面这个副作用是**故意的**：把「编辑速率墙」换成空操作（`utils/rate_limit.wait_for_slot`）。

默认限额是每分钟 3 次编辑，而不少用例会连着调真正发请求的 `wiki_api.edit_page` /
`move_page` / `upload_image`（HTTP 层已经打桩）—— 不换掉的话，同一个测试进程里跑到第 4 次
就会**真睡 20~60 秒**，整个测试套件会被拖垮甚至卡死。
`tests/utils/test_rate_limit.py` 会自己把它换回真身，用假时钟测那一套逻辑。
"""
from utils import rate_limit

real_wait_for_slot = rate_limit.wait_for_slot      # 给 test_rate_limit 用

rate_limit.wait_for_slot = lambda: 0.0
