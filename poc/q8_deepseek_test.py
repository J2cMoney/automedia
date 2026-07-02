"""
POC Q8 - DeepSeek 视觉能力测试
端点:官方 api.deepseek.com,OpenAI 兼容格式

用法(密钥只从环境变量读,不写死):
  export POC_DS_API_KEY="sk-xxxx"          # DeepSeek key
  python q8_deepseek_test.py
"""
import base64, glob, json, os
from openai import OpenAI

_API_KEY = os.environ.get("POC_DS_API_KEY", "")
if not _API_KEY:
    print("❌ 未设置 POC_DS_API_KEY 环境变量")
    print("   先 export POC_DS_API_KEY='你的DeepSeek key'")
    exit(1)

client = OpenAI(
    api_key=_API_KEY,
    base_url=os.environ.get("POC_DS_BASE_URL", "https://api.deepseek.com")
)
MODEL = os.environ.get("POC_DS_MODEL", "deepseek-v4-flash")

def encode_img(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

frames = sorted(glob.glob("frames/*.png"))
print(f"找到 {len(frames)} 帧\n")

# ===== 测试 1:看单帧 =====
print("="*60)
print("测试 1:看单帧,描述内容")
print("="*60)
try:
    b64 = encode_img(frames[1])
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "描述这个视频画面截图的内容:画面有什么、字幕写了什么、什么类型视频。"}
            ]
        }]
    )
    print(f"\n模型回复:\n{resp.choices[0].message.content}")
except Exception as e:
    print(f"\n❌ 调用失败: {type(e).__name__}: {e}")

# ===== 测试 2:看多帧挑高光 =====
print("\n" + "="*60)
print("测试 2:看多帧,挑高光时刻")
print("="*60)
try:
    content = [{"type": "text", "text": "这是口播视频按时间抽出的 6 帧(按顺序)。哪几帧最像'高光时刻'?挑 1-2 帧说明理由。"}]
    for f in frames:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encode_img(f)}"}})
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": content}]
    )
    print(f"\n模型回复:\n{resp.choices[0].message.content}")
except Exception as e:
    print(f"\n❌ 调用失败: {type(e).__name__}: {e}")

# ===== 测试 3:输出剪辑 JSON =====
print("\n" + "="*60)
print("测试 3:输出结构化剪辑决策")
print("="*60)
try:
    content = [{"type": "text", "text": "这是口播视频 6 帧抽帧。剪成 60 秒高光。只输出 JSON:\n{\"highlights\":[{\"start\":\"00:XX\",\"end\":\"00:XX\",\"reason\":\"为什么\"}],\"total\":数字}"}]
    for f in frames:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encode_img(f)}"}})
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": content}]
    )
    result = resp.choices[0].message.content
    print(f"\n模型回复:\n{result}")
    try:
        js = result.strip()
        if "```" in js:
            js = js.split("```")[1]
            if js.startswith("json"): js = js[4:]
        json.loads(js)
        print("\n✅ JSON 解析成功")
    except:
        print("\n⚠️ JSON 解析失败")
except Exception as e:
    print(f"\n❌ 调用失败: {type(e).__name__}: {e}")

print("\n" + "="*60)
print("POC 结束")
