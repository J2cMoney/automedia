"""FFmpeg 抽帧工具 - Phase 4 场景 A 核心。

职责:
    1. 长视频抽帧:按指定帧率(默认 1 帧/秒)抽成 PNG,供 GLM 视觉看图找高光
    2. 取视频时长:用 ffprobe(实际用 ffmpeg 解析,避免额外依赖)
    3. 帧缩放:GLM 看 base64 图,大图撑爆请求体,抽帧后按需缩放

设计:
    - 用 imageio-ffmpeg 提供的 ffmpeg 二进制(跨平台自带,POC 已验证)
    - subprocess 调 ffmpeg,纯函数,可单测(mock subprocess)
    - 抽帧目录约定 automedia/frames/{task_id}/frame_{06d}.png(已在 .gitignore)
    - 帧文件名带时间戳索引,方便 GLM 反推切点时间

Spec FLOW-3 已知约束:GLM 看抽帧是"近似看视频",适合口播/讲解/图文类,
不适合舞蹈/体育等强节奏。抽帧 fps 越高越接近连续理解,但成本越高。
"""
import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Union

import imageio_ffmpeg
from PIL import Image

from app.config import BASE_DIR

logger = logging.getLogger(__name__)

# 抽帧默认输出根目录(automedia/frames/,已 gitignore)
FRAMES_ROOT = BASE_DIR / "frames"


def get_ffmpeg_binary() -> str:
    """返回 imageio-ffmpeg 提供的 ffmpeg 可执行文件绝对路径。"""
    return imageio_ffmpeg.get_ffmpeg_exe()


def get_video_duration(video_path: Union[str, Path]) -> float:
    """获取视频时长(秒)。

    用 ffmpeg 解析(不依赖 ffprobe 单独二进制),从 stderr 的 Duration 行提取。
    ffmpeg 处理任何输入都会先打印文件信息,这里用 -i 拿信息就退出。

    Args:
        video_path: 视频文件路径

    Returns:
        时长秒数(浮点)

    Raises:
        RuntimeError: ffmpeg 无法读取视频或解析不到时长
    """
    exe = get_ffmpeg_binary()
    # -i 只读信息,不输出文件;让 ffmpeg 自然报错退出(code 1),信息在 stderr
    proc = subprocess.run(
        [exe, "-i", str(video_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # ffmpeg 没有输出文件会返回非 0,但 stderr 里有 Duration 信息,这是正常的
    stderr = proc.stderr or ""
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not match:
        raise RuntimeError(
            f"无法解析视频时长: {video_path}; ffmpeg stderr: {stderr[:200]}"
        )
    h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return h * 3600 + m * 60 + s


def extract_frames(
    video_path: Union[str, Path],
    output_dir: Union[str, Path],
    *,
    fps: float = 1.0,
    max_frames: int = 0,
    start_prefix: str = "frame",
) -> List[Path]:
    """按帧率从视频抽帧,输出 PNG 到指定目录。

    帧文件命名:{prefix}_{index:06d}.png,index 从 0 开始,按时间顺序。
    GLM 看图时通过 index 反推时间:index / fps = 该帧在视频中的秒数。

    Args:
        video_path: 源视频路径
        output_dir: 输出目录(不存在自动建)
        fps: 抽帧帧率(默认 1.0,即每秒 1 帧)。POC 验证 1fps 对口播够用
        max_frames: 最多抽多少帧,0 表示不限制(防止超长视频抽太多撑爆 LLM)
        start_prefix: 帧文件名前缀

    Returns:
        抽出的帧文件路径列表(按 index 升序)

    Raises:
        RuntimeError: ffmpeg 抽帧失败
        FileNotFoundError: 视频不存在
    """
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(f"视频文件不存在: {src}")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    exe = get_ffmpeg_binary()
    # -vf fps=N 按帧率抽帧;-y 覆盖已存在文件
    # 输出 pattern:frame_%06d.png(ffmpeg 自动按序号填充)
    pattern = str(out_dir / f"{start_prefix}_%06d.png")

    cmd = [
        exe, "-y",
        "-i", str(src),
        "-vf", f"fps={fps}",
    ]
    if max_frames > 0:
        # -vframes N 限制输出帧数(旧名 -vframes,等同 -frames:v)
        cmd.extend(["-frames:v", str(max_frames)])
    cmd.append(pattern)

    logger.info("抽帧: %s @ %sfps -> %s", src.name, fps, out_dir)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 抽帧失败 code={proc.returncode}: {proc.stderr[:300]}"
        )

    frames = sorted(out_dir.glob(f"{start_prefix}_*.png"))
    logger.info("抽帧完成: %d 帧", len(frames))
    return frames


def scale_frame(
    frame_path: Union[str, Path],
    max_size: int = 1024,
    *,
    output_path: Optional[Union[str, Path]] = None,
    jpeg_quality: int = 75,
    force_jpeg: bool = True,
) -> Path:
    """缩放 + 压缩帧图片,控制单图体积。

    GLM 视觉接口要 base64 编码,且智谱对单请求总大小有限制(实测 5 张高信息 PNG
    共 7MB+ 会报 1210)。PNG 高信息帧难压缩(1450KB),转 JPEG + 降质量可压到 <200KB。

    Args:
        frame_path: 源图片路径
        max_size: 长边像素上限(默认 1024,GLM 接受范围)
        output_path: 输出路径,None 则覆盖到 jpeg 同名文件
        jpeg_quality: JPEG 质量(默认 75,体积/质量平衡)
        force_jpeg: 强制转 JPEG(默认 True,PNG 高信息帧压缩必要)

    Returns:
        输出文件路径(转 JPEG 后扩展名变 .jpg)
    """
    src = Path(frame_path)
    img = Image.open(src).convert("RGB")
    w, h = img.size

    # 缩放
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        new_size = (int(w * ratio), int(h * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    # 决定输出路径
    if force_jpeg:
        # 转 JPEG:输出路径 .jpg 后缀
        if output_path is not None:
            dest = Path(output_path)
            if dest.suffix.lower() not in (".jpg", ".jpeg"):
                dest = dest.with_suffix(".jpg")
        else:
            dest = src.with_suffix(".jpg")
        img.save(dest, "JPEG", quality=jpeg_quality, optimize=True)
    else:
        dest = Path(output_path) if output_path is not None else src
        img.save(dest)
    return dest


def frames_to_timestamp(frame_index: int, fps: float = 1.0) -> float:
    """帧 index 反推时间戳(秒)。frame_%06d.png 的 index 从 1 开始。

    GLM 输出切点时可能用 index,这里统一转成秒供 ffmpeg 剪切。
    """
    # 文件名 frame_000001.png 对应 ffmpeg 抽帧的 index 1,时间约 0s
    return (frame_index - 1) / fps


def timestamp_to_frame_index(ts: float, fps: float = 1.0) -> int:
    """时间戳(秒)转帧 index。"""
    return int(ts * fps) + 1


__all__ = [
    "FRAMES_ROOT",
    "get_ffmpeg_binary",
    "get_video_duration",
    "extract_frames",
    "scale_frame",
    "frames_to_timestamp",
    "timestamp_to_frame_index",
]
