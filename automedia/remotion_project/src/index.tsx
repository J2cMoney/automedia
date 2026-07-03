/**
 * AutoMedia 场景B 视频合成 - Remotion composition。
 *
 * 由 Python render.py 子进程调用:npx remotion render src/index.ts Main out.mp4 --props='...'
 *
 * Props(JSON,从 Python 传入):
 *   - scenes: 分镜数组,每镜 {index, narration, visual, asset_path, duration, start, end}
 *       start/end 是该镜在整个视频中的时间区间(秒),由 Python 算好
 *   - audioPath: 配音 mp3 本地绝对路径(Remotion 静态引用)
 *   - cues: 字幕数组 {start, end, text}(秒)
 *
 * 渲染规格:9:16 竖屏 1080x1920,30fps,适合抖音/小红书/快手竖屏
 *
 * License 注意:Remotion 自定义商业许可证,个人/≤3人营利免费(见 package.json)
 */
import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  Video,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  staticFile,
  Series,
} from "remotion";

// ---------- 类型定义(与 Python scene plan 对齐) ----------

interface Scene {
  index: number;
  narration: string;
  visual: string;
  asset_path?: string | null;
  duration: number; // 秒
  start: number; // 该镜起始秒(全片时间轴)
  end: number;
}

interface SubtitleCue {
  start: number;
  end: number;
  text: string;
}

interface MainProps {
  scenes: Scene[];
  audioPath: string;
  cues: SubtitleCue[];
}

// ---------- 常量 ----------

const FPS = 30;
const WIDTH = 1080;
const HEIGHT = 1920;

// 把秒转成帧(Remotion 用帧号控制时序)
const secToFrame = (s: number) => Math.round(s * FPS);

// ---------- 单镜渲染 ----------

const SceneClip: React.FC<{ scene: Scene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const durationInFrames = secToFrame(scene.duration);

  // 淡入淡出(每镜前后 5 帧渐变,转场感)
  const opacity = interpolate(
    frame,
    [0, 5, durationInFrames - 5, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "#000", opacity }}>
      {scene.asset_path ? (
        // 有素材:显示视频素材(staticFile 从 public/ 加载,绝对路径不支持)
        <AbsoluteFill>
          <Video
            src={staticFile(scene.asset_path)}
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
            muted
          />
        </AbsoluteFill>
      ) : (
        // 无素材:纯色背景 + 画面描述文字(兜底,等待用户上传)
        <AbsoluteFill
          style={{
            backgroundColor: `hsl(${(scene.index * 47) % 360}, 40%, 25%)`,
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <div style={{ color: "#fff", fontSize: 36, textAlign: "center", padding: 40 }}>
            {scene.visual || "（待补充素材）"}
          </div>
        </AbsoluteFill>
      )}
    </AbsoluteFill>
  );
};

// ---------- 字幕层(叠加在所有镜之上) ----------

const SubtitleOverlay: React.FC<{ cues: SubtitleCue[] }> = ({ cues }) => {
  const frame = useCurrentFrame();
  const currentSec = frame / FPS;

  // 找当前时间点的字幕
  const active = cues.find((c) => currentSec >= c.start && currentSec <= c.end);

  if (!active) return null;

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 180,
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(0, 0, 0, 0.65)",
          color: "#fff",
          fontSize: 52,
          fontWeight: 600,
          padding: "16px 32px",
          borderRadius: 12,
          maxWidth: "85%",
          textAlign: "center",
          fontFamily: "'PingFang SC', 'Microsoft YaHei', sans-serif",
          textShadow: "0 2px 4px rgba(0,0,0,0.5)",
        }}
      >
        {active.text}
      </div>
    </AbsoluteFill>
  );
};

// ---------- 主 Composition ----------

export const Main: React.FC<MainProps> = ({ scenes, audioPath, cues }) => {
  // 总时长 = 所有镜 duration 之和
  const totalDuration = scenes.reduce((sum, s) => sum + s.duration, 0);
  const totalFrames = secToFrame(totalDuration);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {/* 各镜按序播放(Series 自动接帧) */}
      <Series>
        {scenes.map((scene) => (
          <Series.Sequence
            key={scene.index}
            durationInFrames={secToFrame(scene.duration)}
          >
            <SceneClip scene={scene} />
          </Series.Sequence>
        ))}
      </Series>

      {/* 字幕层叠加 */}
      <SubtitleOverlay cues={cues} />

      {/* 配音音频(贯穿全片)。audioPath 是 public/ 相对文件名,用 staticFile 加载。
          为空时不渲染(POC/无音频场景) */}
      {audioPath ? <Audio src={staticFile(audioPath)} /> : null}
    </AbsoluteFill>
  );
};

// ---------- Composition 注册(remotion render 入口) ----------

// 注意:Remotion 4.x 用 registerRoot + Composition 注册
// Python 调用时 composition id = "Main"
import { Composition } from "remotion";

export const RemotionRoot: React.FC = () => {
  // 默认 props(实际渲染时 --props 覆盖)
  const defaultProps: MainProps = {
    scenes: [
      { index: 1, narration: "示例", visual: "示例画面", duration: 3, start: 0, end: 3 },
    ],
    audioPath: "",
    cues: [],
  };

  const totalDuration = defaultProps.scenes.reduce((s, sc) => s + sc.duration, 0);

  return (
    <Composition
      id="Main"
      component={Main}
      durationInFrames={secToFrame(totalDuration)}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      defaultProps={defaultProps}
    />
  );
};
