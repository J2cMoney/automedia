"""快手发布器 - Phase 5 FLOW-4。

继承 BasePublisher,只实现 _do_publish 填快手创作者服务平台 selector 逻辑。
通用流程(健康检查/限速/浏览器/异常兜底)由 base.publish() 模板方法兜底。

快手创作者平台(cp.kuaishou.com)DOM 文档少,class 名不稳定,
selector 用宽松匹配 + 多备选。标题/正文常合并成一个输入区(contenteditable)。
"""
import logging

from app.models.content import Content
from app.services.publish.base import BasePublisher, PublishError

logger = logging.getLogger(__name__)


class KsPublisher(BasePublisher):
    """快手创作者服务平台视频发布。

    发布页 https://cp.kuaishou.com/article/publish/video,
    DOM 文档少,selector 用宽松匹配 + 多备选。
    """

    # 快手创作者服务平台视频发布页
    publish_url = "https://cp.kuaishou.com/article/publish/video"
    # 平台中文名(日志用)
    platform_name = "快手"

    def _do_publish(self, page, content: Content) -> str:
        """快手发布流程:goto → 上传视频 → 等处理 → 填文案 → 点发布 → 取 URL。

        快手标题正文常合并成一个输入区,文案 = 标题 + 正文 + 标签拼一起填入。

        Raises:
            PublishError: 任意一步 selector 找不到或发布失败。
        """
        # 1. 打开视频发布页,等 DOM 加载完
        page.goto(self.publish_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)  # 等页面渲染

        # 2. 找视频上传入口(多备选 selector)
        file_selector = self._find_first(page, [
            'input[type="file"][accept*="video"]',
            'input[type="file"]',
            "[class*='upload'] input[type='file']",
        ])
        if file_selector is None:
            raise PublishError("快手:未找到视频上传入口(input[type=file])")
        self._wait_and_upload(page, file_selector, content.video_path)

        # 3. 上传后等视频处理(转码),额外等表单加载
        page.wait_for_timeout(2000)

        # 4. 快手标题正文常合并:优先找独立标题框,无则填合并文案到正文区
        title_selector = self._find_first(page, [
            "input[placeholder*='标题']",
            "[class*='title'] input",
            "input[class*='title']",
            "textarea[placeholder*='标题']",
        ])

        if title_selector is not None:
            # 有独立标题框:标题填标题框,正文+标签填正文区
            self._safe_fill(page, title_selector, content.title or "")
            body_selector = self._find_first(page, [
                "[contenteditable='true']",
                "textarea[placeholder*='描述']",
                "[class*='desc'] [contenteditable]",
                "[class*='editor'] [contenteditable]",
            ])
            if body_selector is None:
                raise PublishError("快手:未找到正文输入区")
            self._fill_editable(page, body_selector, self._compose_body_no_title(content))
        else:
            # 无独立标题框:标题+正文+标签合并填入正文区(快手常见合并模式)
            body_selector = self._find_first(page, [
                "[contenteditable='true']",
                "textarea[placeholder*='描述']",
                "[class*='desc'] [contenteditable]",
                "[class*='editor'] [contenteditable]",
            ])
            if body_selector is None:
                raise PublishError("快手:未找到文案输入区(标题/正文合并区)")
            self._fill_editable(page, body_selector, self._compose_body(content))

        # 5. 点发布按钮(多备选 selector)
        publish_btn = self._find_first(page, [
            "button:has-text('发布')",
            "[class*='publish-btn']",
            ".publish",
            "button:has-text('发布视频')",
        ])
        if publish_btn is None:
            raise PublishError("快手:未找到发布按钮")
        page.click(publish_btn)

        # 6. 等待发布完成,校验跳转
        page.wait_for_timeout(4000)
        post_url = page.url
        # 快手发布成功通常跳到内容管理页,URL 含 publish/manage 即视为成功
        if "/publish" not in post_url and "manage" not in post_url:
            raise PublishError(
                f"快手:发布后未跳转到内容管理页,当前 URL={post_url}"
            )
        return post_url

    # ---------- 内部工具 ----------

    @staticmethod
    def _find_first(page, selectors):
        """逐个尝试 selector 列表,返回第一个命中的 selector 字符串,全 miss 返回 None。"""
        for sel in selectors:
            try:
                if page.query_selector(sel) is not None:
                    return sel
            except Exception:
                continue
        return None

    @staticmethod
    def _compose_body(content: Content) -> str:
        """拼接完整文案:标题 + 正文 + 标签(快手合并模式用)。"""
        title = (content.title or "").strip()
        body = (content.body or "").strip()
        tags = content.tags or []
        tag_line = " ".join(t.strip() for t in tags if t and t.strip())
        parts = []
        if title:
            parts.append(title)
        if body:
            parts.append(body)
        if tag_line:
            parts.append(tag_line)
        return "\n".join(parts)

    @staticmethod
    def _compose_body_no_title(content: Content) -> str:
        """拼接正文+标签(有独立标题框时,正文区只填正文+标签)。"""
        body = (content.body or "").strip()
        tags = content.tags or []
        tag_line = " ".join(t.strip() for t in tags if t and t.strip())
        parts = []
        if body:
            parts.append(body)
        if tag_line:
            parts.append(tag_line)
        return "\n".join(parts)

    @staticmethod
    def _fill_editable(page, selector: str, text: str) -> None:
        """填充 contenteditable 区域:click 聚焦后 keyboard.type 输入。"""
        try:
            page.click(selector)
            page.keyboard.type(text)
        except Exception as e:
            raise PublishError(
                f"快手:文案填充失败 selector={selector}: {e}"
            ) from e


__all__ = ["KsPublisher"]
