#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_undefined.py —— 零构建 ESM 的「未定义标识符」静态守卫。

为什么需要它：本项目刻意不做打包，也就没有构建器替我们做作用域检查。
漏一个 import，只有执行到那一行才会炸 —— `rotateSizes` 就是这么漏的
（viewport.js 用了它但没 import，页面一打开就 ReferenceError）。

算法：把每个文件里「能产生绑定的语法位置」全部收集成一个名字集合，
再找出所有 `name(` 形式的调用点，若该名字不在集合里，就报缺失。
收集的位置包括：import（具名/默认/命名空间）、function/class 声明、
const/let/var、解构、对象字面量方法简写、键值形式的函数、赋值形式的函数、
getter/setter、以及所有圆括号内的形参。

参数里的嵌套括号（默认值 `f(x = obj.bar('a'))`）靠手写的平衡扫描处理，
避开正则嵌套量词的灾难性回溯。注释与字符串字面量先全部替换成空格，
这样就不会把 CSS `rgb(...)`、JSDoc 注释里的方法签名误报成函数调用。

已知局限：不做真正的词法作用域分析，因此极端情况（同名全局被遮蔽、
with、eval 生成的名字）不在覆盖范围内 —— 对本项目的代码风格足够了。

用法：python modules/reader/tests/scan_undefined.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'assets'
DIRS = [ROOT / 'kpdf', ROOT / 'ui', ROOT]

# 语言关键字 / 宿主全局：不该报缺失
BUILTINS = {
    'if', 'for', 'while', 'switch', 'catch', 'return', 'typeof', 'function', 'await',
    'new', 'class', 'super', 'this', 'in', 'of', 'do', 'else', 'delete', 'void', 'yield',
    'Number', 'String', 'Boolean', 'Array', 'Object', 'Math', 'JSON', 'Date', 'RegExp',
    'Map', 'Set', 'WeakMap', 'WeakSet', 'Promise', 'Symbol', 'Error', 'TypeError',
    'RangeError', 'SyntaxError', 'Proxy', 'Reflect', 'BigInt', 'Intl', 'URL',
    'URLSearchParams', 'FormData', 'Headers', 'Request', 'Response', 'AbortSignal',
    'parseInt', 'parseFloat', 'isNaN', 'isFinite', 'encodeURIComponent', 'decodeURIComponent',
    'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame',
    'cancelAnimationFrame', 'queueMicrotask', 'structuredClone', 'fetch', 'console',
    'document', 'window', 'globalThis', 'self', 'performance', 'navigator', 'location',
    'history', 'IntersectionObserver', 'ResizeObserver', 'MutationObserver',
    'AbortController', 'Image', 'Blob', 'File', 'FileReader', 'TextDecoder', 'TextEncoder',
    'DOMParser', 'XMLSerializer', 'getComputedStyle', 'matchMedia', 'CustomEvent',
    'Event', 'HTMLElement', 'HTMLCanvasElement', 'Node', 'Element', 'Uint8Array',
    'Uint16Array', 'Uint32Array', 'Uint8ClampedArray', 'Int8Array', 'Float32Array',
    'Float64Array', 'ArrayBuffer', 'DataView', 'OffscreenCanvas', 'createImageBitmap',
    'Path2D', 'SVGElement', 'devicePixelRatio', 'atob', 'btoa', 'crypto', 'Text',
    'WeakRef', 'FinalizationRegistry', 'AggregateError', 'process', 'require',
}

# `async(`、`get(`、`set(` 是修饰符后紧跟参数列表，不是真正的函数调用
_KEYWORD_CALLERS = {'async', 'await', 'get', 'set', 'import'}

# import 与 顶层声明仍走正则（线性扫描，无嵌套量词）。
# 方法 / 函数的定义与参数名提取，全部交给下方手写的平衡括号扫描。
_IMPORT = re.compile(r'import\s+([\s\S]*?)\s+from\s*[\'"][^\'"]+[\'"]|import\s*[\'"][^\'"]+[\'"]')
_FUNCTION_OR_CLASS = re.compile(r'\b(?:function|class)\s*\*?\s*([A-Za-z_$][\w$]*)')
_DECL = re.compile(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)')
_DECL_OBJECT = re.compile(r'\b(?:const|let|var)\s*\{([^}]*)\}')
_DECL_ARRAY = re.compile(r'\b(?:const|let|var)\s*\[([^\]]*)\]')

_CALL = re.compile(r'(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(')
_IDENT = re.compile(r'[A-Za-z_$][\w$]*')
_MODIFIERS = frozenset({'static', 'async', 'get', 'set'})

