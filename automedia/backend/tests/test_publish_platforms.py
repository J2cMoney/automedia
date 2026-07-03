"""三平台 Publisher + 视频号打包单测 - Phase 5 FLOW-4。

照 test_publish_base.py 的 mock 范式:用 MagicMock 造 page 对象,
设好 query_selector/fill/click/url 的返回值,验证 _do_publish 流程编排。

覆盖重点:
    - 各平台 _do_publish:命中正确 selector 序列 + 返回 URL
    - selector 全 miss 抛 PublishError 带明确消息
    - 发布后 URL 不符抛 PublishError
    - 视频号 package_wx_content:copy_text 拼接正确

真实 Playwright 发布不在单测范围(需真账号 cookie + 平台风控 + DOM 易变),
真实发布靠 Phase 验收手动跑(照 test_crawler / test_publish_base 注释范式)。
"""
from unittest.mock import MagicMock

import pytest

from app.models.account import Account, AccountStatus, AuthState, Platform
from app.models.content import Content, ContentStatus
from app.services.publish.base import PublishError
from app.services.publish.xhs import XhsPublisher
from app.services.publish.dy import DyPublisher
from app.services.publish.ks import KsPublisher
from app.services.publish.wx import package_wx_content, WxPackage, CHANNELS_URL


# ---------- 测试工具 ----------

def _make_content(
    *, title="测试标题", body="测试正文", tags=None, video_path="/tmp/v.mp4"
) -> Content:
    """造测试 Content(tags 默认两个标签)。"""
    return Content(
        id=1, account_id=1, title=title, body=body,
        tags=tags if tags is not None else ["#A", "#B"],
        video_path=video_path, status=ContentStatus.APPROVED,
    )


def _make_page(found_selectors, *, final_url="https://example.com/explore/123"):
    """造 mock page:query_selector 对 found_selectors 中元素返回元素 mock,其余返回 None。

    found_selectors: 命中的 selector 集合(其他 query 返回 None)。
    final_url: 发布后 page.url 的值。
    """
    page = MagicMock()
    page.url = final_url

    def _query(sel):
        if sel in found_selectors:
            el = MagicMock()
            el.is_visible.return_value = True
            return el
        return None

    page.query_selector.side_effect = _query
    # goto/fill/click/click/keyboard 都用默认 MagicMock(无副作用)
    page.goto = MagicMock()
    page.fill = MagicMock()
    page.click = MagicMock()
    page.wait_for_selector = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.keyboard = MagicMock()
    return page


# 让 _wait_and_upload / _wait_gone 不真等文件/真查进度条:
# patch 掉文件存在检查 + upload,使 _do_publish 不阻塞
@pytest.fixture(autouse=True)
def _stub_upload(monkeypatch):
    """类级默认:_wait_and_upload 跳过真实文件检查与上传,避免测试依赖真视频文件。"""
    def _noop_wait_and_upload(self, page, selector, video_path, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.publish.base.BasePublisher._wait_and_upload",
        _noop_wait_and_upload,
    )
    # _wait_gone 也 noop,避免循环查询 mock 元素
    monkeypatch.setattr(
        "app.services.publish.base.BasePublisher._wait_gone",
        lambda self, page, selectors, **kwargs: None,
    )


# ---------- 小红书 XhsPublisher ----------

