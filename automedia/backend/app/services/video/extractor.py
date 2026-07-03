"""场景 A:长视频高光提取 - Phase 4 FLOW-3 场景 A。

完整链路(用户上传的长视频 → 60s 高光短片):
    1. FFmpeg 抽帧(frames.py):按 1fps 抽成 PNG,缩放后供 GLM 看
    2. GLM 视觉决策(agent.py decide_highlights):看帧找高光,输出切点 JSON
    3. FFmpeg 剪切拼接:按切点逐段剪切,concat demuxer 拼成 60s 成片
    4. 成片 + 中间产物存 output/{task_id}/

中间产物保留(Spec FLOW-3 MUST:失败可重试):
    - output/{task_id}/frames/         抽帧目录
    - output/{task_id}/clip_decision.json   GLM 决策 JSON
    - output/{task_id}/segments/       剪切出的各高光片段
    - output/{task_id}/highlight.mp4   最终成片

Spec FLOW-3 已知约束:GLM 看抽帧是近似看视频,适合口播/讲解,真实视频中文 OCR
需二次验证(POC 有水分)。这里把决策 JSON 落盘,便于人工复核切点是否合理。
"""
import json
import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Union

import imageio_ffmpeg

from app.config import BASE_DIR
from app.services.video import frames as frames_mod
from app.services.video import agent as agent_mod
from app.services.video.agent import ClipDecision

logger = logging.getLogger(__name__)

OUTPUT_ROOT = BASE_DIR / "output"


class ExtractorError(Exception):
    """高光提取异常。"""