_BIND_PATTERNS = [
    (_FUNCTION_OR_CLASS, 'name'),
    (_DECL, 'name'),
    (_DECL_OBJECT, 'destruct'),
    (_DECL_ARRAY, 'destruct'),
]


def _clean(name: str):
    """把 `{ a: b }` / `b }` / `...rest` 这类片段归一成一个标识符。"""
    name = name.strip().lstrip('.')
    name = name.strip('{}[]')
    name = name.strip()
    name = name.split(' as ')[-1].strip()
    return name if _IDENT.fullmatch(name) else None


def _collect_imports(raw: str) -> set:
    names = set()
    for m in _IMPORT.finditer(raw):
        clause = m.group(1)
        if not clause:
            continue
        for part in clause.split(','):
            part = part.strip()
            if not part:
                continue
            if part.startswith('{'):
                for piece in part.strip('{}').split(','):
                    nm = _clean(piece)
                    if nm:
                        names.add(nm)
            else:
                nm = _clean(part)
                if nm:
                    names.add(nm)
    return names


def _iter_balanced(src: str):
    """栈式平衡括号扫描：yield (open_idx, close_idx, inner_text)。

    每个有匹配 `)` 的 `(` 都会独立产生一次（内层先出栈），所以 `(()=>r())`
    这种嵌套也能把里外两对都收齐 —— 否则像 `Promise((r) => ...)` 的 `r`
    永远不会被认作形参。O(N) 单遍。
    """
    stack = []
    for i, ch in enumerate(src):
        if ch == '(':
            stack.append(i)
        elif ch == ')':
            if stack:
                open_idx = stack.pop()
                yield open_idx, i, src[open_idx + 1:i]


def _is_definition(src: str, close_idx: int) -> bool:
    """`)` 之后跟 `{` / `,`（方法简写链）/ `=>` 才算定义；纯调用不算。"""
    n = len(src)
    j = close_idx + 1
    while j < n and src[j] in ' \t':
        j += 1
    if j >= n:
        return False
    nxt = src[j]
    if nxt in '{,':
        return True
    if nxt == '=' and j + 1 < n and src[j + 1] == '>':
        return True
    if nxt == '>' and j > 0 and src[j - 1] == '=':
        return True
    return False


def _def_preceding(src: str, i: int) -> bool:
    """`(` 在 i 处能否作为「方法/函数定义」的参数列表开头？"""
    if i == 0:
        return True
    k = i - 1
    while k >= 0 and src[k] in ' \t':
        k -= 1
    if k < 0:
        return True
    prev = src[k]
    if prev in '{,;=>':
        return True
    if prev == '\n':
        return True
    if prev == '(':
        return False
    if prev == '[':
        return False
    if prev.isalnum() or prev in '_$':
        return True
    return False


def _has_arrow(inner: str) -> bool:
    """内文里在深度 0 出现 `=>`。"""
    depth = 0
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == '=' and depth == 0 and i + 1 < n and inner[i + 1] == '>':
            return True
        i += 1
    return False


def _extract_def_name(src: str, open_idx: int):
    """从 `(` 之前回扫，剥掉 static/async/get/set 修饰符，返回函数/方法名。"""
    k = open_idx - 1
    while k >= 0 and (src[k].isalnum() or src[k] in '_$'):
        k -= 1
    raw = src[k + 1:open_idx]
    if not raw:
        return None
    parts = raw.split()
    # 从右往左找第一个非修饰符
    for p in reversed(parts):
        if p not in _MODIFIERS and _IDENT.fullmatch(p):
            return p
    return None


def _split_params(inner: str):
    """在已经平衡过的内文里按外层逗号切分；保留嵌套结构。"""
    depth = 0
    cur = []
    parts = []
    for ch in inner:
        if ch in '([{':
            depth += 1
            cur.append(ch)
        elif ch in ')]}':
            depth -= 1
            cur.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append(''.join(cur))
    return parts


