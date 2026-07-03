"""分发服务包 - Phase 5 FLOW-4。

按平台拆分:base(基类)+ xhs/dy/ks(三平台自动)+ wx(视频号半自动)。

对外入口:
    - publish_content(account, content):按账号平台分发到对应 Publisher(全自动档)
    - package_wx_content(content):视频号半自动打包(不开浏览器,纯数据组装)
    - PUBLISHERS:平台 -> Publisher 类映射(注册表)

视频号(WECHAT)无 Publisher(半自动),调用方应走 package_wx_content。
"""
from typing import Dict, Type

from app.models.account import Account, Platform
from app.models.content import Content
from app.services.publish.base import BasePublisher, PublishResult
from app.services.publish.xhs import XhsPublisher
from app.services.publish.dy import DyPublisher
from app.services.publish.ks import KsPublisher
from app.services.publish.wx import package_wx_content, WxPackage  # noqa: F401

# 平台 -> Publisher 类注册表(视频号不在内:走半自动)
PUBLISHERS: Dict[Platform, Type[BasePublisher]] = {
    Platform.XHS: XhsPublisher,
    Platform.DOUYIN: DyPublisher,
    Platform.KUAISHOU: KsPublisher,
}


def publish_content(account: Account, content: Content, *, headless: bool = True) -> PublishResult:
    """按账号平台分发到对应 Publisher。

    三平台(xhs/dy/ks)全自动发布;视频号(WECHAT)无 Publisher,返回失败提示走半自动。

    Args:
        account: 发布账号(决定平台)
        content: 待发布内容(需含 video_path)
        headless: 是否无头模式运行浏览器(调试传 False 看过程)

    Returns:
        PublishResult: 成功带 post_url,失败带 error 原因
    """
    publisher_cls = PUBLISHERS.get(account.platform)
    if publisher_cls is None:
        # 视频号等不支持自动发布的平台:返回明确提示,走 package_wx_content
        return PublishResult(
            success=False,
            error=f"平台 {account.platform.value} 不支持自动发布(视频号走半自动)",
            platform=account.platform.value,
            content_id=content.id,
        )
    return publisher_cls(headless=headless).publish(account, content)


__all__ = [
    "PUBLISHERS",
    "publish_content",
    "package_wx_content",
    "WxPackage",
    "BasePublisher",
    "PublishResult",
    "XhsPublisher",
    "DyPublisher",
    "KsPublisher",
]
