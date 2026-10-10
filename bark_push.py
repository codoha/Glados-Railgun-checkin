"""Bark 推送:读取 checkin.log 中的签到总结,推送到手机(Bark App)。

依赖 workflow 中签到步骤用 `tee checkin.log` 落盘日志。
Bark URL 格式:https://api.day.app/<key>,通过环境变量 BARK_URL 提供。
"""
import os
import re
import logging
from urllib.parse import quote

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bark_push")


def load_summary(path: str) -> str:
    """提取 '签到总结' 起的整块日志作为推送正文"""
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if "签到总结" in line:
            return "\n".join(lines[i:i + 6]).strip()
    return ""


def main() -> None:
    bark_url = os.environ.get("BARK_URL", "").strip().rstrip("/")
    if not bark_url:
        logger.warning("BARK_URL 未设置, 跳过 Bark 推送")
        return

    summary = load_summary("checkin.log")
    if not summary:
        summary = "未获取到签到总结, 请查看 Actions 日志"
        title = "GLaDOS 签到异常"
    else:
        m = re.search(r"GLaDOS 签到[^\n]*", summary)
        title = m.group(0).strip() if m else "GLaDOS 签到"

    url = f"{bark_url}/{quote(title)}/{quote(summary)}?group=GLaDOS"
    try:
        resp = requests.get(url, timeout=30)
        logger.info(f"Bark 响应: {resp.status_code} {resp.text[:200]}")
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Bark 推送失败: {e}")
        raise


if __name__ == "__main__":
    main()
