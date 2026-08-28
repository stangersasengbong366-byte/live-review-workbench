#!/usr/bin/env python3
"""Build the static GitHub Pages payload without local ASR assets."""

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "github-pages"
OUTPUT.mkdir(exist_ok=True)
(OUTPUT / ".github" / "workflows").mkdir(parents=True, exist_ok=True)

shutil.copyfile(ROOT / "index.html", OUTPUT / "index.html")
shutil.copytree(ROOT / "assets", OUTPUT / "assets", dirs_exist_ok=True)
(OUTPUT / ".nojekyll").write_text("", encoding="utf-8")
(OUTPUT / "README.md").write_text(
    """# 伪直播审核工作台

公开演示版支持视频预览、时间区间定位、风险词库和审核单导出。

> GitHub Pages 不运行本机语音识别服务。示例链接可直接查看逐字稿；识别新的长视频链接需使用本机版审核台。
""",
    encoding="utf-8",
)
(OUTPUT / ".gitignore").write_text(
    ".DS_Store\nserver.log\n",
    encoding="utf-8",
)
(OUTPUT / ".github" / "workflows" / "pages.yml").write_text(
    """name: Deploy GitHub Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: .
      - name: Deploy
        id: deployment
        uses: actions/deploy-pages@v4
""",
    encoding="utf-8",
)

print(f"Built public payload at {OUTPUT}")
