#!/bin/bash
# 打包源码包：tar -czf "Vocawiki-create-tool (版本号).zip" dist
# 用法：./make_source.sh [版本号]   —— 不传版本号则在终端询问
set -e

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
	printf "请输入本次发布的版本号（例如 1.0.0，直接回车用 0.0.0）: " >&2
	read -r VERSION || VERSION=""
fi
VERSION="${VERSION:-0.0.0}"
VERSION="$(printf '%s' "$VERSION" | tr -d '\r' | tr '/\\:*?"<>|' '_')"   # 去 CR、文件名非法字符换成下划线

rm -rf dist
mkdir dist
cp -r config i18n models utils main.py parse_lyrics.py process_image.py README.md config_simple.yaml wiki_credentials.yaml requirements.txt dist
mv dist/config_simple.yaml dist/config.yaml
python compile_mo.py dist/i18n
tar -czf "Vocawiki-create-tool (${VERSION}).zip" dist
echo "打包完成：Vocawiki-create-tool (${VERSION}).zip"
