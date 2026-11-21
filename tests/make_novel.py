"""生成测试小说 TXT（含 GBK 编码版本），写到 /tmp。"""

chapters = []
for i in range(1, 4):
    title = "第%d章 测试章节标题" % i
    body = "\n".join("这是第%d章的第%d段内容。功能机浏览器页面要小，小说阅读需要按字符分页，这一段会重复若干次来撑出足够长度。" % (i, j) for j in range(1, 25))
    chapters.append(title + "\n\n" + body + "\n\n")
text = "测试小说\n\n" + "\n\n".join(chapters)
with open("/tmp/test_novel_utf8.txt", "w", encoding="utf-8") as f:
    f.write(text)
with open("/tmp/test_novel_gbk.txt", "w", encoding="gb18030") as f:
    f.write(text)
print("UTF-8 版本字符数:", len(text))
