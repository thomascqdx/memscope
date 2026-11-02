"""Unisoc ylog 容器流的顺序解码（本实现独立编写，仅复用格式知识）。

文件结构：
  FileHeader  96B  首 int32 = 0x2E2E（文件头；TAGS 表重置，行缓冲丢弃）
  FileTail   104B  首 int32 = 0xB5B5 / 0xFFFFB5B5
  Block      20B 头 <iIiic3x：magic 0x5A5A、seq、len、z(0=raw,1=zlib)、tag 字节
             + len 字节负载
每个文件首块为元信息，含 `TAGS: <tag>:<name>;…`（key = ':' 前一字节）
和 `VERSION:x.y.z`。只解压 tag 等于 android.log 的块；行跨块重组。
可多个 .ylog 文件首尾相接；fh 只被顺序 read（管道/ZipExtFile 均可）。
产出：android.log 中以 'E' 开头且含任一 tags 的行，去掉 `E<hex>` 前缀
和尾部 \\r，utf-8 errors='replace' 解码后的标准 threadtime 行。
"""
from __future__ import annotations

import re
import struct
import zlib
from typing import Iterator

_BH = struct.Struct('<iIiic3x')
_MAGIC_FILE = 0x2E2E
_MAGIC_TAIL = (0xB5B5, 0xB5B5 - 0x10000)  # 0x0000B5B5 / 0xFFFFB5B5（有符号）
_MAGIC_BLOCK = 0x5A5A
_FH_SIZE = 96
_FT_SIZE = 104
_MAX_BLOCK = 64 << 20
_CHUNK = 4 << 20
_TRIM = 8 << 20
_VERSION = re.compile(rb'VERSION:([\d.]+)')
_EPRE = re.compile(rb'E[0-9A-Fa-f]+\s+')


def iter_event_lines(fh, tags: tuple[bytes, ...],
                     stats: dict | None = None) -> Iterator[str]:
    """顺序扫描 ylog 流，产出过滤后的事件日志行（str）。"""
    buf = b''
    android_tag = None
    eof = False

    def bump(k: str):
        if stats is not None:
            stats[k] = stats.get(k, 0) + 1

    def setver(raw: bytes):
        if stats is not None and 'version' not in stats:
            m = _VERSION.search(raw)
            if m:
                stats['version'] = m.group(1).decode('ascii', 'replace')

    def need(n: int) -> bool:
        nonlocal buf, eof
        while len(buf) < n and not eof:
            c = fh.read(_CHUNK)
            if not c:
                eof = True
                break
            buf += c
        return len(buf) >= n

    pos = 0
    tail = b''  # 跨块未完成的行
    while True:
        if pos > _TRIM:
            buf = buf[pos:]
            pos = 0
        if not need(pos + _BH.size):
            break
        m = struct.unpack_from('<i', buf, pos)[0]
        if m == _MAGIC_FILE:
            setver(buf[pos:pos + _FH_SIZE])
            pos += _FH_SIZE
            android_tag = None
            tail = b''
            bump('files')
            continue
        if m in _MAGIC_TAIL:
            pos += _FT_SIZE
            continue
        if m != _MAGIC_BLOCK:
            pos += 1
            bump('resync')
            continue
        _m, _seq, ln, z, t = _BH.unpack_from(buf, pos)
        if (ln <= 0 or ln > _MAX_BLOCK or z not in (0, 1)
                or not need(pos + _BH.size + ln)):
            pos += 1
            bump('resync')
            continue
        blk = buf[pos + _BH.size: pos + _BH.size + ln]
        pos += _BH.size + ln
        bump('blocks')
        if android_tag is None:
            # 首个块（或 TAGS 找到前的块）视为元信息
            try:
                raw = zlib.decompress(blk) if z == 1 else blk
            except zlib.error:
                continue
            i = raw.find(b'TAGS:')
            if i >= 0:
                for ent in raw[i + 5:i + 300].split(b';'):
                    j = ent.find(b':')
                    if j > 0 and ent[j + 1:] == b'android.log':
                        android_tag = ent[j - 1:j]
                setver(raw)
            continue
        if t != android_tag:
            continue
        bump('android_blocks')
        try:
            raw = zlib.decompress(blk) if z == 1 else blk
        except zlib.error:
            bump('zerr')
            continue
        data = tail + raw
        cut = data.rfind(b'\n')
        if cut < 0:
            tail = data
            continue
        tail = data[cut + 1:]
        for line in data[:cut].split(b'\n'):
            if line[:1] == b'E' and any(x in line for x in tags):
                yield (_EPRE.sub(b'', line, count=1)
                       .rstrip(b'\r').decode('utf-8', 'replace'))