class TestXhsPublisher:
    def test_successful_publish_returns_post_url(self):
        """正常发布:命中 selector 序列,返回含 /explore/ 的帖子 URL。"""
        page = _make_page({
            'input[type="file"][accept*="video"]',
            "[class*='title'] input",
            "[class*='desc'] [contenteditable]",
            "button:has-text('发布')",
        }, final_url="https://www.xiaohongshu.com/explore/abc123")
        pub = XhsPublisher()
        url = pub._do_publish(page, _make_content())
        assert "explore/abc123" in url
        # 验证调用了上传入口
        assert page.goto.call_args.args[0] == XhsPublisher.publish_url
        page.click.assert_called()  # 点了发布按钮

    def test_missing_upload_input_raises(self):
        """找不到上传入口,抛 PublishError 带明确消息。"""
        page = _make_page(set())  # 全 miss
        pub = XhsPublisher()
        with pytest.raises(PublishError, match="视频上传入口"):
            pub._do_publish(page, _make_content())

    def test_missing_title_raises(self):
        """有上传入口但无标题框,抛 PublishError。"""
        page = _make_page({'input[type="file"]'})
        pub = XhsPublisher()
        with pytest.raises(PublishError, match="标题输入框"):
            pub._do_publish(page, _make_content())

    def test_missing_body_raises(self):
        """有上传+标题但无正文区,抛 PublishError。"""
        page = _make_page({
            'input[type="file"]',
            "[class*='title'] input",
        })
        pub = XhsPublisher()
        with pytest.raises(PublishError, match="正文输入区"):
            pub._do_publish(page, _make_content())

    def test_missing_publish_btn_raises(self):
        """有上传+标题+正文但无发布按钮,抛 PublishError。"""
        page = _make_page({
            'input[type="file"]',
            "[class*='title'] input",
            "[class*='desc'] [contenteditable]",
        })
        pub = XhsPublisher()
        with pytest.raises(PublishError, match="发布按钮"):
            pub._do_publish(page, _make_content())

    def test_url_not_explore_raises(self):
        """发布后 URL 不含 /explore/,判定发布失败抛 PublishError。"""
        page = _make_page({
            'input[type="file"]',
            "[class*='title'] input",
            "[class*='desc'] [contenteditable]",
            "button:has-text('发布')",
        }, final_url="https://creator.xiaohongshu.com/publish/publish")  # 没跳转
        pub = XhsPublisher()
        with pytest.raises(PublishError, match="未跳转到帖子页"):
            pub._do_publish(page, _make_content())

    def test_body_includes_tags(self):
        """正文区填入的内容应含 body + 标签。"""
        page = _make_page({
            'input[type="file"]',
            "[class*='title'] input",
            "[class*='desc'] [contenteditable]",
            "button:has-text('发布')",
        })
        pub = XhsPublisher()
        pub._do_publish(page, _make_content(body="正", tags=["#X", "#Y"]))
        # keyboard.type 被调用,参数含正文+标签
        typed = page.keyboard.type.call_args.args[0]
        assert "正" in typed
        assert "#X" in typed and "#Y" in typed

    def test_discovery_item_url_also_success(self):
        """URL 含 /discovery/item/ 也算发布成功(备选帖子页路径)。"""
        page = _make_page({
            'input[type="file"]',
            "[class*='title'] input",
            "[class*='desc'] [contenteditable]",
            "button:has-text('发布')",
        }, final_url="https://www.xiaohongshu.com/discovery/item/zzz")
        pub = XhsPublisher()
        url = pub._do_publish(page, _make_content())
        assert "discovery/item/zzz" in url


# ---------- 抖音 DyPublisher ----------

class TestDyPublisher:
    def test_successful_publish_returns_url(self):
        """正常发布:命中 selector 序列,返回跳转后的 URL。"""
        page = _make_page({
            'input[type="file"]',
            "input[placeholder*='标题']",
            "[class*='ql-editor']",
            "button:has-text('发布')",
        }, final_url="https://creator.douyin.com/creator-micro/content/manage")
        pub = DyPublisher()
        url = pub._do_publish(page, _make_content())
        assert "content" in url
        assert page.goto.call_args.args[0] == DyPublisher.publish_url

    def test_missing_upload_raises(self):
        """找不到上传入口,抛 PublishError。"""
        page = _make_page(set())
        pub = DyPublisher()
        with pytest.raises(PublishError, match="视频上传入口"):
            pub._do_publish(page, _make_content())

    def test_missing_title_raises(self):
        """无标题框,抛 PublishError。"""
        page = _make_page({'input[type="file"]'})
        pub = DyPublisher()
        with pytest.raises(PublishError, match="标题输入框"):
            pub._do_publish(page, _make_content())

    def test_missing_editor_raises(self):
        """无富文本编辑器,抛 PublishError。"""
        page = _make_page({
            'input[type="file"]',
            "input[placeholder*='标题']",
        })
        pub = DyPublisher()
        with pytest.raises(PublishError, match="正文编辑器"):
            pub._do_publish(page, _make_content())

    def test_missing_publish_btn_raises(self):
        """无发布按钮,抛 PublishError。"""
        page = _make_page({
            'input[type="file"]',
            "input[placeholder*='标题']",
            "[class*='ql-editor']",
        })
        pub = DyPublisher()
        with pytest.raises(PublishError, match="发布按钮"):
            pub._do_publish(page, _make_content())

    def test_url_no_content_raises(self):
        """发布后 URL 不含 content,判定失败抛 PublishError。"""
        page = _make_page({
            'input[type="file"]',
            "input[placeholder*='标题']",
            "[class*='ql-editor']",
            "button:has-text('发布')",
        }, final_url="https://creator.douyin.com/some/other/page")
        pub = DyPublisher()
        with pytest.raises(PublishError, match="未跳转到内容管理页"):
            pub._do_publish(page, _make_content())


# ---------- 快手 KsPublisher ----------

