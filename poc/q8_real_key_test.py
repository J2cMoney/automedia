"""
POC Q8 真实测试:用用户提供的火山方舟中转端点(GLM-5.2)测视觉能力
端点是 Anthropic 格式,所以用 anthropic SDK

用法(密钥只从环境变量读,不写死):
  export POC_LLM_API_KEY="ark-xxxx"        # 火山方舟 token
  export POC_LLM_BASE_URL="https://ark.cn-beijing.volces.com/api/coding"
  export POC_LLM_MODEL="glm-5.2[1m]"
  python q8_real_key_test.py
"""
import base64, os, glob, json
from anthropic import Anthropic

BASE_URL = os.environ.get("POC_LLM_BASE_URL", "")
AUTH_TOKEN = os.environ.get("POC_LLM_API_KEY", "")
MODEL = os.environ.get("POC_LLM_MODEL", "")

if not AUTH_TOKEN:
    print("❌ 未设置 POC_LLM_API_KEY 环境变量")
    print("   先 export POC_LLM_API_KEY='你的火山方舟token'")
    print("   并设 POC_LLM_BASE_URL / POC_LLM_MODEL")
    exit(1)

client = Anthropic(base_url=BASE_URL, api_key=AUTH_TOKEN)

def encode_img(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

frames = sorted(glob.glob("frames/*.png"))
print(f"找到 {len(frames)} 帧\n")

# ===== 测试 1:看单帧,描述内容 =====
print("="*60)
print("测试 1:看单帧,描述内容")
print("="*60)
try:
    b64 = encode_img(frames[1])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": "这是视频的一个画面截图。描述你看到的内容:画面里有什么、字幕写了什么、这是什么类型的视频。"}
            ]
        }]
    )
    print(f"\n模型回复:\n{resp.content[0].text}")
except Exception as e:
    print(f"\n❌ 调用失败: {type(e).__name__}: {e}")

# ===== 测试 2:看多帧,挑高光 =====
print("\n" + "="*60)
print("测试 2:看多帧,挑高光时刻")
print("="*60)
try:
    content = [{"type": "text", "text": "这是口播视频按时间抽出的 6 帧(按顺序)。哪几帧最像'高光时刻'(金句/核心观点/最有传播感)?挑 1-2 帧并说明理由。"}]
    for f in frames:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encode_img(f)}})

    resp = client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": content}]
    )
    print(f"\n模型回复:\n{resp.content[0].text}")
except Exception as e:
    print(f"\n❌ 调用失败: {type(e).__name__}: {e}")

# ===== 测试 3:输出结构化剪辑 JSON =====
print("\n" + "="*60)
print("测试 3:输出结构化剪辑决策")
print("="*60)
try:
    content = [{"type": "text", "text": "这是口播视频 6 帧抽帧。剪成 60 秒高光。输出 JSON(只输出 JSON):\n{\"highlights\":[{\"start\":\"00:XX\",\"end\":\"00:XX\",\"reason\":\"为什么\"}],\"total\":数字}"}]
    for f in frames:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encode_img(f)}})

    resp = client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": content}]
    )
    result = resp.content[0].text
    print(f"\n模型回复:\n{result}")
    try:
        js = result.strip()
        if "```" in js:
            js = js.split("```")[1]
            if js.startswith("json"): js = js[4:]
        json.loads(js)
        print("\n✅ JSON 解析成功")
    except:
        print("\n⚠️ JSON 解析失败(格式不标准,但看意图)")
except Exception as e:
    print(f"\n❌ 调用失败: {type(e).__name__}: {e}")

print("\n" + "="*60)
print("POC 结束")
print("="*60)