def collect(src: str) -> set:
    """收集剥离后的源码里所有能产生绑定的名字。"""
    names = set()

    for rx, kind in _BIND_PATTERNS:
        for m in rx.finditer(src):
            body = m.group(1)
            if kind == 'destruct' or ',' in body or ':' in body:
                for piece in re.split(r'[,]', body):
                    nm = _clean(piece.split('=')[0].split(':')[-1])
                    if nm:
                        names.add(nm)
            else:
                nm = _clean(body)
                if nm:
                    names.add(nm)

    # 平衡扫描：把所有 `name(...)` 形态的方法/函数的「名字 + 参数名」都收进来。
    # 三道闸：
    #  ① `_def_preceding`  判定 `(...)` 在该位置是参数列表（不是普通表达式括号）
    #  ② `_has_arrow`     若内文是 `... => ...`（箭头函数），即使外层 `(` 是嵌套表达式也算
    #  ③ `_is_definition` 排除单纯的调用（如 `rotateSizes(...)` 后跟 `;` / `)` / `,`）
    for open_idx, close_idx, inner in _iter_balanced(src):
        is_def = _def_preceding(src, open_idx) and _is_definition(src, close_idx)
        is_arrow = _has_arrow(inner)
        if not is_def and not is_arrow:
            continue
        if is_arrow:
            # 箭头函数：参数在 `=>` 之前的部分；可能是 `r` 或 `(a, b) = 1`
            depth = 0
            arr_idx = -1
            for i, ch in enumerate(inner):
                if ch in '([{':
                    depth += 1
                elif ch in ')]}':
                    depth -= 1
                elif ch == '=' and depth == 0 and i + 1 < len(inner) and inner[i + 1] == '>':
                    arr_idx = i
                    break
            params_str = inner[:arr_idx].strip() if arr_idx >= 0 else inner
            # 单参数箭头函数形如 `r`，否则是 `(a, b)`
            if params_str.startswith('(') and params_str.endswith(')'):
                params_str = params_str[1:-1]
            for p in params_str.split(','):
                p = p.strip().lstrip('.')
                nm = _clean(p.split('=')[0].split(':')[-1])
                if nm:
                    names.add(nm)
            continue
        nm = _extract_def_name(src, open_idx)
        if nm:
            names.add(nm)
        for p in _split_params(inner):
            p = p.strip()
            if p.startswith('...'):
                p = p[3:]
            nm = _clean(p.split('=')[0].split(':')[-1])
            if nm:
                names.add(nm)

    return names


def strip_code(src: str) -> str:
    """把注释与字符串字面量替换成空格（保留换行，保证行号不变）。"""
    out = list(src)
    n = len(src)
    i = 0
    prev = ''
    while i < n:
        ch = src[i]
        if ch == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != '\n':
                    out[k] = ' '
            i = j
            continue
        if ch == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = ' '
            i = j
            continue
        if ch == '/' and (prev == '' or prev in '({,=:[!&|?{};+-*%~^<>' or prev == '\n'):
            j = i + 1
            in_class = False
            while j < n:
                c = src[j]
                if c == '\\':
                    j += 2
                    continue
                if c == '[':
                    in_class = True
                elif c == ']':
                    in_class = False
                elif c == '/' and not in_class:
                    break
                elif c == '\n':
                    break
                j += 1
            if j < n and src[j] == '/':
                for k in range(i, j + 1):
                    out[k] = ' '
                i = j + 1
                prev = '/'
                continue
        if ch in '\'"`':
            quote = ch
            j = i + 1
            while j < n:
                c = src[j]
                if c == '\\':
                    j += 2
                    continue
                if c == quote:
                    break
                if c == '\n' and quote != '`':
                    break
                j += 1
            for k in range(i, min(j + 1, n)):
                if out[k] != '\n':
                    out[k] = ' '
            i = min(j + 1, n)
            prev = quote
            continue
        if not ch.isspace():
            prev = ch
        i += 1
    return ''.join(out)


def scan(path: Path):
    raw = path.read_text(encoding='utf-8')
    if not raw.strip():
        return []
    src = strip_code(raw)
    bound = _collect_imports(raw) | collect(src)
    hits, seen = [], set()
    for m in _CALL.finditer(src):
        nm = m.group(1)
        if nm in BUILTINS or nm in bound or nm in seen:
            continue
        if nm in _KEYWORD_CALLERS:
            continue
        seen.add(nm)
        hits.append((src[:m.start()].count('\n') + 1, nm))
    return hits


def main():
    files = []
    for d in DIRS:
        if d.is_dir():
            files.extend(d.glob('*.js'))
    files = sorted(set(files))
    total = 0
    for f in files:
        hits = scan(f)
        if not hits:
            continue
        total += len(hits)
        print(f'[缺失] {f.relative_to(ROOT.parent)}')
        for line, nm in hits:
            print(f'    L{line}: {nm}()')
    print(f'\n扫描 {len(files)} 个模块，发现 {total} 处未定义调用。')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())