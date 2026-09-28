.PHONY : build source clean

# 发布包版本号：make build VERSION=1.0.0 直接指定，否则打包时在终端询问
VERSION ?=
PYTHON ?= python

# exe 发布包：全部交给 build.py（图标 / 运行时资源 / zip 都在它里面，项目里不会留下 dist/）
build:
	$(PYTHON) build.py $(VERSION)

# 源码包（不含 exe，顶层目录是 Vocawiki-create-tool (版本号)/）
source:
	$(PYTHON) build.py --source $(VERSION)

clean:
	rm -rf output dist build apicache-py3 logs
	rm -f logs.txt Vocawiki-create-tool*.zip pywikibot.lwp throttle.ctrl