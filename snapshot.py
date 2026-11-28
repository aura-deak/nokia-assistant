"""诺基亚浏览器快照格式生成。

内置自 https://github.com/aura-deak/nokia_read_novel 的格式逆向成果：
快照文件 = 小端序头部 + URL 字节 + 保留字段 + HTML 正文。
Nokia 215 4G 等机型的内置浏览器可直接打开这类文件离线阅读。
"""

import struct


def create_snapshot(html_bytes, url="http://nokia.assistant/novel", mystery_length=14):
    """把 HTML 字节包装成诺基亚浏览器快照文件。"""
    url_bytes = url.encode("utf-8")
    header_len = 24 + len(url_bytes) + mystery_length
    header = struct.pack(
        "<IIIIII",
        header_len,          # [0-3]   头部总长度
        1,                   # [4-7]   版本号
        0,                   # [8-11]  保留
        len(url_bytes),      # [12-15] URL 长度
        mystery_length,      # [16-19] 保留字段长度
        0x68747470,          # [20-23] "http" 标志
    )
    return header + url_bytes + (b"\x00" * mystery_length) + html_bytes
