"""Remotion 渲染封装 - Phase 4 场景 B。

职责:
    1. render_video:编排 scene plan + 配音 + 字幕 -> 调 Remotion 子进程渲染成 MP4
    2. ensure_browser:首次调用下载 Chrome Headless Shell
    3. 串行队列:模块级锁,同时只跑 1 条渲染(Spec FLOW-8 CPU 密集约束)

2026-07 联网确认(写入注释,不靠记忆):
    - Remotion 4.0.484(2026-06-26,活跃),纯 Node.js 项目,无 Python SDK
    - Python 调用方式:子进程 npx remotion render
    - Windows 必须 npx.cmd(不是 npx),否则 subprocess 找不到
    - 首次渲染需 Chrome Headless Shell(remotion browser ensure 下载,~300MB)
    - FFmpeg 自动下载(v3.3 起),通常已在 remotion 项目内
    - License:个人/≤3人营利免费,>3人商用需购 License(用户自用 OK)

设计:
    - 渲染是 CPU 密集 + 长任务(几十秒到几分钟),放 Dramatiq worker 进程跑
    - 串行锁保证同时只跑 1 条(单机 CPU 资源有限,Spec FLOW-8)
    - 失败抛 RenderError,上层 actor 标记任务失败可重试
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional, Union

from app.config import BASE_DIR, settings
from app.services.video.agent import ScenePlan
from app.services.video.tts import SubtitleCue

logger = logging.getLogger(__name__)

# Remotion 项目目录(默认 automedia/remotion_project/)
DEFAULT_REMOTION_DIR = BASE_DIR / "remotion_project"
# Composition entry 文件(相对 remotion 项目根)
ENTRY_FILE = "src/entry.ts"
# Composition id(对应 index.tsx 里注册的 id)
COMPOSITION_ID = "Main"
# 渲染超时(秒):长视频可能跑几分钟
RENDER_TIMEOUT = 600


class RenderError(Exception):
    """渲染异常。"""


# 串行锁:同时只跑 1 条渲染(Spec FLOW-8 CPU 密集约束)
_render_lock = threading.Lock()
# Chrome 是否已 ensure(进程内缓存,避免重复检查)
_browser_ensured = False


def _get_remotion_dir() -> Path:
    """获取 Remotion 项目目录(配置优先,否则默认)。"""
    custom = settings.REMOTION_PROJECT_DIR.strip()
    if custom:
        p = Path(custom)
        if not p.is_absolute():
            p = BASE_DIR / custom
        return p
    return DEFAULT_REMOTION_DIR


def _npx_cmd() -> str:
    """Windows 用 npx.cmd,其他平台用 npx(subprocess 找不到 npx)。"""
    return "npx.cmd" if sys.platform == "win32" else "npx"


def ensure_browser() -> None:
    """首次调用时下载 Chrome Headless Shell(幂等)。

    Remotion 渲染需要 Chrome Headless,首次自动下载到 node_modules。
    进程内缓存标记,不重复检查(下载一次即可)。
    """
    global _browser_ensured
    if _browser_ensured:
        return

    remotion_dir = _get_remotion_dir()
    if not remotion_dir.exists():
        raise RenderError(f"Remotion 项目目录不存在: {remotion_dir}")

    logger.info("Remotion: 检查/下载 Chrome Headless(首次较慢)")
    cmd = [_npx_cmd(), "remotion", "browser", "ensure"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(remotion_dir),
            capture_output=True, text=True, timeout=300,
            shell=False,
        )
        if proc.returncode != 0:
            logger.warning("remotion browser ensure 返回非 0(可能已存在): %s", proc.stderr[:200])
    except subprocess.TimeoutExpired:
        raise RenderError("Chrome Headless 下载超时(网络慢,可手动重试)")
    except FileNotFoundError as e:
        raise RenderError(f"找不到 npx(Node.js 未安装或不在 PATH): {e}") from e

    _browser_ensured = True
    logger.info("Remotion: Chrome Headless 就绪")


def _build_props(
    scenes: List[ScenePlan],
    audio_rel: str,
    cues: List[SubtitleCue],
) -> dict:
    """构造传给 Remotion 的 props(JSON)。

    Python 算好每镜的全片时间区间(start/end),Remotion 不用再算。
    字幕 cue 直接透传。

    注意:asset_path / audioPath 必须是相对 remotion_project/public/ 的文件名,
    Remotion 用 staticFile() 加载(不支持绝对路径)。
    """
    scene_list = []
    current_start = 0.0
    for s in scenes:
        scene_list.append({
            "index": s.index,
            "narration": s.narration,
            "visual": s.visual,
            "asset_path": s.asset_path,  # 已转成 public/ 相对文件名(None 或 "scene_001.mp4")
            "duration": s.duration,
            "start": current_start,
            "end": current_start + s.duration,
        })
        current_start += s.duration

    return {
        "scenes": scene_list,
        "audioPath": audio_rel,  # public/ 相对文件名
        "cues": [
            {"start": c.start, "end": c.end, "text": c.text}
            for c in cues
        ],
    }


def _stage_to_public(
    scenes: List[ScenePlan],
    audio_path: Path,
    remotion_dir: Path,
) -> tuple:
    """把音频和各镜素材复制到 remotion_project/public/,返回相对文件名。

    Remotion 的 <Audio>/<Video> 只支持 staticFile()(public/ 下文件)或 HTTP URL,
    不支持绝对本地路径(见 https://www.remotion.dev/docs/miscellaneous/absolute-paths)。
    这里在渲染前把素材和音频复制到 public/ 下,渲染后由调用方清理。

    Args:
        scenes: 分镜(asset_path 是绝对路径或 None)
        audio_path: 配音音频绝对路径
        remotion_dir: Remotion 项目根目录

    Returns:
        (audio_rel, public_files):audio_rel 是 audio 相对 public 的文件名;
        public_files 是本次复制进去的所有文件列表(渲染后清理用)
    """
    public_dir = remotion_dir / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    public_files: List[Path] = []

    # 音频
    audio_rel = "audio.mp3"
    audio_dest = public_dir / audio_rel
    shutil.copy2(audio_path, audio_dest)
    public_files.append(audio_dest)

    # 各镜素材(改名 scene_{index}.mp4,避免冲突)
    for s in scenes:
        if s.asset_path and Path(s.asset_path).exists():
            asset_rel = f"scene_{s.index:03d}.mp4"
            asset_dest = public_dir / asset_rel
            shutil.copy2(s.asset_path, asset_dest)
            public_files.append(asset_dest)
            s.asset_path = asset_rel  # 改成 public/ 相对文件名
        else:
            s.asset_path = None  # None/不存在统一置 None(走兜底渲染)

    return audio_rel, public_files


def render_video(
    scenes: List[ScenePlan],
    audio_path: Union[str, Path],
    output_path: Union[str, Path],
    cues: Optional[List[SubtitleCue]] = None,
    *,
    ensure_browser_first: bool = True,
) -> Path:
    """调 Remotion 渲染视频(场景 B 成片)。

    串行锁保护:同时只跑 1 条渲染(Spec FLOW-8)。

    Args:
        scenes: 分镜计划(每镜含 asset_path,已由 assets.py 回填或 None)
        audio_path: 配音音频路径(mp3)
        output_path: 输出 MP4 路径
        cues: 字幕时间轴(可选,无则无字幕)
        ensure_browser_first: 是否先 ensure Chrome Headless(首次必 True)

    Returns:
        渲染后的 MP4 路径

    Raises:
        RenderError: 渲染失败(Chrome 缺失/Remotion 报错/超时)
    """
    if not scenes:
        raise RenderError("无分镜可渲染")
    audio = Path(audio_path)
    if not audio.exists():
        raise RenderError(f"配音音频不存在: {audio}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    remotion_dir = _get_remotion_dir()
    if not remotion_dir.exists():
        raise RenderError(f"Remotion 项目目录不存在: {remotion_dir}")

    if ensure_browser_first:
        ensure_browser()

    # 把音频和素材复制到 remotion_project/public/(Remotion 只支持 staticFile,不支持绝对路径)
    audio_rel, public_files = _stage_to_public(scenes, audio, remotion_dir)
    props = _build_props(scenes, audio_rel, cues or [])

    # 串行锁:同时只跑 1 条渲染(CPU 密集)
    logger.info("Remotion 渲染开始(串行锁,%d 镜)-> %s", len(scenes), output.name)
    try:
        with _render_lock:
            # props 通过 --props 文件传(避免命令行转义地狱)
            cmd = [
                _npx_cmd(), "remotion", "render",
                ENTRY_FILE,
                COMPOSITION_ID,
                str(output),
                "--codec=h264",
            ]
            props_file = output.parent / "render_props.json"
            props_file.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
            cmd.append(f"--props={props_file}")

            logger.info("Remotion 命令: %s", " ".join(cmd[:5]) + " ...")
            try:
                proc = subprocess.run(
                    cmd, cwd=str(remotion_dir),
                    capture_output=True, text=True, timeout=RENDER_TIMEOUT,
                    shell=False,
                )
            except subprocess.TimeoutExpired:
                raise RenderError(f"Remotion 渲染超时(>{RENDER_TIMEOUT}s)")
            except FileNotFoundError as e:
                raise RenderError(f"找不到 npx: {e}") from e

            if proc.returncode != 0 or not output.exists():
                raise RenderError(
                    f"Remotion 渲染失败 code={proc.returncode}: {proc.stderr[:500]}"
                )
    finally:
        # 清理 public/ 下本次复制的临时文件(避免堆积 + 下次冲突)
        for f in public_files:
            try:
                f.unlink()
            except OSError:
                pass

    logger.info("Remotion 渲染完成: %s (%.1fMB)",
                output.name, output.stat().st_size / (1024 * 1024))
    return output


def reset_render_state():
    """重置渲染状态(测试用)。"""
    global _browser_ensured
    _browser_ensured = False


__all__ = [
    "DEFAULT_REMOTION_DIR",
    "RenderError",
    "ensure_browser",
    "render_video",
    "reset_render_state",
]
