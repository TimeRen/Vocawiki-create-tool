#!/bin/bash
# 打包源码包（不含 exe）：顶层目录是 Vocawiki-create-tool (版本号)/，项目里不会留下 dist/
# 具体逻辑在 build.py 的 --source 模式里（Python 的 zipfile 直接出 zip，不需要 tar/zip 命令）
# 用法：./make_source.sh [版本号]   —— 不传版本号则在终端询问
set -e
cd "$(dirname "$0")"
exec "${PYTHON:-python}" build.py --source "$@"
