# -*- coding: utf-8 -*-
"""
本地小模型（llama.cpp + Qwen 0.8B）从标题/摘要提取 GeoKB 元数据。
只负责：摘要、标签、实体、模块建议；不判断是否相关（由 keyword_filter 负责）。
"""
import json
import os
import re
import subprocess
import tempfile
import hashlib
from pathlib import Path

_NO_WIN = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW：子孙全继承隐藏控制台
BASE = Path(__file__).resolve().parent.parent
PROFILE = json.load(open(BASE / "config" / "team_profile.json", encoding="utf-8"))
LLAMA_CLI = PROFILE.get("llama_cli", r"D:/PostNews/Translate/llama-cpp/llama-cli.exe")
MODEL_PATH = PROFILE.get("model_path", r"D:/PostNews/Translate/models-gguf/Qwen3.5-0.8B-Q4_K_M.gguf")
CACHE = BASE / "index" / "llm_extract_cache.json"


def _cache_key(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def load_cache():
    if CACHE.exists():
        try:
            return json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(cache):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def build_prompt(title, summary="", source=""):
    modules = [
        "economic-geology", "mineralogy", "petrology", "geochemistry",
        "geochronology", "structure", "tectonics", "field-methods",
        "data-sheets", "glossary"
    ]
    text = f"标题：{title}\n"
    if summary:
        text += f"摘要：{summary}\n"
    if source:
        text += f"来源：{source}\n"
    user = f"""从下面条目中提取结构化信息。\n\n{text}\n\n请只输出一个 JSON 对象，不要任何解释。module 只能从以下选项选最贴切的一个：{', '.join(modules)}。\nJSON 格式：\n{{\n  "module": "economic-geology",\n  "tags": ["标签1", "标签2"],\n  "summary_cn": "50字以内中文摘要",\n  "entities": ["实体1", "实体2"],\n  "key_method": "核心方法或技术（如有）",\n  "study_area": "研究区域（如有）",\n  "application": "应用场景或找矿意义（如有）"\n}}\nJSON："""
    sys_prompt = "你是一名地质学文献元数据提取助手，只输出 JSON，不解释。"
    # Qwen3.5 chat template
    prompt = f"<|im_start|>system\n{sys_prompt}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n"
    return prompt


def extract(title, summary="", source="", n_predict=320, threads=4, timeout=180):
    cache = load_cache()
    key = _cache_key(title + "|" + summary + "|" + source)
    if key in cache:
        return cache[key]

    prompt = build_prompt(title, summary, source)
    if not os.path.exists(LLAMA_CLI) or not os.path.exists(MODEL_PATH):
        return {"error": "llama-cli or model not found"}

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        cmd = [
            LLAMA_CLI,
            "-m", MODEL_PATH,
            "-f", prompt_file,
            "-n", str(n_predict),
            "--threads", str(threads),
            "--temp", "0.1",
            "--no-display-prompt",
            "-ngl", "0",
            "-st",
            "--simple-io"
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="ignore", timeout=timeout,
                             creationflags=_NO_WIN).stdout
    except subprocess.TimeoutExpired:
        return {"error": "llama timeout"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        try:
            os.unlink(prompt_file)
        except Exception:
            pass

    result = _parse_json_from_text(out)
    result["_raw"] = out[-2000:]
    cache[key] = result
    save_cache(cache)
    return result


def _parse_json_from_text(text):
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"(\{.*\})", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return {"module": "glossary", "tags": [], "summary_cn": "", "entities": [],
            "key_method": "", "study_area": "", "application": "", "_parse_error": True}


def normalize_result(result):
    if not isinstance(result, dict):
        result = {}
    return {
        "module": re.sub(r"[^a-z0-9_-]", "", str(result.get("module", "glossary"))) or "glossary",
        "tags": [str(t) for t in result.get("tags", []) if t][:10],
        "summary_cn": str(result.get("summary_cn", "") or result.get("summary", ""))[:120],
        "entities": [str(e) for e in result.get("entities", []) if e][:10],
        "key_method": str(result.get("key_method", ""))[:100],
        "study_area": str(result.get("study_area", ""))[:100],
        "application": str(result.get("application", ""))[:200],
    }


if __name__ == "__main__":
    demo = extract(
        title="Porphyry copper mineralization associated with adakitic rocks in the Jinhe belt, SW China",
        source="Economic Geology"
    )
    print(json.dumps(demo, ensure_ascii=False, indent=2))
