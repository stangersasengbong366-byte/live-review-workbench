---
title: 伪直播审核工作台
emoji: 🎬
colorFrom: blue
colorTo: indigo
sdk: static
app_file: index.html
---

# 伪直播审核工作台

选择本地音视频文件，或粘贴允许跨域读取的公开视频链接后，网页会在访问者自己的浏览器中读取真实音轨并生成逐字稿。页面支持：

- 视频与逐字稿时间轴同步
- 中文课堂、English 英语课堂和中英双语课堂识别
- 自动整理为每段约两句话的阅读模式
- 自定义风险词和相似话术标红
- 在线修订与审核单导出

## 免费公开版

- 不需要服务器或 API Key，音视频和逐字稿不上传服务器
- 首次识别会下载约 80–200 MB 的浏览器模型，之后由浏览器缓存
- 优先使用 WebGPU，无法使用时自动切换 WebAssembly 兼容模式
- 单个文件最大 500 MB、最长 60 分钟
- 远程链接必须允许浏览器跨域读取；否则请先下载，再选择本地文件
- 识别速度和准确率取决于访问者的电脑性能，低于本机 `faster-whisper small` 版本

## 可选本机版

仓库仍保留 `server.py`、`Dockerfile` 和 `requirements.txt`，可在本机使用
`faster-whisper` 服务端识别。公开 Pages 默认使用免费的浏览器端识别。
