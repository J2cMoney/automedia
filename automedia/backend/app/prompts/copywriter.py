"""文案/脚本 prompt 模板实现。

平台调性表(Spec FLOW-2 MUST):
    小红书:长文(300-800字)+ 多标签(5-8个)+ 情绪化标题 + 表情符号
    抖音:  短平快(50-150字)+ 少标签(2-3个)+ 钩子标题 + 口语化
    快手:  接地气(100-200字)+ 中等标签(3-5个)+ 朴实标题
    视频号:中等(150-300字)+ 中标签(3-5个)+ 信息量标题

输出 JSON 格式约束(让 LLM 返回结构化,后端解析):
    文案:  {"title": "...", "body": "...", "tags": ["..."]}
    脚本:  {"scenes": [{"index": 1, "narration": "...", "visual": "...", "duration": 5}]}
"""
from typing import List

from app.models.account import Platform


# 各平台调性参数(文案生成长度/风格/标签数的硬约束)
PLATFORM_TONE = {
    Platform.XHS: {
        "name": "小红书",
        "body_len": "300-800字",
        "tag_count": "5-8个",
        "style": "情绪化、生活化、多用表情符号(emoji)、标题要有钩子(数字/反差/疑问)",
        "title_hint": "20字以内,带情绪或反差",
    },
    Platform.DOUYIN: {
        "name": "抖音",
        "body_len": "50-150字",
        "tag_count": "2-3个",
        "style": "短平快、口语化、前3秒抓人、直接给结论或冲突",
        "title_hint": "15字以内,钩子型",
    },
    Platform.KUAISHOU: {
        "name": "快手",
        "body_len": "100-200字",
        "tag_count": "3-5个",
        "style": "接地气、朴实、老铁文化、少修饰",
        "title_hint": "15字以内,直白",
    },
    Platform.WECHAT: {
        "name": "视频号",
        "body_len": "150-300字",
        "tag_count": "3-5个",
        "style": "信息量足、偏理性、适合微信生态传播",
        "title_hint": "20字以内,信息型",
    },
}


# ---------- 热点筛选 prompt ----------

def build_hotspot_filter_prompt(
    topic_theme: str,
    hot_titles: List[str],
    top_n: int = 5,
) -> str:
    """热点筛选 prompt:从一批热榜词条里挑与账号主题最相关的 N 条。

    crawler.py 的规则打分是粗筛(关键词命中),这里用 LLM 做精筛(语义相关性),
    减少无关选题进文案环节浪费 token。

    Args:
        topic_theme: 账号主题
        hot_titles: 热榜词条标题列表
        top_n: 要挑几条

    Returns:
        prompt 字符串(LLM 返回 JSON 数组,元素是选中的标题)
    """
    titles_block = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(hot_titles))
    return f"""你是一个内容选题专家。下面是一批平台热榜词条,请挑出与给定主题最相关、最适合做成内容的 {top_n} 条。

【账号主题】{topic_theme}

【热榜词条】
{titles_block}

【要求】
1. 只选与主题强相关的,弱相关或不相关的不选
2. 优先选有热度、有讨论度、能延展出有价值内容的
3. 按相关性从高到低排序

【输出格式】严格的 JSON,不要 markdown 代码块,不要多余解释:
{{"selected": ["选中的标题1原文", "标题2原文"]}}

如果没有任何相关词条,返回 {{"selected": []}}"""


# ---------- 文案生成 prompt ----------

def build_copywriter_prompt(
    topic_title: str,
    topic_theme: str,
    platform: Platform,
) -> str:
    """文案生成 prompt:按选题+主题+平台调性产出标题/正文/标签。

    Args:
        topic_title: 选题标题(热榜词条)
        topic_theme: 账号主题(约束内容方向)
        platform: 目标平台(决定调性)

    Returns:
        prompt 字符串
    """
    tone = PLATFORM_TONE[platform]
    return f"""你是一个资深 {tone["name"]} 内容创作者。请根据选题和账号主题,写一篇符合 {tone["name"]} 调性的文案。

【选题】{topic_title}
【账号主题】{topic_theme}
【平台】{tone["name"]}

【调性要求】
- 正文长度:{tone["body_len"]}
- 标签数量:{tone["tag_count"]}
- 风格:{tone["style"]}
- 标题:{tone["title_hint"]}

【内容要求】
1. 紧扣选题,延展出对读者有价值的内容,不要泛泛而谈
2. 符合账号主题方向,不要跑题
3. 原创表达,不要照搬选题原文

【标签规则(重要)】
- 标签必须放在 JSON 的 tags 字段(数组),每个标签用 # 开头,如 ["#AI编程", "#程序员"]
- 正文(body)里不要包含标签,标签只放 tags 字段
- 标签数符合调性要求({tone["tag_count"]})

【输出格式】严格的 JSON,不要 markdown 代码块,不要多余解释:
{{"title": "标题", "body": "正文(不含标签)", "tags": ["#标签1", "#标签2", "#标签3"]}}"""


# ---------- 视频脚本 prompt ----------

def build_video_script_prompt(
    topic_title: str,
    topic_theme: str,
    copy_body: str,
    scene_count: int = 6,
) -> str:
    """视频脚本 prompt:基于文案产出分镜列表(口播+画面描述)。

    Phase 3 产出脚本结构,实际渲染在 Phase 4(场景 B 从零生成)。
    每镜含:index / narration(口播文案) / visual(画面描述) / duration(秒)。

    Args:
        topic_title: 选题
        topic_theme: 主题
        copy_body: 已生成的文案正文(脚本基于文案延展)
        scene_count: 分镜数量(默认 6,30-60s 短视频)

    Returns:
        prompt 字符串
    """
    return f"""你是一个短视频编导。请基于选题和文案,产出一条 {scene_count} 个分镜的竖屏短视频脚本。

【选题】{topic_title}
【账号主题】{topic_theme}
【文案正文(参考)】{copy_body}

【分镜要求】
1. 共 {scene_count} 个分镜,总时长 30-60 秒
2. 每镜 narration(口播)是这一镜的配音文案,口语化、能念出来
3. 每镜 visual(画面)描述这一镜的画面内容(便于后续找素材或拍摄)
4. 第 1 镜必须是钩子(3 秒内抓住注意力)
5. 口播连贯起来就是完整的内容,不要和文案正文完全重复,要适配口播节奏

【输出格式】严格的 JSON,不要 markdown 代码块,不要多余解释:
{{"scenes": [
  {{"index": 1, "narration": "口播文案", "visual": "画面描述", "duration": 3}},
  {{"index": 2, "narration": "...", "visual": "...", "duration": 5}}
]}}"""
