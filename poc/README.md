# POC Q8:中转 API 多模态视觉能力验证

## 验证什么
FLOW-3(agent 智能剪辑)的核心前提:**中转 API(智谱/通义/豆包)的多模态视觉能力,看抽帧能不能理解视频内容并做剪辑决策。**

通了 → FLOW-3 能跑,产品核心成立。
不通 → 整条 agent 剪辑路线要重评估。

## 怎么跑

### 1. 设 API Key 环境变量(Git Bash)

智谱 GLM-4V:
```bash
export POC_LLM_API_KEY="你的智谱key"
export POC_LLM_BASE_URL="https://open.bigmodel.cn/api/paas/v4"
export POC_LLM_MODEL="glm-4v"
```

通义千问 VL:
```bash
export POC_LLM_API_KEY="你的通义key"
export POC_LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
export POC_LLM_MODEL="qwen-vl-max"
```

### 2. (可选)换成真实视频帧
现在 frames/ 里是我生成的示意图(有字幕的色块)。要测真实场景,用 FFmpeg 从你的视频抽帧:
```bash
# 用 imageio_ffmpeg 自带的 ffmpeg
python -c "from imageio_ffmpeg import get_ffmpeg_exe; print(get_ffmpeg_exe())"
# 拿到路径后抽帧(每 15 秒一帧):
ffmpeg -i 你的视频.mp4 -vf "fps=1/15" frames/real_frame_%03d.png
```

### 3. 跑测试
```bash
cd E:\AIproject\dd-test\poc
python q8_vision_test.py
```

## 三个测试

| 测试 | 验证 | 判定通过 |
|---|---|---|
| 1 看单帧 | 基础视觉:识别画面内容 | 模型说出人物/字幕/视频类型 |
| 2 挑高光 | 剪辑决策:判断哪帧是高光 | 模型挑出的高光帧符合常识 |
| 3 出 JSON | 可执行性:输出结构化方案 | 模型输出可解析的剪辑决策 |

**三个都通过 → FLOW-3 路线可行。任一失败 → 换模型或换方案。**

## 当前局限
- frames/ 现在是示意图(色块+字幕),模型容易看懂,**严格测试建议换真实视频帧**
- API 调用有成本(看图比纯文本贵),但 POC 三个测试预估 < 1 元
