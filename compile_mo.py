#!/usr/bin/env python3
"""将 .po 翻译文件编译为 .mo（仅依赖标准库，替代 GNU msgfmt）。

用法:
    python compile_mo.py [目录]

默认编译 i18n 目录；也可以在打包流程中指定其它目录，例如:
    python compile_mo.py dist/i18n
"""
import sys
import struct
from pathlib import Path

MAGIC = 0x950412de

_ESCAPES = {
    'n': '\n', 't': '\t', 'r': '\r', 'a': '\a', 'b': '\b',
    'f': '\f', 'v': '\v', '\\': '\\', '"': '"', "'": "'",
}


def unescape(s: str) -> str:
    """解码 .po 字符串中的 C 风格转义（如 \\n、\\t、\\"）。"""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                i += 2
                continue
        out.append(c)
        i += 1
    return ''.join(out)


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def parse_po(text: str):
    """极简 .po 解析：按出现顺序返回 [(msgid, msgstr), ...]（均为未转义的字符串）。

    本项目不使用复数（msgid_plural）与上下文（msgctxt），此处不做处理。
    """
    entries = []
    msgid_parts = []
    msgstr_parts = []
    section = None

    def flush():
        if section is not None:
            entries.append((''.join(msgid_parts), ''.join(msgstr_parts)))

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('msgid_plural') or line.startswith('msgctxt'):
            continue
        if line.startswith('msgid'):
            flush()
            msgid_parts.clear()
            msgstr_parts.clear()
            section = 'msgid'
            msgid_parts.append(unescape(_unquote(line[5:])))
        elif line.startswith('msgstr'):
            section = 'msgstr'
            msgstr_parts.append(unescape(_unquote(line[6:])))
        else:
            # 续行：被引号包裹的字符串
            content = _unquote(line)
            if section == 'msgid':
                msgid_parts.append(unescape(content))
            elif section == 'msgstr':
                msgstr_parts.append(unescape(content))
    flush()
    return entries


def make_mo(entries) -> bytes:
    """按 gettext 二进制 .mo 格式生成内容（与 Python gettext 解析器兼容）。"""
    data = [(mid.encode('utf-8'), mstr.encode('utf-8')) for mid, mstr in entries]
    if not data:
        return b''
    n = len(data)
    offsets = []
    ids = b''
    strs = b''
    for mid, mstr in data:
        offsets.append((len(ids), len(mid), len(strs), len(mstr)))
        ids += mid + b'\0'
        strs += mstr + b'\0'
    keystart = 7 * 4 + 16 * n
    valuestart = keystart + len(ids)
    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    all_offsets = koffsets + voffsets
    header = struct.pack('<Iiiiiii', MAGIC, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
    body = struct.pack('<' + 'i' * len(all_offsets), *all_offsets)
    return header + body + ids + strs


def compile_file(po_path: Path) -> None:
    mo_path = po_path.with_suffix('.mo')
    text = po_path.read_text(encoding='utf-8')
    entries = parse_po(text)
    mo_path.write_bytes(make_mo(entries))
    print(f"compiled {po_path} -> {mo_path}")


def compile_dir(base: Path) -> None:
    for po_path in sorted(base.rglob('*.po')):
        compile_file(po_path)


def main() -> int:
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('i18n')
    if not base.exists():
        print(f"directory not found: {base}", file=sys.stderr)
        return 1
    compile_dir(base)
    return 0


if __name__ == '__main__':
    sys.exit(main())
