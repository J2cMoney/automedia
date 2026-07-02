"""
POC Q8: 中转 API 多模态视觉能力验证
验证:LLM 能不能看抽帧理解视频内容,并做剪辑决策

用法:
  先设环境变量(三选一):
    # 智谱 GLM-4V
    export POC_LLM_API_KEY="你的key"
    export POC_LLM_BASE_URL="https://open.bigmodel.cn/api/paas/v4"
    export POC_LLM_MODEL="glm-4v"

    # 通义千问 VL
    export POC_LLM_API_KEY="你的key"
    export POC_LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
    export POC_LLM_MODEL="qwen-vl-max"

  然后把真实视频帧放到 frames/ 目录(或用脚本生成的测试帧)
  最后运行:python q8_vision_test.py
"""

import os
import base64
import json
import glob
from openai import OpenAI

# === 配置(从环境变量读) ===
API_KEY = os.environ.get("POC_LLM_API_KEY", "")
BASE_URL = os.environ.get("POC_LLM_BASE_URL", "")
MODEL = os.environ.get("POC_LLM_MODEL", "")

if not API_KEY:
    print("❌ 未设置 POC_LLM_API_KEY 环境变量")
    print("   先 export POC_LLM_API_KEY='你的key'")
    exit(1)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

FRAMES_DIR = "frames"

def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def get_frames():
    files = sorted(glob.glob(os.path.join(FRAMES_DIR, "*.png")))
    return files

# ========== 测试 1:看单帧,描述内容 ==========
def test1_single_frame(frame_path):
    print("\n" + "="*60)
    print("测试 1:LLM 看单帧,描述内容")
    print("="*60)
    print(f"帧: {frame_path}")

    b64 = encode_image(frame_path)
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": "这是视频的一个画面截图。描述你看到的内容:画面里有什么、字幕写了什么、这大概是什么类型的视频。"}
                ]
            }],
            max_tokens=300,
        )
        result = resp.choices[0].message.content
        print(f"\n模型回复:\n{result}")
        return result
    except Exception as e:
        print(f"\n❌ 调用失败: {e}")
        return None

# ========== 测试 2:看多帧,挑高光 ==========
def test2_pick_highlights(frames):
    print("\n" + "="*60)
    print("测试 2:LLM 看多帧,挑出高光时刻")
    print("="*60)
    print(f"共 {len(frames)} 帧")

    content = [{"type": "text", "text": "这是一个口播视频按时间抽出的 6 帧画面(按顺序对应不同时间点)。哪几帧最像是'高光时刻'(金句、核心观点、最有传播感的画面)?挑出 1-2 帧,说明理由。"}]
    for f in frames:
        b64 = encode_image(f)
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": content}],
            max_tokens=400,
        )
        result = resp.choices[0].message.content
        print(f"\n模型回复:\n{result}")
        return result
    except Exception as e:
        print(f"\n❌ 调用失败: {e}")
        return None

# ========== 测试 3:输出结构化剪辑 JSON ==========
def test3_clip_decision(frames):
    print("\n" + "="*60)
    print("测试 3:LLM 输出结构化剪辑决策(JSON)")
    print("="*60)

    content = [{"type": "text", "text": "这是口播视频的 6 帧抽帧。我要把它剪成 60 秒高光。请输出剪辑决策,严格用这个 JSON 格式(只输出 JSON,不要其他文字):\n{\"highlights\": [{\"start_time\": \"00:XX\", \"end_time\": \"00:XX\", \"reason\": \"为什么选这段\"}], \"total_clips\": 数字}"}]
    for f in frames:
        b64 = encode_image(f)
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": content}],
            max_tokens=400,
        )
        result = resp.choices[0].message.content
        print(f"\n模型回复:\n{result}")
        # 尝试解析 JSON
        try:
            # 提取 JSON 部分
            json_str = result.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]
            parsed = json.loads(json_str)
            print(f"\n✅ JSON 解析成功: {json.dumps(parsed, ensure_ascii=False, indent=2)}")
        except:
            print(f"\n⚠️ JSON 解析失败(模型输出格式不标准,但能看出意图)")
        return result
    except Exception as e:
        print(f"\n❌ 调用失败: {e}")
        return None

# ========== 主流程 ==========
if __name__ == "__main__":
    print(f"模型: {MODEL}")
    print(f"Base URL: {BASE_URL}")

    frames = get_frames()
    if not frames:
        print(f"❌ {FRAMES_DIR}/ 目录没有 PNG 帧")
        exit(1)

    print(f"找到 {len(frames)} 帧测试图片")

    # 跑三个测试
    test1_single_frame(frames[1])  # 用第 2 帧(标了高光的)
    test2_pick_highlights(frames)
    test3_clip_decision(frames)

    print("\n" + "="*60)
    print("POC 完成。判断标准:")
    print("="*60)
    print("测试1 通过 = 模型能识别画面内容(人物/字幕/类型)")
    print("测试2 通过 = 模型能判断哪帧是高光(剪辑决策能力)")
    print("测试3 通过 = 模型能输出可执行的结构化方案(可落地)")
    print("\n三个都通过 = FLOW-3 的 agent 剪辑路线可行")
    print("任一失败 = 需要换模型或换方案")
