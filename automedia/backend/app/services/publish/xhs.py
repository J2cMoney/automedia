"""小红书发布器 - Phase 5 FLOW-4。

继承 BasePublisher,只实现 _do_publish 填小红书创作者中心 selector 逻辑。
通用流程(健康检查/限速/浏览器/异常兜底)由 base.publish() 模板方法兜底。

DOM 易变(2026 实测为准):所有 selector 用多备选,逐个 query_selector 尝试,
找不到抛 PublishError 带明确消息,失败可追溯。
"""
import logging

from app.models.content import Content
from app.services.publish.base import BasePublisher, PublishError

logger = logging.getLogger(__name__)


class XhsPublisher(BasePublisher):
    """小红书创作者中心视频发布。

    发布页 https://creator.xiaohongshu.com/publish/publish,
    React 应用,DOM class 名带 hash 易变,selector 用 [class*=] 模糊匹配 + 多备选。
    """

    # 小红书创作者中心发布页
    publish_url = "https://creator.xiaohongshu.com/publish/publish"
    # 平台中文名(日志用)
    platform_name = "小红书"

    def _do_publish(self, page, content: Content) -> str:
        """小红书发布流程:goto → 上传视频 → 填标题 → 填正文+标签 → 点发布 → 取 URL。

        Raises:
            PublishError: 任意一步 selector 找不到或发布后未跳转帖子页。
        """
        # 1. 打开发布页,等 DOM 加载完(React 渲染需额外等待)
        page.goto(self.publish_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)  # 等 React 渲染上传入口

        # 2. 找视频上传入口(多备选 selector,DOM 易变)
        file_selector = self._find_first(page, [
            'input[type="file"][accept*="video"]',  # 优先:明确视频类型
            'input[type="file"]',                    # 兜底:任意文件输入
        ])
        if file_selector is None:
            raise PublishError("小红书:未找到视频上传入口(input[type=file])")
        self._wait_and_upload(page, file_selector, content.video_path)

        # 3. 填标题(多备选 selector)
        title_selector = self._find_first(page, [
            "[class*='title'] input",
            "input[placeholder*='标题']",
            "#title",
            "input[class*='dew']",
        ])
        if title_selector is None:
            raise PublishError("小红书:未找到标题输入框")
        self._safe_fill(page, title_selector, content.title or "")

        # 4. 填正文:contenteditable 用 click + keyboard.type(fill 对 contenteditable 可能无效)
        # 正文 = body + 标签尾部拼接(#tag #tag 格式)
        body_selector = self._find_first(page, [
            "[class*='desc'] [contenteditable]",
            "textarea[placeholder*='正文']",
            ".input-content",
            "[contenteditable='true']",
        ])
        if body_selector is None:
            raise PublishError("小红书:未找到正文输入区")
        body_text = self._compose_body(content)
        self._fill_editable(page, body_selector, body_text)

        # 5. 点发布按钮(多备选 selector)
        publish_btn = self._find_first(page, [
            "button:has-text('发布')",
            "[class*='publish-btn']",
            ".publish",
            "[class*='publishBtn']",
        ])
        if publish_btn is None:
            raise PublishError("小红书:未找到发布按钮")
        page.click(publish_btn)

        # 6. 等待跳转并校验帖子页 URL
        page.wait_for_timeout(3000)
        post_url = page.url
        if "/explore/" not in post_url and "/discovery/item/" not in post_url:
            # URL 不含帖子页标识,判定发布失败
            raise PublishError(
                f"小红书:发布后未跳转到帖子页,当前 URL={post_url}"
            )
        return post_url

    # ---------- 内部工具 ----------

    @staticmethod
    def _find_first(page, selectors):
        """逐个尝试 selector 列表,返回第一个命中的 selector 字符串,全 miss 返回 None。

        DOM 易变,用 query_selector 探测,命中即用,避免 wait_for_selector 硬等超时。
        """
        for sel in selectors:
            try:
                if page.query_selector(sel) is not None:
                    return sel
            except Exception:
                # 单个 selector 查询异常(语法/无效)跳过试下一个
                continue
        return None

    @staticmethod
    def _compose_body(content: Content) -> str:
        """拼接正文:body + 标签尾部。标签格式 "#tag #tag",用空格分隔。"""
        body = (content.body or "").strip()
        tags = content.tags or []
        # 标签统一成 # 开头(去重空格),尾部接上
        tag_line = " ".join(t.strip() for t in tags if t and t.strip())
        parts = []
        if body:
            parts.append(body)
        if tag_line:
            parts.append(tag_line)
        return "\n".join(parts)

    @staticmethod
    def _fill_editable(page, selector: str, text: str) -> None:
        """填充 contenteditable 正文区:click 聚焦后 keyboard.type 输入。

        contenteditable 元素 page.fill 常无效(非 input/textarea),改用 type 模拟键盘。
        """
        try:
            page.click(selector)
            page.keyboard.type(text)
        except Exception as e:
            raise PublishError(
                f"小红书:正文填充失败 selector={selector}: {e}"
            ) from e


__all__ = ["XhsPublisher"]
