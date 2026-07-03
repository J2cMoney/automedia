"""视频号半自动打包 - Phase 5 FLOW-4 Spec A-5。

视频号(微信)无开放自动发布 API,且全自动登录/发布风控极严,
按 Spec A-5 走半自动档:不自动发,纯数据组装,产出 WxPackage 给前端/用户
手动粘贴到视频号网页发布。

不继承 BasePublisher(不发),不开浏览器,纯数据组装。
"""
from dataclasses import dataclass
from typing import List, Optional

from app.models.content import Content

# 视频号创作者工具网址(用户手动打开粘贴)
CHANNELS_URL = "https://channels.weixin.qq.com"


@dataclass
class WxPackage:
    """视频号待发布内容包(半自动档产物)。

    不含自动发布逻辑,只组装数据 + 预拼 copy_text 供一键复制粘贴。
    """
    title: str
    body: str
    tags: List[str]
    video_path: str
    cover_path: Optional[str]
    # 预拼好的"标题+正文+标签"一键复制文本
    copy_text: str
    channels_url: str = CHANNELS_URL


def package_wx_content(content: Content) -> WxPackage:
    """打包视频号待发布内容(Spec FLOW-4 半自动档)。

    不开浏览器,纯数据组装。把 Content 字段拼成 WxPackage,
    copy_text 预拼好供用户一键复制粘贴到视频号网页。

    Args:
        content: 待发布的 Content(含 title/body/tags/video_path)

    Returns:
        WxPackage: 组装好的待发布包
    """
    title = content.title or ""
    body = content.body or ""
    tags = content.tags or []
    # copy_text 格式:标题 + 空行 + 正文 + 空行 + 标签(空行分隔,粘贴友好)
    tag_line = " ".join(tags) if tags else ""
    parts = [title]
    if body:
        parts.append(body)
    if tag_line:
        parts.append(tag_line)
    copy_text = "\n\n".join(parts)
    return WxPackage(
        title=title,
        body=body,
        tags=tags,
        video_path=content.video_path or "",
        cover_path=None,  # Phase 5 暂不生成封面,留空由用户手动选
        copy_text=copy_text,
    )


__all__ = ["WxPackage", "package_wx_content", "CHANNELS_URL"]
