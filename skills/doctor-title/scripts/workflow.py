"""Local-only coordination, compact novelty memory and safe content archiving."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import unicodedata
import uuid


VERSION = "doctor-content-v1-bottom128"
SKILLS = {
    "doctor-title": "doctorTitle-skill",
    "doctor-article": "doctorArticle-skill",
    "doctor-img": "doctorImg-skill",
}
CATEGORIES = {
    "prostate", "sexual-function", "reproductive-infection",
    "foreskin-phimosis", "male-infertility",
}
DISCLAIMER = "本文仅作健康科普，不能替代面诊、检查与个体化诊疗。"
# This is a lower-bound detector, not an exhaustive drug/device or claims database.
BANNED = (
    "据了解", "最", "百分百", "百分之百", "彻底根治", "根治", "包治", "包好",
    "绝对安全", "无副作用", "保证治愈", "一次治好", "药到病除", "立竿见影",
    "锦旗", "强力推荐", "强力推", "权威推荐", "专家背书", "技术领先",
    "西地那非", "他达拉非", "达泊西汀", "伟哥", "阿莫西林", "左氧氟沙星",
    "头孢", "阿奇霉素", "六味地黄丸", "鹿茸", "枸杞", "山药", "桑葚",
    "治疗仪", "诊疗仪", "超声仪", "碎石机", "达芬奇机器人", "CT机", "核磁共振仪",
)
RULES = (
    r"(?:治愈率|有效率|成功率|改善率)",
    r"(?:\d+(?:\.\d+)?\s*[%％]|[零一二三四五六七八九十百千万两]+成(?:患者|有效|治愈))",
    r"(?:[\d零一二三四五六七八九十百千万两]+(?:\.\d+)?\s*(?:元|块钱|人民币)|[¥￥$]\s*\d)",
    r"(?:百分之|千分之)[零一二三四五六七八九十百\d]+",
    r"(?:\d+|[一二三四五六七八九十几两]+)\s*(?:天|周|个?月|年).{0,8}(?:恢复|治好|治愈|见效)",
    r"(?:医院|机构).{0,25}比.{0,25}(?:好|强|优|领先)",
    r"(?:专家|名医).{0,12}(?:推荐|力荐|认可|背书)",
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def atomic_text(path: Path, text: str):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def discover(roots=()):
    here = Path(__file__).resolve()
    candidates = [Path(p).expanduser() for p in roots]
    candidates += [Path(p).expanduser() for p in os.environ.get("DOCTOR_SKILLS_ROOT", "").split(os.pathsep) if p]
    candidates += [here.parents[2], here.parents[4], Path.cwd(), Path("C:/demo")]
    for base in (Path.home(), Path.cwd()):
        candidates += [base / ".kilo/skills", base / ".claude/skills", base / ".codex/skills"]
    candidates += [Path.home() / ".config/kilo/skills"]
    found, checked = {}, []
    for root in dict.fromkeys(candidates):
        for name, repo in SKILLS.items():
            for location in (root / name, root / "skills" / name, root / repo / "skills" / name, root):
                path = location / "SKILL.md"
                if str(path) in checked:
                    continue
                checked.append(str(path))
                if name not in found and location.name == name and path.is_file():
                    text = path.read_text(encoding="utf-8-sig")
                    if re.match(r"\A---\s*\n", text) and re.search(rf"(?m)^name:\s*{re.escape(name)}\s*$", text):
                        found[name] = str(path.resolve())
    return {"ok": len(found) == len(SKILLS), "skills": found,
            "missing": [name for name in SKILLS if name not in found], "checked": checked}


def desktop_root():
    if os.name != "nt":
        raise ValueError("Non-Windows hosts must provide --output-root explicitly.")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    guid = GUID.from_buffer_copy(uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641").bytes_le)
    shell = ctypes.windll.shell32.SHGetKnownFolderPath
    shell.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell.restype = ctypes.c_long
    pointer = ctypes.c_void_p()
    result = shell(ctypes.byref(guid), 0, None, ctypes.byref(pointer))
    if result != 0:
        raise OSError("Cannot resolve the user's Desktop known folder.")
    try:
        desktop = Path(ctypes.wstring_at(pointer))
    finally:
        free = ctypes.windll.ole32.CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free(pointer)
    if not desktop.is_dir():
        raise ValueError("Resolved Desktop does not exist; supply --output-root.")
    return desktop / "doctorlist"


@contextmanager
def locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".workflow.lock"
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ValueError("Workflow is locked. If a process crashed, verify it stopped and inspect pending.json before manual recovery.") from exc
    try:
        handle.write(json_text({"pid": os.getpid(), "created_at": datetime.now().astimezone().isoformat()}))
        handle.close()
        yield
    finally:
        handle.close()
        path.unlink(missing_ok=True)


def memory(root: Path, today=None):
    today = today or date.today()
    directory = root / "memory"
    directory.mkdir(exist_ok=True)
    if (directory / "pending.json").exists():
        raise ValueError("Unfinished archive transaction: inspect memory/pending.json and its files/history before manual recovery; do not reset memory.")
    state_file, history_file = directory / "state.json", directory / "history.jsonl"
    existing_outputs = any((root / "title").glob("*")) or any((root / "img").glob("*"))
    if not state_file.exists():
        if history_file.exists() or existing_outputs:
            raise ValueError("Memory state is missing but history/output exists. Restore memory; refusing an empty reset.")
        atomic_text(history_file, "")
        atomic_text(state_file, json_text({"version": VERSION, "started_on": today.isoformat()}))
    state = load_json(state_file)
    if not isinstance(state, dict) or state.get("version") != VERSION:
        raise ValueError("Unsupported memory version; explicit migration is required.")
    if not isinstance(state.get("started_on"), str):
        raise ValueError("Invalid memory start date; restore the state.")
    if not history_file.is_file():
        raise ValueError("History is missing; restore it rather than starting empty.")
    elapsed = (today - date.fromisoformat(state["started_on"])).days
    if elapsed < 0:
        raise ValueError("Local date precedes memory start date; check system clock.")
    entries, ids = [], set()
    for line in history_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            raise ValueError("Blank/corrupt history record; restore the history.")
        entry = json.loads(line)
        required = {"id", "title", "normalized_title", "summary", "features", "fingerprints", "files", "version"}
        if not isinstance(entry, dict) or not required.issubset(entry) or entry["version"] != VERSION:
            raise ValueError("Invalid history record; restore the history.")
        if any(not isinstance(entry[k], str) or not entry[k] for k in ("id", "title", "normalized_title", "summary")):
            raise ValueError("Invalid history text fields; restore the history.")
        if entry["id"] in ids or entry["normalized_title"] != normalize(entry["title"]):
            raise ValueError("Duplicate or inconsistent history record; restore the history.")
        ids.add(entry["id"])
        if not isinstance(entry["features"], list) or any(not isinstance(s, str) for s in entry["features"]):
            raise ValueError("Invalid history features; restore the history.")
        if not isinstance(entry["fingerprints"], dict) or not isinstance(entry["files"], dict):
            raise ValueError("Invalid history fingerprints/files objects; restore the history.")
        if set(entry["files"]) != {"article", "manifest", "image"} or any(not isinstance(entry["files"][k], str) or not entry["files"][k] for k in ("article", "manifest")):
            raise ValueError("Invalid history output paths; restore the history.")
        for key in ("title", "article", "features"):
            hashes = entry["fingerprints"].get(key)
            if not isinstance(hashes, list) or not hashes or any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{16}", x) for x in hashes):
                raise ValueError("Invalid history fingerprint; restore the history.")
        for value in entry["files"].values():
            if value is None:
                continue
            if not isinstance(value, str) or not value:
                raise ValueError("Invalid history output path type; restore the history.")
            path = (root / value).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError("History references missing or unsafe output files; restore the complete archive.")
        entries.append(entry)
    known = {str(Path(value)) for e in entries for value in e["files"].values() if value}
    actual = {str(p.relative_to(root)) for directory_name in ("title", "img") for p in (root / directory_name).glob("*") if p.is_file()}
    if actual != known:
        raise ValueError("Archive/history mismatch: untracked or missing files. Reconcile without resetting history.")
    threshold = min(200 + 2 * elapsed, 400) / 1000
    return {"state": state, "threshold": threshold, "elapsed_days": elapsed,
            "notice": "相似度阈值已达到40%上限，不再自动提高；请评估选题空间。" if threshold == .4 else None,
            "history": entries}


def normalize(text):
    text = unicodedata.normalize("NFKC", text).lower()
    for before, after in (("小便", "排尿"), ("尿尿", "排尿"), ("硬不起来", "勃起困难"),
                          ("勃起障碍", "勃起困难"), ("打飞机", "自慰")):
        text = text.replace(before, after)
    return "".join(char for char in text if char.isalnum())


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def grams(text):
    text = normalize(text)
    return {digest(text[i:i + 2]) for i in range(max(1, len(text) - 1))} if text else set()


def fingerprints(draft):
    return {
        "title": sorted(grams(draft["title"])),
        "article": sorted(grams(draft.get("article", "").replace(DISCLAIMER, "")))[:128],
        "features": sorted({digest(normalize(value)) for value in draft["features"]}),
    }


def overlap(left, right, sketch=False):
    left, right = set(left), set(right)
    sample = sorted(left | right)[:128] if sketch else left | right
    return len((left & right) & set(sample)) / len(sample) if sample else 0.0


def similarity(draft, context):
    current = fingerprints(draft)
    comparisons = []
    for old in context["history"]:
        scores = {key: overlap(current[key], old["fingerprints"][key], key == "article") for key in current}
        same_title = normalize(draft["title"]) == old["normalized_title"]
        score = max(scores.values())
        comparisons.append({"id": old["id"], "title": old["title"], "summary": old["summary"],
                            "score": score, "components": scores, "same_title": same_title})
    comparisons.sort(key=lambda item: item["score"], reverse=True)
    score = comparisons[0]["score"] if comparisons else 0.0
    return {"ok": score <= context["threshold"] and not any(item["same_title"] for item in comparisons),
            "score": score, "threshold": context["threshold"], "notice": context["notice"],
            "nearest": comparisons[:5], "algorithm": VERSION}


def reader_article_errors(article):
    errors = []
    if re.search(r"https?://|www\.", article, re.I) or re.search(r"\[[^\]]+\]\([^)]+\)", article):
        errors.append("Reader article must not contain links or URLs; keep sources in sources.")
    if re.search(r"(?m)^\s*#{1,6}\s|^\s*[-*]\s|\*\*", article):
        errors.append("Reader article must be plain prose: no Markdown headings, lists, or bold.")
    normalized = normalize(article)
    for phrase in (
        "事实依据", "急症依据", "不能直接当作病名", "并不能直接推出", "直接翻译成", "诊断标签",
        "把下一步放在门诊咨询上", "就诊不需要先证明自己得了什么病",
        "才便于讨论接下来如何评估", "把所有情况都塞进一句身体被掏空里",
        "持续的不适和生活困扰是值得认真沟通的需求", "让担心有一个可以说清的地方",
        "先别把自慰和身体受损画等号", "先别把自慰和身体受损划等号",
        "没有一条人人都适用的正常线",
    ):
        if normalize(phrase) in normalized:
            errors.append(f"Prompt-like wording leaked into article: {phrase}")
    count = len(re.findall(r"[\u4e00-\u9fff]", article.replace(DISCLAIMER, "")))
    if not 560 <= count <= 620:
        errors.append(f"Article must contain 560-620 Chinese characters, target 600, excluding disclaimer; got {count}.")
    return errors


def article_style_warnings(article):
    """Context-sensitive editing hints, not a verdict on authorship or safety."""
    sentences = [s.strip() for s in re.split(r"[。！？!?\n]+", article) if s.strip()]
    warnings = []
    patterns = (
        ("consultation_padding", 2,
         r"下一步.{0,12}(?:沟通|评估|安排|咨询)|(?:把|将).{0,14}(?:问题|变化).{0,10}(?:说清|交代|讲清)|备忘录|带上已有|就诊资料",
         "多句围绕问诊准备或沟通流程；核对是否回答标题。非就诊准备主题不要用此类内容凑篇幅，必要信息仍应保留。"),
        ("formulaic_contrasts", 2,
         r"(?:先别|别急着|不要急着).{0,24}(?:划等号|画等号|对号入座)|不是.{0,24}而是|不是结论.{0,6}(?:只是|是)线索",
         "反复纠偏或对比；检查是否重复同一意思。保留真正区分不同情况的信息，不机械删除否定句。"),
        ("abstract_closer", 1,
         r"(?:让|给).{0,12}(?:担心|焦虑|困扰).{0,16}(?:地方|出口|安放|方向)|把这一步安排起来",
         "检查抒情收尾是否提供新信息；空泛安慰可删除，具体医学条件与行动不可删。"),
    )
    for code, minimum, pattern, message in patterns:
        matches = [s for s in sentences if re.search(pattern, s)]
        if len(matches) >= minimum:
            warnings.append({"code": code, "excerpts": matches[:3], "message": message})
    return warnings


def content_errors(draft, final=False):
    errors = []
    if not isinstance(draft, dict):
        return ["Draft must be a JSON object."]
    for key, maximum in (("title", 160), ("summary", 400)):
        value = draft.get(key)
        if not isinstance(value, str) or not normalize(value) or len(value) > maximum or (key == "title" and ("\n" in value or "\r" in value)):
            errors.append(f"{key} must be a nonempty string, <= {maximum} characters; title must be one line.")
    if draft.get("category") not in CATEGORIES:
        errors.append("category must be one of the five supported category codes.")
    features = draft.get("features")
    if not isinstance(features, list) or not 4 <= len(features) <= 8 or any(not isinstance(s, str) or not normalize(s) or len(s) > 60 for s in features):
        errors.append("features must contain 4-8 concrete nonempty phrases, each <= 60 characters.")
    elif len({normalize(s) for s in features}) != len(features):
        errors.append("features must be distinct after normalization.")
    if "article" in draft and not isinstance(draft["article"], str):
        errors.append("article must be a string.")
    elif isinstance(draft.get("article"), str) and draft["article"].strip():
        errors.extend(reader_article_errors(draft["article"]))
    banner = draft.get("banner_text", [])
    if not isinstance(banner, list) or any(not isinstance(s, str) or not s.strip() for s in banner):
        errors.append("banner_text must be an array of nonempty visible strings.")
    public = [draft.get(k, "") for k in ("title", "summary", "article")]
    public += features if isinstance(features, list) else []
    public += banner if isinstance(banner, list) else []
    text = unicodedata.normalize("NFKC", "\n".join(s for s in public if isinstance(s, str)))
    for term in BANNED:
        if term.casefold() in text.casefold():
            errors.append(f"Disallowed visible wording: {term}")
    for pattern in RULES:
        if re.search(pattern, text, re.I):
            errors.append(f"Disallowed claim/data: {re.search(pattern, text, re.I).group()}")
    if final:
        for key in ("palette", "image_tool"):
            if not isinstance(draft.get(key), str) or not draft[key].strip() or len(draft[key]) > 120:
                errors.append(f"{key} must describe the palette/tool (or no available tool), <= 120 characters.")
        for key in ("article", "image_prompt"):
            if not isinstance(draft.get(key), str) or len(draft[key].strip()) < 40:
                errors.append(f"{key} is missing or too short to be a complete deliverable.")
        if isinstance(draft.get("article"), str) and len(normalize(draft["article"].replace(DISCLAIMER, ""))) < 40:
            errors.append("article must contain substantive text beyond punctuation and the standard disclaimer.")
        if not banner:
            errors.append("banner_text is required even when generation is skipped.")
        status = draft.get("image_status")
        if status not in ("generated", "skipped"):
            errors.append("image_status must be generated or skipped.")
        if status == "skipped" and not (isinstance(draft.get("skip_reason"), str) and draft["skip_reason"].strip()):
            errors.append("Skipped generation needs a concrete skip_reason.")
        if status == "generated" and not (isinstance(draft.get("image_path"), str) and draft["image_path"].strip()):
            errors.append("Generated image needs a local image_path.")
        if status == "skipped" and draft.get("image_path"):
            errors.append("Skipped generation must not include an image_path.")
        loaded = draft.get("loaded_skills")
        if not isinstance(loaded, list) or any(not isinstance(s, str) for s in loaded) or set(loaded) != set(SKILLS):
            errors.append("loaded_skills must attest that all three skills were actually read.")
        review = draft.get("review")
        required = ["medical", "compliance", "originality", "semantic", "editorial"] + (["visual"] if status == "generated" else [])
        if not isinstance(review, dict) or any(review.get(key) is not True for key in required):
            errors.append("Required medical/compliance/originality/semantic/editorial (and generated image visual) reviews must be true after actual review.")
        sources = draft.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append("At least one verified medical source is required.")
        else:
            for source in sources:
                if not isinstance(source, dict) or any(not isinstance(source.get(k), str) or not source[k].strip() for k in ("title", "url", "claim")):
                    errors.append("Each source needs title, url and supported claim.")
                elif not re.match(r"^https://[^/\s]+(?:/|$)", source["url"]):
                    errors.append("Source URLs must be public HTTPS URLs, actually checked by the Agent.")
    return errors


def image_info(data):
    """Validate container bounds and dimensions; visual decoding remains an Agent task."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        import zlib
        position, dimensions, has_data, ended = 8, None, False, False
        compressed, header = [], None
        while position + 12 <= len(data):
            length = struct.unpack_from(">I", data, position)[0]
            kind = data[position + 4:position + 8]
            end = position + 12 + length
            if end > len(data):
                raise ValueError("Truncated PNG chunk.")
            body = data[position + 8:position + 8 + length]
            crc = struct.unpack_from(">I", data, end - 4)[0]
            if zlib.crc32(kind + body) & 0xffffffff != crc:
                raise ValueError("Invalid PNG checksum.")
            if position == 8 and (kind != b"IHDR" or length != 13):
                raise ValueError("PNG must begin with a valid IHDR.")
            if kind == b"IHDR":
                if dimensions is not None or length != 13:
                    raise ValueError("Invalid repeated PNG header.")
                dimensions = struct.unpack_from(">II", body)
                header = struct.unpack_from(">IIBBBBB", body)
            if kind == b"IDAT":
                has_data = has_data or bool(body)
                compressed.append(body)
            if kind == b"IEND":
                ended = length == 0 and end == len(data)
                break
            position = end
        if dimensions and has_data and ended:
            width, height, depth, color, compression, filtering, interlace = header
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if not width or not height or depth not in depths.get(color, ()) or compression or filtering or interlace not in (0, 1):
                raise ValueError("Unsupported PNG pixel format.")
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            passes = [(0, 0, 1, 1)] if interlace == 0 else [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
            rows = []
            for x, y, dx, dy in passes:
                columns, count = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
                if columns and count:
                    rows.append((1 + (columns * channels * depth + 7) // 8, count))
            expected = sum(size * count for size, count in rows)
            if expected > 100 * 1024 * 1024:
                raise ValueError("Decoded PNG exceeds the 100 MiB limit.")
            try:
                decoder = zlib.decompressobj()
                pixels = decoder.decompress(b"".join(compressed), expected + 1)
            except zlib.error as exc:
                raise ValueError("PNG pixel data cannot be decompressed.") from exc
            if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError("PNG pixel data has an invalid length or stream.")
            offset = 0
            for size, count in rows:
                if any(pixels[offset + row * size] > 4 for row in range(count)):
                    raise ValueError("Invalid PNG scanline filter.")
                offset += size * count
            return ".png", *dimensions
        raise ValueError("Incomplete PNG image.")
    if data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"):
        position, dimensions = 2, None
        while position + 4 <= len(data):
            if data[position] != 255:
                break
            while position < len(data) and data[position] == 255:
                position += 1
            marker = data[position]
            position += 1
            if marker in (0xd8, 0x01) or 0xd0 <= marker <= 0xd7:
                continue
            if marker == 0xd9:
                break
            length = struct.unpack_from(">H", data, position)[0]
            if length < 2 or position + length > len(data):
                raise ValueError("Truncated JPEG segment.")
            if marker in (0xc0, 0xc1, 0xc2) and length >= 8:
                height, width = struct.unpack_from(">HH", data, position + 3)
                dimensions = (width, height)
            if marker == 0xda:
                if dimensions and length >= 6 and position + length < len(data) - 2:
                    return ".jpg", *dimensions
                break
            position += length
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        if len(data) < 20 or struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
            raise ValueError("Invalid WebP length.")
        dimensions, has_pixels, position = None, False, 12
        while position + 8 <= len(data):
            kind = data[position:position + 4]
            length = struct.unpack_from("<I", data, position + 4)[0]
            chunk = data[position + 8:position + 8 + length]
            if len(chunk) != length:
                raise ValueError("Truncated WebP chunk.")
            if kind == b"VP8X" and length == 10:
                if chunk[0] & 2:
                    raise ValueError("Animated WebP is not a static Banner.")
                dimensions = (1 + int.from_bytes(chunk[4:7], "little"), 1 + int.from_bytes(chunk[7:10], "little"))
            elif kind == b"VP8 " and length >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
                dimensions = dimensions or (int.from_bytes(chunk[6:8], "little") & 0x3fff, int.from_bytes(chunk[8:10], "little") & 0x3fff)
                has_pixels = True
            elif kind == b"VP8L" and length >= 5 and chunk[0] == 0x2f:
                bits = int.from_bytes(chunk[1:5], "little")
                dimensions = dimensions or ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1)
                has_pixels = True
            position += 8 + length + length % 2
        if dimensions and has_pixels and position == len(data):
            return ".webp", *dimensions
    raise ValueError("Unsupported or damaged image. Provide a visually verified PNG, JPEG or static WebP.")


def safe_stem(title):
    value = unicodedata.normalize("NFKC", title)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not value:
        value = "title-" + digest(title)[:8]
    if re.match(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", value, re.I):
        value = "_" + value
    if len(value.encode("utf-16-le")) > 140:
        value = value.encode("utf-16-le")[:116].decode("utf-16-le", errors="ignore").rstrip(" .") + "-" + digest(title)[:8]
    return value


def available_stem(root, title):
    base = safe_stem(title)
    used = {p.stem.casefold() for folder in ("img", "title") for p in (root / folder).glob("*")}
    candidate, count = base, 0
    while candidate.casefold() in used:
        count += 1
        candidate = base + "-" + digest(title)[:8] + (f"-{count}" if count > 1 else "")
    return candidate


def archive(root, draft, context, draft_dir):
    errors = content_errors(draft, final=True)
    article = draft.get("article") if isinstance(draft, dict) else None
    style_warnings = article_style_warnings(article) if isinstance(article, str) else []
    if errors:
        return {"ok": False, "errors": errors, "style_warnings": style_warnings}
    report = similarity(draft, context)
    if not report["ok"]:
        return {"ok": False, "errors": ["Similarity exceeds threshold or title duplicates history."], "similarity": report, "style_warnings": style_warnings}
    data, dimensions, extension = None, None, None
    if draft["image_status"] == "generated":
        path = Path(draft["image_path"])
        path = path if path.is_absolute() else draft_dir / path
        if path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("Image exceeds the 50 MiB archive limit.")
        data = path.read_bytes()
        extension, width, height = image_info(data)
        if not (1500 <= width <= 1700 and 800 <= height <= 1000):
            raise ValueError("Banner width must be 1500-1700 pixels and height 800-1000 pixels (inclusive); exact 16:9 is not required.")
        dimensions = {"width": width, "height": height}
    stem = available_stem(root, draft["title"])
    files = {"article": f"title/{stem}.md", "manifest": f"title/{stem}.json",
             "image": f"img/{stem}{extension}" if data else None}
    record_id = uuid.uuid4().hex
    created_at = datetime.now().astimezone().isoformat()
    entry = {"id": record_id, "version": VERSION, "created_at": created_at,
             "title": draft["title"], "normalized_title": normalize(draft["title"]),
             "category": draft["category"], "summary": draft["summary"], "features": draft["features"],
             "fingerprints": fingerprints(draft), "files": files, "image_status": draft["image_status"],
             "palette": draft["palette"]}
    manifest = {k: v for k, v in draft.items() if k not in ("article", "image_path", "review_notes")}
    manifest.update({"id": record_id, "version": VERSION, "created_at": created_at,
                     "files": files, "dimensions": dimensions, "similarity": report,
                     "style_warnings": style_warnings,
                     "image_sha256": hashlib.sha256(data).hexdigest() if data else None})
    body = f"# {draft['title']}\n\n{draft['article'].strip()}\n"
    if DISCLAIMER not in body:
        body += "\n" + DISCLAIMER + "\n"
    outputs = [(root / files["article"], body.encode("utf-8")),
               (root / files["manifest"], json_text(manifest).encode("utf-8"))]
    if data:
        outputs.append((root / files["image"], data))
    for folder in ("title", "img"):
        (root / folder).mkdir(exist_ok=True)
    pending = root / "memory/pending.json"
    atomic_text(pending, json_text({"id": record_id, "files": files, "entry": entry}))
    created = []
    committed = False
    try:
        for path, payload in outputs:
            with path.open("xb") as handle:
                created.append(path)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        history = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in context["history"] + [entry])
        atomic_text(root / "memory/history.jsonl", history)
        committed = True
        pending.unlink()
    except Exception:
        # After the history commit, keep files/journal for recovery instead of losing data.
        if not committed:
            for path in created:
                path.unlink(missing_ok=True)
            pending.unlink(missing_ok=True)
        raise
    return {"ok": True, "title": draft["title"], "output_root": str(root), "files": files,
            "image_status": draft["image_status"], "skip_reason": draft.get("skip_reason"), "similarity": report,
            "style_warnings": style_warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "context", "check", "archive"))
    parser.add_argument("--skills-root", action="append", default=[])
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--draft", type=Path)
    args = parser.parse_args(argv)
    try:
        found = discover(args.skills_root)
        if not found["ok"] or args.command == "preflight":
            result = found
        else:
            if args.command in ("check", "archive") and args.draft is None:
                raise ValueError("--draft is required for check/archive.")
            root = (args.output_root or desktop_root()).expanduser().resolve()
            with locked(root):
                context = memory(root)
                if args.command == "context":
                    result = {"ok": True, "output_root": str(root), **context}
                else:
                    draft = load_json(args.draft)
                    errors = content_errors(draft, final=args.command == "archive")
                    if errors:
                        result = {"ok": False, "errors": errors}
                    elif args.command == "archive":
                        result = archive(root, draft, context, args.draft.resolve().parent)
                    else:
                        result = similarity(draft, context)
                    article = draft.get("article") if isinstance(draft, dict) else None
                    result["style_warnings"] = article_style_warnings(article) if isinstance(article, str) else []
    except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json_text(result), end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
