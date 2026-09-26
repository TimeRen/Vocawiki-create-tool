.PHONY : build clean

# 发布包版本号：make build VERSION=1.0.0 直接指定，否则打包时在终端询问
VERSION ?=

build: dist
	@set -e; \
	 v="$(VERSION)"; \
	 if [ -z "$$v" ]; then printf "请输入本次发布的版本号（例如 1.0.0，直接回车用 0.0.0）: " >&2; read v || true; fi; \
	 v=$${v:-0.0.0}; \
	 v=$$(printf '%s' "$$v" | tr -d '\r' | tr '/\\:*?"<>|' '_'); \
	 file_name="Vocawiki-create-tool ($$v).zip"; \
	 echo "发布包：$$file_name"; \
	 cp README.md config_simple.yaml dist; \
	 mv dist/config_simple.yaml dist/config.yaml; \
	 cp wiki_credentials.yaml dist; \
	 cp -r i18n dist; \
	 python compile_mo.py dist/i18n; \
	 tar -czf "$$file_name" dist; \
	 rm -rf build main.spec

dist: main.py
	pyinstaller --onefile --noconfirm --windowed --name Vocawiki-create-tool main.py
# change to rx r r so that the program can be executed
# ignores the error generated under Windows
	chmod 544 dist/Vocawiki-create-tool || true

clean:
	rm -rf output dist build apicache-py3 logs
	rm -f logs.txt Vocawiki-create-tool*.zip pywikibot.lwp throttle.ctrl