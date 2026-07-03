"""Pexels 无版权素材获取 - Phase 4 场景 B。

职责:
    1. search_videos:调 Pexels API 搜索竖屏(portrait)视频素材
    2. download_video:下载视频直链到本地(无防盗链,直接 HTTP GET)
    3. find_or_fallback:混合素材模式 —— Pexels 找为主,找不到返回兜底标记
       (Spec FLOW-3 MUST:Pexels 找为主,手动上传兜底)

2026-07 联网确认(写入注释,不靠记忆):
    - 端点:GET https://api.pexels.com/v1/videos/search(旧 /videos/ 将弃用)
    - 鉴权:Authorization 头直接传 API key(不是 Bearer)
    - 配额:200 次/小时、2 万次/月,成功响应带 X-Ratelimit-Remaining 头
    - 直链:video_files[].link 是无防盗链 mp4,可直接下载/热链
    - License:Pexels License,可商用、可修改、免署名(建议署名)
    - 无官方 Python SDK,直接 requests

Spec FLOW-3 混合素材模式:
    Pexels 找到 -> 下载本地路径
    Pexels 找不到 / key 未配 -> 返回 None(上层 generator.py 标记该镜需手动上传)
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

import requests

from app.config import settings

logger = logging.getLogger(__name__)

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/v1/videos/search"
# 下载超时(秒):Pexels 直链走 Vimeo CDN,大文件可能慢
DOWNLOAD_TIMEOUT = 60


class AssetsError(Exception):
    """素材获取异常。"""


@dataclass
class PexelsVideo:
    """Pexels 视频素材(只保留渲染需要的字段)。"""
    id: int
    duration: int
    width: int
    height: int
    # 各清晰度直链列表:[{"quality":"hd","width":1080,"link":"...","file_type":"video/mp4"}]
    video_files: List[dict]
    preview_image: str = ""

    @property
    def best_mp4_link(self) -> Optional[str]:
        """挑最高清晰度的 mp4 直链(优先 hd,降级 sd)。

        Pexels 返回的 video_files 含多版本(hd/sd/hls),hls 是 m3u8 不能直接下。
        这里只取 video/mp4 类型,按 width 降序选最高清。
        """
        mp4_files = [
            f for f in self.video_files
            if isinstance(f, dict)
            and f.get("file_type") == "video/mp4"
            and f.get("link")
        ]
        if not mp4_files:
            return None
        # 按 width 降序(最高清优先)
        mp4_files.sort(key=lambda f: f.get("width", 0), reverse=True)
        return mp4_files[0]["link"]


def search_videos(
    query: str,
    *,
    orientation: str = "portrait",
    size: str = "medium",
    per_page: int = 5,
    locale: str = "en-US",
    timeout: int = 30,
) -> List[PexelsVideo]:
    """调 Pexels API 搜索视频素材。

    Args:
        query: 搜索词(英文效果好,agent.plan_scenes_from_script 已提炼英文 keyword)
        orientation: 方向 landscape/portrait/square,默认 portrait(竖屏 9:16)
        size: 最小尺寸 large(4K)/medium(1080p)/small(720p),默认 medium
        per_page: 每页数量(默认 5,够选了),最大 80
        locale: 语言区域(英文搜索用 en-US)
        timeout: 请求超时秒

    Returns:
        PexelsVideo 列表(按 Pexels 默认相关性排序)

    Raises:
        AssetsError: API key 未配 / 请求失败 / 解析失败
    """
    api_key = settings.PEXELS_API_KEY.strip()
    if not api_key:
        raise AssetsError("PEXELS_API_KEY 未配置,无法搜索素材(请在 .env 填 key)")

    if not query.strip():
        raise AssetsError("搜索词为空")

    try:
        resp = requests.get(
            PEXELS_VIDEO_SEARCH_URL,
            headers={"Authorization": api_key},
            params={
                "query": query,
                "orientation": orientation,
                "size": size,
                "locale": locale,
                "per_page": per_page,
                "page": 1,
            },
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise AssetsError(f"Pexels 请求失败: {type(e).__name__}: {e}") from e

    if resp.status_code == 429:
        raise AssetsError("Pexels 配额超限(200次/小时),请稍后再试")
    if resp.status_code != 200:
        raise AssetsError(
            f"Pexels 搜索失败 HTTP {resp.status_code}: {resp.text[:200]}"
        )

    # 记录剩余配额(便于监控,不阻塞)
    remaining = resp.headers.get("X-Ratelimit-Remaining")
    if remaining is not None:
        logger.debug("Pexels 剩余配额: %s", remaining)

    try:
        data = resp.json()
    except ValueError as e:
        raise AssetsError(f"Pexels 响应解析失败: {e}") from e

    videos_raw = data.get("videos") or []
    videos: List[PexelsVideo] = []
    for v in videos_raw:
        if not isinstance(v, dict):
            continue
        try:
            videos.append(PexelsVideo(
                id=int(v.get("id", 0)),
                duration=int(v.get("duration", 0)),
                width=int(v.get("width", 0)),
                height=int(v.get("height", 0)),
                video_files=v.get("video_files") or [],
                preview_image=str(v.get("image", "")),
            ))
        except (TypeError, ValueError):
            continue

    logger.info("Pexels 搜索 '%s' 命中 %d 条", query, len(videos))
    return videos


def download_video(
    video: PexelsVideo,
    dest_dir: Union[str, Path],
    *,
    filename: Optional[str] = None,
    timeout: int = DOWNLOAD_TIMEOUT,
) -> Path:
    """下载 Pexels 视频到本地。

    选最高清 mp4 直链下载。无防盗链,直接 HTTP GET 流式写文件。

    Args:
        video: PexelsVideo 对象
        dest_dir: 下载目标目录
        filename: 文件名,None 则用 {id}.mp4
        timeout: 下载超时秒

    Returns:
        下载后的本地文件路径

    Raises:
        AssetsError: 无可用 mp4 直链 / 下载失败
    """
    link = video.best_mp4_link
    if not link:
        raise AssetsError(f"Pexels 视频 {video.id} 无可用 mp4 直链")

    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = filename or f"pexels_{video.id}.mp4"
    out_path = out_dir / fname

    try:
        with requests.get(link, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
    except requests.RequestException as e:
        raise AssetsError(f"下载 Pexels 视频 {video.id} 失败: {e}") from e

    logger.info("下载 Pexels 视频 %s -> %s (%.1fKB)",
                video.id, out_path, out_path.stat().st_size / 1024)
    return out_path


def find_or_fallback(
    keyword: str,
    dest_dir: Union[str, Path],
    *,
    filename: Optional[str] = None,
) -> Optional[Path]:
    """混合素材模式:Pexels 找为主,找不到返回 None(上层标记需手动上传)。

    Spec FLOW-3 MUST:Pexels 找为主,手动上传兜底。
    本函数只负责"找 Pexels",找不到(无结果/key 未配/下载失败)统一返回 None,
    generator.py 拿到 None 后标记该镜需手动上传,前端弹上传接口。

    Args:
        keyword: Pexels 搜索词(英文,来自 agent.ScenePlan.asset_keyword)
        dest_dir: 下载目录
        filename: 下载文件名

    Returns:
        下载后的本地路径,或 None(需兜底)
    """
    if not keyword.strip():
        logger.info("素材关键词为空,需手动上传兜底")
        return None

    try:
        videos = search_videos(keyword, per_page=3)
    except AssetsError as e:
        logger.warning("Pexels 搜索 '%s' 失败,需手动上传兜底: %s", keyword, e)
        return None

    if not videos:
        logger.info("Pexels 搜索 '%s' 无结果,需手动上传兜底", keyword)
        return None

    # 取第一个(相关性最高)下载
    try:
        return download_video(videos[0], dest_dir, filename=filename)
    except AssetsError as e:
        logger.warning("Pexels 下载失败 '%s',需手动上传兜底: %s", keyword, e)
        return None


__all__ = [
    "PEXELS_VIDEO_SEARCH_URL",
    "PexelsVideo",
    "AssetsError",
    "search_videos",
    "download_video",
    "find_or_fallback",
]
