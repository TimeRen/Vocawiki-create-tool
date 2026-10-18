#!/usr/bin/env bash
# 在 WSL/Linux 中构建 Android APK。Windows 侧由 build.bat apk 调用。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${1:-0.0.0}"
MODE="${2:-debug}"

if ! command -v buildozer >/dev/null 2>&1; then
    cat >&2 <<'EOF'
[FAILED] 没找到 buildozer。
请先在 WSL/Ubuntu 里执行：
  sudo apt update
  sudo apt install -y python3-pip python3-venv openjdk-17-jdk unzip git zip
  python3 -m pip install --user buildozer cython
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
EOF
    exit 1
fi

cd "$ROOT/mobile"

python3 - "$VERSION" <<'PY'
import pathlib
import sys

version = sys.argv[1].strip() or "0.0.0"
template = pathlib.Path("buildozer.spec.in").read_text(encoding="utf-8")
pathlib.Path("buildozer.spec").write_text(
    template.replace("__VERSION__", version), encoding="utf-8")
print(f"[apk] buildozer.spec version = {version}")
PY

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

TARGET="$ROOT/Vocawiki-create-tool (${VERSION}).apk"
cp -f "$APK" "$TARGET"
echo "[done] $TARGET"