def extract_highlights(
    video_path: Union[str, Path],
    *,
    task_id: int,
    target_duration: int = 60,
    fps: float = 1.0,
    max_frame_size: int = 1024,
    max_frames: int = 60,
    decision: Optional[ClipDecision] = None,
    llm=None,
) -> tuple:
    """场景 A 主入口:长视频 → 高光短片。

    Args:
        video_path: 源长视频路径
        task_id: 任务 id(决定中间产物目录)
        target_duration: 目标成片秒数(默认 60)
        fps: 抽帧帧率(默认 1.0,口播够用)
        max_frame_size: 抽帧缩放长边(默认 1024,压缩 base64 请求体)
        max_frames: 最多抽多少帧(默认 60,防长视频帧爆炸 + GLM 图片上限)。
            glm-4.6v-flash 支持 300 张,这里保守限 60(2分钟1fps=120帧会被均匀采样到60)。
        decision: 可选,外部已算好的剪辑决策(测试/复用);None 则现场调 GLM
        llm: LLM 客户端注入(测试用)

    Returns:
        (output_path, clip_decision):成片路径 + 决策对象

    Raises:
        ExtractorError: 任何环节失败
    """
    src = Path(video_path)
    if not src.exists():
        raise ExtractorError(f"源视频不存在: {src}")

    work_dir = OUTPUT_ROOT / str(task_id)
    frames_dir = work_dir / "frames"
    segments_dir = work_dir / "segments"
    decision_path = work_dir / "clip_decision.json"
    output_path = work_dir / "highlight.mp4"

    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. 抽帧(max_frames 限制防长视频帧爆炸 + GLM 图片上限)
    logger.info("场景A task=%s: 抽帧 %s @ %sfps (max %d 帧)", task_id, src.name, fps, max_frames)
    try:
        raw_frames = frames_mod.extract_frames(src, frames_dir, fps=fps, max_frames=max_frames)
    except Exception as e:
        raise ExtractorError(f"抽帧失败: {e}") from e

    if not raw_frames:
        raise ExtractorError("抽帧结果为空")

    # 缩放(压缩 base64 体积,GLM 看图更快更省)
    scaled_frames: List[Path] = []
    for f in raw_frames:
        scaled = frames_mod.scale_frame(f, max_size=max_frame_size)
        scaled_frames.append(scaled)

    # 2. GLM 决策(若外部未传入)
    if decision is None:
        logger.info("场景A task=%s: GLM 看帧找高光(%d 帧)", task_id, len(scaled_frames))
        try:
            decision = agent_mod.decide_highlights(
                scaled_frames, fps=fps,
                target_duration=target_duration, llm=llm,
            )
        except Exception as e:
            raise ExtractorError(f"GLM 剪辑决策失败: {e}") from e
    else:
        logger.info("场景A task=%s: 使用外部传入的决策(%d 段)", task_id, len(decision.segments))

    # 决策 JSON 落盘(便于人工复核 + 失败重试复用)
    decision_path.write_text(
        json.dumps(decision.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not decision.segments:
        raise ExtractorError("剪辑决策无有效片段")

    # 3. 按切点剪切 + 拼接
    logger.info("场景A task=%s: 剪切 %d 段并拼接", task_id, len(decision.segments))
    segments_dir.mkdir(parents=True, exist_ok=True)
    try:
        segment_files = _cut_segments(src, decision.segments, segments_dir)
        _concat_segments(segment_files, output_path)
    except Exception as e:
        raise ExtractorError(f"剪切拼接失败: {e}") from e

    logger.info("场景A task=%s 完成: %s", task_id, output_path)
    return output_path, decision


def _cut_segments(
    video_path: Path,
    segments: List[agent_mod.HighlightSegment],
    out_dir: Path,
) -> List[Path]:
    """按切点逐段剪切视频(重编码,防花屏)。

    用 ffmpeg -ss(开始)+ -t(时长)+ 重编码(libx264/aac)。
    重编码虽慢(比 -c copy 慢 10 倍),但保证切点精确、无花屏黑帧、
    concat 兼容性稳定。-c copy 在非关键帧处会花屏(口播剪切点几乎不在关键帧)。

    Args:
        video_path: 源视频
        segments: 切点列表
        out_dir: 各段输出目录

    Returns:
        各段文件路径列表(顺序与 segments 一致)
    """
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    out_files: List[Path] = []

    for i, seg in enumerate(segments):
        seg_path = out_dir / f"seg_{i:03d}.mp4"
        duration = max(0.1, seg.end - seg.start)
        # -ss 放 -i 前:fast seek(关键帧粗定位);重编码保证精确切点
        cmd = [
            exe, "-y",
            "-ss", f"{seg.start:.3f}",
            "-i", str(video_path),
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac",
            "-avoid_negative_ts", "make_zero",
            str(seg_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not seg_path.exists():
            raise ExtractorError(
                f"剪切第 {i} 段失败(start={seg.start},end={seg.end}): {proc.stderr[:200]}"
            )
        out_files.append(seg_path)

    return out_files


def _concat_segments(segment_files: List[Path], output_path: Path) -> Path:
    """用 concat demuxer 拼接各段成最终成片。

    生成 file list 文件,调 ffmpeg -f concat。各段编码一致时用 -c copy(快),
    不一致则需重编码。这里假设同源剪切(编码一致),用 copy。

    Args:
        segment_files: 各段路径
        output_path: 输出成片路径

    Returns:
        output_path
    """
    if not segment_files:
        raise ExtractorError("无片段可拼接")

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    work_dir = output_path.parent

    # concat demuxer 需要文件列表(file 'path' 格式)
    list_path = work_dir / "concat_list.txt"
    # 路径用绝对路径并转义单引号(ffmpeg concat 格式要求)
    lines = []
    for f in segment_files:
        abs_path = str(f.resolve()).replace("\\", "/")
        abs_path = abs_path.replace("'", r"'\''")
        lines.append(f"file '{abs_path}'")
    list_path.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        exe, "-y",
        "-f", "concat",
        "-safe", "0",  # 允许绝对路径
        "-i", str(list_path),
        "-c", "copy",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not output_path.exists():
        raise ExtractorError(f"拼接失败: {proc.stderr[:200]}")

    return output_path


__all__ = [
    "OUTPUT_ROOT",
    "ExtractorError",
    "extract_highlights",
]
