"""抖音发布器 - Phase 5 FLOW-4。

继承 BasePublisher,只实现 _do_publish 填抖音创作者中心 selector 逻辑。
通用流程(健康检查/限速/浏览器/异常兜底)由 base.publish() 模板方法兜底。

抖音是 React 应用,input[type=file] 是最稳定的上传入口(2026 实测)。
创作者中心标题/正文用 Quill 富文本编辑器(ql-editor contenteditable)。
DOM 易变,selector 用多备选,找不到抛 PublishError 带明确消息。
"""
import logging

from app.models.content import Content
from app.services.publish.base import BasePublisher, PublishError

logger = logging.getLogger(__name__)


class DyPublisher(BasePublisher):
    """抖音创作者中心视频发布。

    发布页 https://creator.douyin.com/creator-micro/content/upload,
    React 应用,input[type=file] 上传最稳定,正文用 ql-editor 富文本。
    """

    # 抖音创作者中心视频上传页
    publish_url = "https://creator.douyin.com/creator-micro/content/upload"
    # 平台中文名(日志用)
    platform_name = "抖音"

    def _do_publish(self, page, content: Content) -> str:
        """抖音发布流程:goto → 上传视频 → 等处理 → 填标题 → 填正文+标签 → 点发布 → 取 URL。

        Raises:
            PublishError: 任意一步 selector 找不到或发布失败。
        """
        # 1. 打开上传页,等 DOM 加载完(React 渲染需额外等待)
        page.goto(self.publish_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)  # 抖音 React 渲染较慢,多等

        # 2. 找视频上传入口(抖音 input[type=file] 最稳定,优先)
        file_selector = self._find_first(page, [
            'input[type="file"][accept*="video"]',
            'input[type="file"]',
            "[class*='upload'] input[type='file']",
        ])
        if file_selector is None:
            raise PublishError("抖音:未找到视频上传入口(input[type=file])")
        self._wait_and_upload(page, file_selector, content.video_path)

        # 3. 抖音上传后需等视频处理完成(转码/封面提取),进度条消失后再填文案
        # _wait_and_upload 已等进度条消失,额外等一下确保表单加载
        page.wait_for_timeout(2000)

        # 4. 填标题(抖音创作者中心单一标题输入框)
        title_selector = self._find_first(page, [
            "input[placeholder*='标题']",
            "input[class*='title']",
            "[class*='title'] input",
            ".editor-kit-input",
        ])
        if title_selector is None:
            raise PublishError("抖音:未找到标题输入框")
        self._safe_fill(page, title_selector, content.title or "")

        # 5. 填正文:抖音用 Quill 富文本编辑器(ql-editor contenteditable)
        # 正文 = 标题或 body + 标签(抖音正文支持 # 话题)
        body_selector = self._find_first_page_editor(page)
        if body_selector is None:
            raise PublishError("抖音:未找到正文编辑器(ql-editor)")
        body_text = self._compose_body(content)
        self._fill_editable(page, body_selector, body_text)

        # 6. 点发布按钮(多备选 selector)
        publish_btn = self._find_first(page, [
            "button:has-text('发布')",
            "button:has-text('发布视频')",
            "[class*='publish-btn']",
            ".contentBtn",
        ])
        if publish_btn is None:
            raise PublishError("抖音:未找到发布按钮")
        page.click(publish_btn)

        # 7. 等待发布完成,抖音发布成功后跳转内容管理页
        page.wait_for_timeout(5000)
        post_url = page.url
        # 抖音发布成功通常跳到 content/manage 页,URL 含 content 即视为成功
        if "content" not in post_url and "/publish" not in post_url:
            raise PublishError(
                f"抖音:发布后未跳转到内容管理页,当前 URL={post_url}"
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
    def _find_first_page_editor(page):
        """找抖音富文本编辑器(ql-editor contenteditable 多备选)。"""
        return DyPublisher._find_first(page, [
            "[class*='ql-editor']",
            ".ql-editor[contenteditable='true']",
            "[contenteditable='true']",
            "[class*='editor'] [contenteditable]",
        ])

    @staticmethod
    def _compose_body(content: Content) -> str:
        """拼接正文:body + 标签尾部。抖音正文支持 # 话题,标签用空格分隔。"""
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
        """填充 contenteditable 编辑器:click 聚焦后 keyboard.type 输入。"""
        try:
            page.click(selector)
            page.keyboard.type(text)
        except Exception as e:
            raise PublishError(
                f"抖音:正文填充失败 selector={selector}: {e}"
            ) from e


__all__ = ["DyPublisher"]