class TestKsPublisher:
    def test_successful_publish_with_separate_title(self):
        """有独立标题框:标题填标题框,正文区填正文+标签。"""
        page = _make_page({
            'input[type="file"]',
            "input[placeholder*='标题']",
            "[contenteditable='true']",
            "button:has-text('发布')",
        }, final_url="https://cp.kuaishou.com/article/manage")
        pub = KsPublisher()
        url = pub._do_publish(page, _make_content(title="T", body="B", tags=["#t1"]))
        assert "manage" in url
        # 标题框被 fill
        assert page.fill.call_count >= 1

    def test_successful_publish_merged_mode(self):
        """无独立标题框:标题+正文+标签合并填入正文区(快手常见合并模式)。"""
        page = _make_page({
            'input[type="file"]',
            "[contenteditable='true']",  # 只有正文区,无标题框
            "button:has-text('发布')",
        }, final_url="https://cp.kuaishou.com/article/publish/ok")
        pub = KsPublisher()
        url = pub._do_publish(page, _make_content(title="T", body="B", tags=["#t1"]))
        assert "publish" in url
        # 合并模式:keyboard.type 的内容应含标题
        typed = page.keyboard.type.call_args.args[0]
        assert "T" in typed and "B" in typed and "#t1" in typed

    def test_missing_upload_raises(self):
        """找不到上传入口,抛 PublishError。"""
        page = _make_page(set())
        pub = KsPublisher()
        with pytest.raises(PublishError, match="视频上传入口"):
            pub._do_publish(page, _make_content())

    def test_missing_all_text_input_raises(self):
        """无标题框也无正文区,抛 PublishError。"""
        page = _make_page({'input[type="file"]'})
        pub = KsPublisher()
        with pytest.raises(PublishError, match="文案输入区"):
            pub._do_publish(page, _make_content())

    def test_missing_publish_btn_raises(self):
        """无发布按钮,抛 PublishError。"""
        page = _make_page({
            'input[type="file"]',
            "input[placeholder*='标题']",
            "[contenteditable='true']",
        })
        pub = KsPublisher()
        with pytest.raises(PublishError, match="发布按钮"):
            pub._do_publish(page, _make_content())

    def test_url_no_publish_or_manage_raises(self):
        """发布后 URL 既无 publish 也无 manage,判定失败抛 PublishError。"""
        page = _make_page({
            'input[type="file"]',
            "input[placeholder*='标题']",
            "[contenteditable='true']",
            "button:has-text('发布')",
        }, final_url="https://cp.kuaishou.com/home/index")
        pub = KsPublisher()
        with pytest.raises(PublishError, match="未跳转到内容管理页"):
            pub._do_publish(page, _make_content())


# ---------- 视频号打包 package_wx_content ----------

class TestWxPackage:
    def test_copy_text_full(self):
        """标题+正文+标签都有:copy_text 用空行分隔三段。"""
        content = _make_content(title="标题A", body="正文B", tags=["#x", "#y"])
        pkg = package_wx_content(content)
        assert pkg.copy_text == "标题A\n\n正文B\n\n#x #y"

    def test_copy_text_no_body(self):
        """无正文:copy_text 只含标题+标签。"""
        content = _make_content(title="标题", body="", tags=["#t"])
        pkg = package_wx_content(content)
        assert pkg.copy_text == "标题\n\n#t"

    def test_copy_text_no_tags(self):
        """无标签:copy_text 只含标题+正文。"""
        content = _make_content(title="标题", body="正文", tags=[])
        pkg = package_wx_content(content)
        assert pkg.copy_text == "标题\n\n正文"

    def test_copy_text_title_only(self):
        """只有标题:copy_text 只含标题。"""
        content = _make_content(title="只有标题", body="", tags=[])
        pkg = package_wx_content(content)
        assert pkg.copy_text == "只有标题"

    def test_fields_carried(self):
        """字段正确带入 WxPackage。"""
        content = _make_content(
            title="T", body="B", tags=["#a"], video_path="/v/m.mp4"
        )
        pkg = package_wx_content(content)
        assert pkg.title == "T"
        assert pkg.body == "B"
        assert pkg.tags == ["#a"]
        assert pkg.video_path == "/v/m.mp4"
        assert pkg.cover_path is None  # Phase 5 留空
        assert pkg.channels_url == CHANNELS_URL

    def test_none_fields_defaulted(self):
        """None 字段安全降级为空字符串/空列表。"""
        content = Content(id=1, account_id=1)  # title/body/tags/video_path 全 None/默认
        pkg = package_wx_content(content)
        assert pkg.title == ""
        assert pkg.body == ""
        assert pkg.tags == []
        assert pkg.video_path == ""
        assert pkg.copy_text == ""  # 三段都空,join 出空串

    def test_is_wx_package_instance(self):
        """返回值是 WxPackage 实例(dataclass)。"""
        pkg = package_wx_content(_make_content())
        assert isinstance(pkg, WxPackage)


# ---------- 统一入口 publish_content ----------

class TestPublishContentEntry:
    def test_wechat_platform_returns_semi_auto_hint(self):
        """视频号无 Publisher,返回失败提示走半自动。"""
        from app.services.publish import publish_content, PUBLISHERS
        from app.models.account import Account, AccountStatus, AuthState, Platform
        acc = Account(
            id=1, platform=Platform.WECHAT, nickname="视频号",
            topic_theme="", auth_state="x", auth_status=AuthState.VALID,
            status=AccountStatus.ACTIVE,
        )
        result = publish_content(acc, _make_content())
        assert result.success is False
        assert "半自动" in result.error
        assert result.platform == "wx"

    def test_publishers_registry_has_three_platforms(self):
        """注册表含三平台,不含视频号。"""
        from app.services.publish import PUBLISHERS
        assert Platform.XHS in PUBLISHERS
        assert Platform.DOUYIN in PUBLISHERS
        assert Platform.KUAISHOU in PUBLISHERS
        assert Platform.WECHAT not in PUBLISHERS
