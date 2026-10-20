#!/usr/bin/env bash
# 在 WSL/Linux 中构建 Android APK。Windows 侧由 build.bat apk 调用。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${1:-0.1.0}"
MODE="${2:-debug}"

# pipx / pip --user / 独立 venv 都可能装到这里；build.bat 走非交互 bash 时也要能找到。
export PATH="$HOME/.buildozer-venv/bin:$HOME/.local/bin:$PATH"

# 直接调用 venv 里的 buildozer 不会设置 VIRTUAL_ENV，buildozer 会改用
# `pip install --user` 安装 p4a 依赖，而 venv 中该方式不可用。
if [ -z "${VIRTUAL_ENV:-}" ] && [ -x "$HOME/.buildozer-venv/bin/python" ]; then
    export VIRTUAL_ENV="$HOME/.buildozer-venv"
fi

# Android Gradle 需要 Java 17；优先用 apt 装到标准位置的 17。
for candidate in /usr/lib/jvm/java-17-openjdk-amd64 /usr/lib/jvm/java-17-openjdk; do
    if [ -d "$candidate" ]; then
        export JAVA_HOME="$candidate"
        export PATH="$JAVA_HOME/bin:$PATH"
        break
    fi
done

if ! command -v python3 >/dev/null 2>&1; then
    echo "[FAILED] 没找到 python3，请先安装：sudo apt install -y python3 python3-venv" >&2
    exit 1
fi

if ! command -v buildozer >/dev/null 2>&1; then
    cat >&2 <<'EOF'
[FAILED] 没找到 buildozer。
请先在 WSL/Ubuntu 里执行（建议用独立 venv，避开 Ubuntu 的 PEP 668 限制）：
  sudo apt update
  sudo apt install -y python3 python3-venv openjdk-17-jdk unzip git zip
  python3 -m venv "$HOME/.buildozer-venv"
  "$HOME/.buildozer-venv/bin/pip" install --upgrade pip buildozer cython
  echo 'export PATH="$HOME/.buildozer-venv/bin:$PATH"' >> ~/.bashrc
EOF
    exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9A-Za-z._-]+$ ]]; then
    echo "[FAILED] 版本号只能包含字母、数字、点、下划线或连字符：$VERSION" >&2
    exit 1
fi

# .mo 在 .gitignore 里，CI 检出的仓库只有 .po；先编译，否则 APK 里没有翻译。
python3 "$ROOT/compile_mo.py" "$ROOT/i18n"

cd "$ROOT/mobile"

python3 - "$VERSION" <<'PY'
import pathlib
import sys

version = sys.argv[1].strip() or "0.0.0"
template = pathlib.Path("buildozer.spec.in").read_text(encoding="utf-8")
# Windows 检出可能是 CRLF；Buildozer 在 Linux 下运行，统一写成 LF。
template = template.replace("\r\n", "\n").replace("\r", "\n")
with pathlib.Path("buildozer.spec").open("w", encoding="utf-8", newline="\n") as handle:
    handle.write(template.replace("__VERSION__", version))
print(f"[apk] buildozer.spec version = {version}")
PY

# 版本号 / 构建时间 / 提交号写进 _build_info.py（设置页「检查更新」靠它报当前版本）。
python3 - "$ROOT" "$VERSION" <<'PY'
import datetime
import os
import pathlib
import subprocess
import sys

root, version = pathlib.Path(sys.argv[1]), sys.argv[2]
commit = os.environ.get("GITHUB_SHA", "").strip()
if not commit:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = ""
built_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
(root / "_build_info.py").write_text(
    '"""本次构建的信息（由 build.py / build_android.sh 生成，别手改）。"""\n\n'
    f"VERSION = {version!r}\nBUILT_AT = {built_at!r}\nCOMMIT = {commit!r}\n",
    encoding="utf-8")
print(f"[apk] 构建信息：版本 {version}，{built_at}，提交 {commit or '未知'}")
PY

TARGET="$ROOT/Vocawiki-create-tool (${VERSION}).apk"
rm -f "$TARGET"

if [ "$MODE" = "release" ]; then
    if [ -z "${P4A_RELEASE_KEYSTORE:-}" ] || [ -z "${P4A_RELEASE_KEYSTORE_PASSWD:-}" ] \
       || [ -z "${P4A_RELEASE_KEYALIAS:-}" ]; then
        echo "[FAILED] release 需要 P4A_RELEASE_KEYSTORE / P4A_RELEASE_KEYSTORE_PASSWD / P4A_RELEASE_KEYALIAS" >&2
        exit 1
    fi
    echo "[apk] building release APK ..."
    buildozer -v android release
else
    echo "[apk] building debug APK ..."
    buildozer -v android debug
fi

APK="$(find bin -maxdepth 1 -name '*.apk' -type f -printf '%T@ %p\n' 2>/dev/null \
      | sort -nr | head -n1 | cut -d' ' -f2- || true)"
if [ -z "$APK" ]; then
    echo "[FAILED] Buildozer 没生成 APK（见 mobile/bin/ 与上面的日志）" >&2
    exit 1
fi

cp -f "$APK" "$TARGET"
echo "[done] $TARGET"
