# -*- coding: utf-8 -*-
"""DeepSeek API 共享客户端（GeoKB 各摄入管线统一使用）。

Key 来源（知识库入库专用，优先级从高到低）：
    环境变量 GEOKB_DEEPSEEK_API_KEY / GEOKB_DEEPSEEK_KEY
    → D:/ZSK/geokb/.deepseek_key.ingest
    → 旧约定 DEEPSEEK_API_KEY / DEEPSEEK_KEY → D:/PostNews/Translate/.deepseek_key

Tracking：所有入库请求统一带 HTTP-Tracking-Id = GEOKB_DS_TRACKING_ID
（默认 5e62b29d-7074-4062-8485-97599610bf3d），与导读/翻译分账。

纪律：429/5xx/网络错误指数退避重试；infra 故障抛 InfraError 由上层决定
（留待重跑，绝不写垃圾数据）。
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

# ─── usage 旁路采集（2026-10-19）──────────────────────────────
# 历史缺口：ds_chat 第二个返回值是「错误字符串」，而 kp_extract 把它当
# usage dict 取 .get("usage") → 永远取到 0，logs/kp_ingest.jsonl 里
# 1609 条记录 in/out 全为空。没有计量就没法谈省 token，故加线程本地旁路：
#   ds_chat(...)              正常调用，行为完全不变
#   last_usage()              取本线程最近一次调用的 usage（含缓存命中数）
_TL = threading.local()


def last_usage():
    """本线程最近一次 ds_chat（含续写）的累计 usage（无则空 dict）。"""
    return getattr(_TL, "usage", {}) or {}


def _accumulate_usage(u):
    """把一次请求的 usage 累加进线程本地（续写时不会丢掉前一次的账）。"""
    if not isinstance(u, dict) or not u:
        return
    cur = dict(getattr(_TL, "usage", None) or {})
    for k in ("prompt_tokens", "completion_tokens", "total_tokens",
              "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        cur[k] = (cur.get(k) or 0) + (u.get(k) or 0)
    _TL.usage = cur

DS_ENDPOINT = "https://api.deepseek.com/chat/completions"
# 官方当前型号：deepseek-flash = DeepSeek-V4.1-Flash
# 旧名 deepseek-chat / deepseek-v4-flash 已下线，会被重定向并按 Flash 价计费。
# ⚠ 官方规定该模型「思考模式默认打开且 effort=high」——必须在请求中显式传
#   thinking={"type":"disabled"}，否则思维链会吃掉全部 max_tokens 预算，
#   使 content 为空或残缺。历史事故见 logs/truncation_repro.json。
DS_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")  # V4.1-Flash
# 入库专用凭据：与导读 (.deepseek_key.papers)、翻译 (.deepseek_key) 分离计费
DS_KEY_FILE_INGEST = r"D:/ZSK/geokb/.deepseek_key.ingest"
DS_KEY_FILE_LEGACY = r"D:/PostNews/Translate/.deepseek_key"
DS_TRACKING_ID = os.environ.get("GEOKB_DS_TRACKING_ID",
                                "5e62b29d-7074-4062-8485-97599610bf3d")
DS_TIMEOUT = 120
DS_RETRY = 4
# 思考档位。实测（logs/thinking_effort_probe.json，同一任务）：
#   disabled 总输出 3414（思考 0）
#   minimal  总输出 4180（思考 544）  ← 采用
#   low      总输出 4102（思考 474）
#   high     总输出 5906（思考 2071） ← 官方默认，贵 41%
# minimal 保留少量推理以稳定字段质量，成本仅比关闭高 22%，比 high 省 29%。
DS_THINKING = os.environ.get("DEEPSEEK_THINKING", "1") not in ("0", "false", "False")
DS_EFFORT = os.environ.get("DEEPSEEK_EFFORT", "minimal")
# 默认输出上限。思考模式下该额度与思维链共享，故按任务显式指定更稳妥。
DS_MAX_TOKENS_DEFAULT = 4000


class InfraError(Exception):
    """API 侧故障（429/5xx/网络），与输出质量无关，重跑时可恢复。"""


def load_api_key():
    # 1) 入库专用环境变量
    for env in ("GEOKB_DEEPSEEK_API_KEY", "GEOKB_DEEPSEEK_KEY"):
        k = os.environ.get(env, "").strip()
        if k:
            return k
    # 2) 入库专用文件（默认路径）
    if os.path.exists(DS_KEY_FILE_INGEST):
        k = open(DS_KEY_FILE_INGEST, encoding="utf-8").read().strip()
        if k:
            return k
    # 3) 旧约定，兼容历史任务环境
    for env in ("DEEPSEEK_API_KEY", "DEEPSEEK_KEY"):
        k = os.environ.get(env, "").strip()
        if k:
            return k
    if os.path.exists(DS_KEY_FILE_LEGACY):
        k = open(DS_KEY_FILE_LEGACY, encoding="utf-8").read().strip()
        if k:
            return k
    raise RuntimeError("未找到 GeoKB 入库专用 DeepSeek API Key"
                       f"（env GEOKB_DEEPSEEK_API_KEY 或 {DS_KEY_FILE_INGEST}）")


def extract_json(s):
    """从模型输出中找出第一个平衡的 JSON 对象。"""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except Exception:
                    return None
    return None


def ds_chat(messages, api_key, max_tokens=DS_MAX_TOKENS_DEFAULT,
            temperature=0.2, json_mode=True, timeout=DS_TIMEOUT,
            thinking=DS_THINKING, effort=DS_EFFORT, max_continue=3,
            retries=None, _depth=0):
    """调用 DeepSeek chat/completions，返回 (content, error)。

    thinking/effort: 是否思考及思考档位（默认 True + minimal，实测性价比最优）。

    防截断三重机制（历史事故：思维链吃光 max_tokens 导致 content 为空/残缺）：
      ① 显式传 thinking/reasoning_effort，杜绝默认 high 吃掉预算；
      ② finish_reason == 'length' 时**自动续写**（把已得内容拼回去再续最多
         max_continue 次），而不是直接判失败；
      ③ 仍被 length 终止 → 返回 error，绝不让残缺内容被上层当完整结果接受。
    infra 故障（429/5xx/网络）重试 DS_RETRY 次后抛 InfraError。
    retries：覆盖 DS_RETRY。**调用方有自己的外层超时时必须显式给**——
    否则 timeout×DS_RETRY+退避 会轻松超过外层预算，被杀在重试中途，
    结果既没拿到内容、也看不到真实错误（2026-10-29 知识点抽取 420s 被杀即此因）。
    """
    if _depth == 0:
        _TL.usage = {}          # 一次逻辑调用一个账本，续写继续累加
    payload = {
        "model": DS_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if thinking and effort:
        payload["reasoning_effort"] = effort
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if not thinking:
        # 官方：思考模式下 temperature 不生效（不报错但无效）
        payload["temperature"] = temperature

    def _once(msgs, tok):
        p = dict(payload, messages=msgs, max_tokens=tok)
        req = urllib.request.Request(
            DS_ENDPOINT, data=json.dumps(p).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}",
                     "HTTP-Tracking-Id": DS_TRACKING_ID}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        _accumulate_usage(resp.get("usage"))
        return resp

    def _call_with_retry(msgs, tok):
        """网络层重试；耗尽后抛 InfraError，其他异常也归一化为 InfraError。"""
        last_err = ""
        n = max(1, int(DS_RETRY if retries is None else retries))
        for attempt in range(n):
            try:
                return _once(msgs, tok)
            except urllib.error.HTTPError as e:
                try:
                    body = e.read().decode("utf-8", "ignore")[:200]
                except Exception:
                    body = ""
                last_err = f"HTTP {e.code}: {body}"
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(min(2 ** attempt * 5, 60))
                    continue
                raise InfraError(last_err)
            except (urllib.error.URLError, ConnectionResetError,
                    TimeoutError, OSError) as e:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(min(2 ** attempt * 5, 60))
                continue
            except InfraError:
                raise
            except Exception as e:
                raise InfraError(f"{type(e).__name__}: {e}")
        raise InfraError(last_err)

    try:
        resp = _call_with_retry(messages, max_tokens)
        choice = resp["choices"][0]
        content = choice["message"].get("content") or ""
        if choice.get("finish_reason") != "length":
            if not content.strip():
                return content, "EMPTY: 模型未返回内容"
            return content, None
        # ── 被截断：尝试续写 ──
        if _depth >= max_continue:
            return content, f"TRUNCATED: 续写 {max_continue} 次仍未收敛"
        cont_msgs = messages + [
            {"role": "assistant", "content": content},
            {"role": "user",
             "content": "你的上一条回复在输出未完成时被中断了。"
                        "请从被截断的位置紧接着继续输出剩余部分，**不要重复已输出的内容**，"
                        "也不要添加任何说明，直接接着写。"}]
        tail, err = ds_chat(cont_msgs, api_key, max_tokens=max_tokens,
                            temperature=temperature, json_mode=False,
                            timeout=timeout, thinking=thinking, effort=effort,
                            max_continue=max_continue, retries=retries,
                            _depth=_depth + 1)
        return content + tail, err
    except InfraError:
        raise
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
