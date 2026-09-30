"""按平台获取：元信息、文字、章节三样分别取。

每个 acquire_* 返回 Result 字典：
    code/message  状态码（见 common.CODES）与一句说明
    meta          title、url、platform、source、author、podcast、published、published_date、
                  duration_s、description、chapters[{start_s,title}]、interaction、stable_id
    kind          article / document / subs_manual / subs_auto / platform_transcript / asr / images
    segments      语音类段落 [{ts,label,text,turn}]；文章类用 blocks（Markdown 段落列表）
    audio         需要本机转写时的音频路径
    lang          zh / en
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from xml.etree import ElementTree as ET

from . import htmltext
from .common import beijing_date, download, hms, http_get, lang_of, parse_hms, run
from .transcript import (
    cues_to_segments,
    docx_paragraphs,
    parse_labeled_lines,
    parse_subtitles,
    pdf_text,
    plain_paragraphs,
    pptx_slides,
)

AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr",
             ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".flv", ".mpeg", ".mpg"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".gif", ".bmp", ".tiff"}
YTDLP_HOSTS = {
    "youtube": ("youtube.com", "youtu.be"),
    "bilibili": ("bilibili.com", "b23.tv"),
    "x": ("x.com", "twitter.com"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com", "rednote.com"),
    "ytdlp": ("ximalaya.com", "vimeo.com", "soundcloud.com", "music.163.com", "ted.com", "podbean.com"),
}
PLATFORM_NAMES = {
    "xiaoyuzhou": "小宇宙", "apple": "Apple Podcasts", "youtube": "YouTube", "bilibili": "B站", "x": "X",
    "douyin": "抖音", "xiaohongshu": "小红书", "shipinhao": "视频号", "feishu_minutes": "飞书妙记",
    "wechat": "微信公众号", "web": "网页", "pdf": "PDF", "ytdlp": "网页视频", "local": "本地文件",
}


def result(code="OK", message="", **kw) -> dict:
    base = {"code": code, "message": message, "meta": {}, "kind": None, "segments": [], "blocks": [],
            "audio": None, "lang": None}
    base.update(kw)
    return base


# ---------- 识别输入 ----------

def detect(target: str) -> dict:
    p = Path(target).expanduser()
    if p.exists():
        ext = p.suffix.lower()
        if p.is_dir():
            return {"kind": "file", "platform": "local", "type": "dir", "path": str(p)}
        typ = ("audio" if ext in AUDIO_EXT else "image" if ext in IMAGE_EXT else ext.lstrip(".") or "txt")
        return {"kind": "file", "platform": "local", "type": typ, "path": str(p)}
    url = target.strip()
    host = urllib.parse.urlparse(url).netloc.lower()
    path = urllib.parse.urlparse(url).path
    if "xiaoyuzhoufm.com" in host:
        return {"kind": "url", "platform": "xiaoyuzhou", "url": url}
    if "podcasts.apple.com" in host:
        return {"kind": "url", "platform": "apple", "url": url}
    if "channels.weixin.qq.com" in host:
        return {"kind": "url", "platform": "shipinhao", "url": url}
    if "mp.weixin.qq.com" in host:
        return {"kind": "url", "platform": "wechat", "url": url}
    if ("feishu.cn" in host or "larksuite.com" in host) and "/minutes/" in path:
        return {"kind": "url", "platform": "feishu_minutes", "url": url}
    for plat, hosts in YTDLP_HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return {"kind": "url", "platform": plat, "url": url}
    if path.lower().endswith(".pdf"):
        return {"kind": "url", "platform": "pdf", "url": url}
    return {"kind": "url", "platform": "web", "url": url}


# ---------- 章节 ----------

TIMELINE = re.compile(r"^\s*(?:[\-*•·▶️⏱️🕐\s]|\d+[.、)]\s)*[\[(（]?(\d{1,2}:\d{2}(?::\d{2})?)[\])）]?\s*[-—–:：|｜]?\s*(.{2,300}?)\s*$")


def _short_title(title: str, limit: int = 36) -> str:
    title = re.sub(r"\s+", " ", title).strip(" -—–:：|｜")
    if len(title) <= limit:
        return title
    cuts = [i for i in (title.find(c) for c in "：，。？（(") if 8 <= i <= limit]
    return title[:min(cuts)] if cuts else title[:limit].rstrip("，：、") + "…"


def chapters_from_text(text: str, duration_s: float | None = None) -> list[dict]:
    """从节目简介里认出“00:12 话题”这样的时间轴。至少 3 条才算。"""
    out = []
    for line in (text or "").splitlines():
        m = TIMELINE.match(line.replace("**", ""))
        if not m:
            continue
        start = parse_hms(m.group(1))
        title = _short_title(m.group(2))
        if duration_s and start > duration_s + 60:
            continue
        if title and (not out or start >= out[-1]["start_s"]):
            out.append({"start_s": start, "title": title})
    return out if len(out) >= 3 else []


# ---------- 小宇宙 ----------

def acquire_xiaoyuzhou(url: str, job_dir: Path) -> dict:
    try:
        final, html = http_get(url)
    except Exception as e:  # noqa: BLE001
        return result("FAILED", f"打开小宇宙页面失败：{e}")
    (job_dir / "raw").mkdir(parents=True, exist_ok=True)
    (job_dir / "raw" / "page.html").write_text(html, encoding="utf-8")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    ep = {}
    if m:
        try:
            ep = json.loads(m.group(1))["props"]["pageProps"].get("episode") or {}
        except (ValueError, KeyError):
            ep = {}
    ld = {}
    m2 = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    if m2:
        try:
            ld = json.loads(m2.group(1))
        except ValueError:
            ld = {}
    if not ep and not ld:
        return result("NOT_FOUND", "页面里没有单集数据，可能链接有误或单集已下架")
    podcast = ep.get("podcast") or {}
    eid = ep.get("eid") or re.search(r"/episode/([0-9a-f]+)", url).group(1)
    shownotes_html = ep.get("shownotes") or ""
    shownotes = "\n".join(b for b in htmltext.to_blocks(htmltext.parse(shownotes_html)) if b != "〔图〕") \
        if shownotes_html else (ep.get("description") or ld.get("description") or "")
    duration = ep.get("duration")
    if not duration and ld.get("timeRequired"):
        mm = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", ld["timeRequired"])
        if mm:
            h, mi, s = (int(x or 0) for x in mm.groups())
            duration = h * 3600 + mi * 60 + s
    published = ep.get("pubDate") or ld.get("datePublished")
    meta = {
        "platform": "xiaoyuzhou",
        "title": (ep.get("title") or ld.get("name") or "").strip(),
        "url": f"https://www.xiaoyuzhoufm.com/episode/{eid}",
        "stable_id": eid,
        "podcast": podcast.get("title"),
        "author": podcast.get("author"),
        "source": f"播客《{podcast.get('title')}》· 小宇宙" if podcast.get("title") else "小宇宙",
        "published": published,
        "published_date": beijing_date(published),
        "duration_s": duration,
        "description": shownotes.strip(),
        "chapters": chapters_from_text(shownotes, duration),
        "interaction": {k: ep.get(k) for k in ("playCount", "clapCount", "commentCount", "favoriteCount") if ep.get(k) is not None},
    }
    audio_url = (ep.get("enclosure") or {}).get("url") or ((ep.get("media") or {}).get("source") or {}).get("url")
    if not audio_url:
        m3 = re.search(r'og:audio"?\s+content="([^"]+)"', html)
        audio_url = m3.group(1) if m3 else None
    if not audio_url:
        code = "PAID" if (ep.get("payType") and ep.get("payType") != "FREE") else "UNSUPPORTED"
        return result(code, "页面没有音频地址（付费单集或仅 App 可听）", meta=meta)
    audio = job_dir / "raw" / ("audio" + (Path(urllib.parse.urlparse(audio_url).path).suffix or ".m4a"))
    if not audio.exists() and not download(audio_url, audio):
        return result("FAILED", "音频下载失败", meta=meta)
    return result(meta=meta, kind="asr", audio=str(audio), lang="zh" if lang_of(meta["title"] + shownotes[:500]) == "zh" else "en")


# ---------- Apple Podcasts / RSS ----------

PODCAST_NS = "https://podcastindex.org/namespace/1.0"


def _rss_transcript(feed_url: str, title: str, guid: str | None, job_dir: Path):
    """在 RSS 里找同一集的创作者文字稿（podcast:transcript）。返回 (段落, 类型) 或 (None, None)。"""
    try:
        _, xml = http_get(feed_url, timeout=60)
        root = ET.fromstring(xml.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return None, None
    for item in root.iter("item"):
        t = (item.findtext("title") or "").strip()
        g = (item.findtext("guid") or "").strip()
        if (guid and g == guid) or (title and t == title):
            best = None
            for tr in item.findall(f"{{{PODCAST_NS}}}transcript"):
                typ = tr.get("type", "")
                rank = {"application/json": 0, "text/vtt": 1, "application/srt": 2, "application/x-subrip": 2,
                        "text/srt": 2, "text/html": 3, "text/plain": 4}.get(typ, 9)
                if best is None or rank < best[0]:
                    best = (rank, tr.get("url"), typ)
            if not best or not best[1]:
                return None, None
            _, text = http_get(best[1], timeout=60)
            (job_dir / "raw" / "rss_transcript.txt").write_text(text, encoding="utf-8")
            if best[2] == "application/json":
                data = json.loads(text)
                segs = [{"ts": None if s.get("startTime") is None else hms(s["startTime"]),
                         "label": s.get("speaker"), "text": (s.get("body") or "").strip(), "turn": False}
                        for s in data.get("segments", []) if (s.get("body") or "").strip()]
                return segs, "platform_transcript"
            if best[2] in {"text/vtt", "application/srt", "application/x-subrip", "text/srt"}:
                lang = lang_of(text)
                return cues_to_segments(parse_subtitles(text), lang), "subs_manual"
            paras = plain_paragraphs(re.sub(r"<[^>]+>", "\n", text))
            segs, info = parse_labeled_lines(paras)
            if info["matched"] >= 0.5:
                return segs, "platform_transcript"
            return [{"ts": None, "label": None, "text": p, "turn": False} for p in paras], "platform_transcript"
    return None, None


def acquire_apple(url: str, job_dir: Path) -> dict:
    pid = re.search(r"/id(\d+)", url)
    eid = re.search(r"[?&]i=(\d+)", url)
    if not pid:
        return result("NOT_FOUND", "链接里没有节目编号 id")
    try:
        _, body = http_get(f"https://itunes.apple.com/lookup?id={pid.group(1)}&entity=podcastEpisode&limit=300")
        data = json.loads(body)
    except Exception as e:  # noqa: BLE001
        return result("FAILED", f"Apple 查询接口失败：{e}")
    items = data.get("results", [])
    show = next((x for x in items if x.get("kind") == "podcast" or x.get("wrapperType") == "track"), {})
    ep = next((x for x in items if eid and str(x.get("trackId")) == eid.group(1)), None)
    if ep is None:
        return result("NOT_FOUND", "查询接口的最近 300 集里没有这一集（较老的单集请给小宇宙或 RSS 链接）")
    meta = {
        "platform": "apple",
        "title": ep.get("trackName", "").strip(),
        "url": url.split("&")[0],
        "stable_id": str(ep.get("trackId")),
        "podcast": ep.get("collectionName") or show.get("collectionName"),
        "author": show.get("artistName"),
        "source": f"播客《{ep.get('collectionName') or show.get('collectionName')}》· Apple Podcasts",
        "published": ep.get("releaseDate"),
        "published_date": beijing_date(ep.get("releaseDate")),
        "duration_s": (ep.get("trackTimeMillis") or 0) / 1000 or None,
        "description": ep.get("description") or ep.get("shortDescription") or "",
        "feed_url": show.get("feedUrl"),
    }
    meta["chapters"] = chapters_from_text(meta["description"], meta["duration_s"])
    lang = lang_of(meta["title"] + meta["description"][:500])
    (job_dir / "raw").mkdir(parents=True, exist_ok=True)
    if meta.get("feed_url"):
        segs, kind = _rss_transcript(meta["feed_url"], meta["title"], ep.get("episodeGuid"), job_dir)
        if segs:
            return result(meta=meta, kind=kind, segments=segs, lang=lang)
    audio_url = ep.get("episodeUrl")
    if not audio_url:
        return result("UNSUPPORTED", "没有音频地址", meta=meta)
    audio = job_dir / "raw" / ("audio" + (Path(urllib.parse.urlparse(audio_url).path).suffix or ".m4a"))
    if not audio.exists() and not download(audio_url, audio):
        return result("FAILED", "音频下载失败", meta=meta)
    return result(meta=meta, kind="asr", audio=str(audio), lang=lang)


# ---------- yt-dlp 平台 ----------

def _ytdlp_error(stderr: str) -> tuple[str, str]:
    s = stderr or ""
    errors = [l for l in s.splitlines() if "ERROR" in l]
    lines = s.strip().splitlines()
    msg = (errors[-1] if errors else lines[-1] if lines else "")[:300]
    low = s.lower()
    if "impersonate target" in low:
        return "BLOCKED", "yt-dlp 缺少模拟浏览器的依赖（curl_cffi），平台签名校验过不去：" + msg
    if "sign in to confirm" in low or "not a bot" in low or "logged in" in low or "login" in low or "cookies" in low:
        return "NEED_LOGIN", msg
    if "unsupported url" in low:
        return "UNSUPPORTED", msg
    if "404" in low or "not available" in low or "unavailable" in low or "removed" in low or "private" in low:
        return "NOT_FOUND", msg
    if "nsig" in low or "unable to extract" in low or "only images are available" in low:
        return "NEED_UPGRADE", msg
    if "403" in low or "fresh cookies" in low or "a_bogus" in low or "signature" in low:
        return "BLOCKED", msg
    return "FAILED", msg


def _cookie_args(platform: str, opts: dict, force: bool = False) -> list[str]:
    profile = opts.get("cookies_profile")
    cfile = opts.get("cookies_file")
    if platform == "x":
        return ["--cookies", cfile] if cfile else []
    if platform in {"bilibili", "douyin", "xiaohongshu"} or force:
        args = ["--cookies-from-browser", f"firefox:{profile}"] if profile else []
        if platform in {"douyin", "xiaohongshu"}:
            args += ["--impersonate", "chrome"]
        return args
    return []


def _pick_sub(info: dict, lang: str, platform: str):
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k not in {"danmaku", "live_chat"}}
    auto = info.get("automatic_captions") or {}
    prefs = [lang, f"{lang}-Hans", f"{lang}-CN", f"{lang}-orig"] if lang == "zh" else [lang, f"{lang}-US", f"{lang}-GB", f"{lang}-orig"]
    if platform == "bilibili":
        up = [k for k in manual if not k.startswith("ai-")]
        for k in up:
            if k.split("-")[0] == lang:
                return k, "subs_manual"
        if f"ai-{lang}" in manual:
            return f"ai-{lang}", "subs_auto"
    for k in prefs:
        if k in manual:
            return k, "subs_manual"
    for k in manual:
        if k.split("-")[0] == lang:
            return k, "subs_manual"
    for k in [f"{lang}-orig", lang]:
        if k in auto:
            return k, "subs_auto"
    return None, None


def acquire_ytdlp(url: str, platform: str, job_dir: Path, opts: dict) -> dict:
    raw = job_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    base = ["yt-dlp", "--no-warnings", "--no-playlist"]
    cookies = _cookie_args(platform, opts)
    r = run(base + cookies + ["--dump-single-json", "--skip-download", url], timeout=180)
    if r.returncode != 0 and "impersonate target" in (r.stderr or "").lower() and "--impersonate" in cookies:
        cookies = [c for c in cookies if c not in {"--impersonate", "chrome"}]
        r = run(base + cookies + ["--dump-single-json", "--skip-download", url], timeout=180)
    if r.returncode != 0 and platform == "youtube" and opts.get("cookies_profile"):
        code, _ = _ytdlp_error(r.stderr)
        if code in {"NEED_LOGIN", "BLOCKED"}:
            cookies = _cookie_args(platform, opts, force=True)
            r = run(base + cookies + ["--dump-single-json", "--skip-download", url], timeout=180)
    if r.returncode != 0:
        code, msg = _ytdlp_error(r.stderr)
        if platform in {"douyin", "xiaohongshu"}:
            msg += "（这类平台常失败：可找同一内容在其他平台的版本，或请用户手动导出文件）"
        if platform == "x" and not opts.get("cookies_file"):
            code, msg = "NEED_LOGIN", "X 需要登录：请提供单站导出的 cookies.txt 路径（见 fetch.md）"
        return result(code, msg)
    info = json.loads(r.stdout)
    (raw / "info.json").write_text(json.dumps(info, ensure_ascii=False)[:2_000_000], encoding="utf-8")
    title = (info.get("title") or "").strip()
    lang = (info.get("language") or "").split("-")[0] or lang_of(title + (info.get("description") or "")[:500])
    lang = "zh" if lang.startswith("zh") else ("en" if lang == "en" else lang_of(title))
    channel = info.get("channel") or info.get("uploader") or ""
    published = info.get("timestamp") or info.get("release_timestamp") or info.get("upload_date")
    meta = {
        "platform": platform,
        "title": title,
        "url": info.get("webpage_url") or url,
        "stable_id": info.get("id"),
        "author": channel,
        "source": f"{PLATFORM_NAMES.get(platform, info.get('extractor_key', ''))} · {channel}".strip(" ·"),
        "published": published,
        "published_date": beijing_date(published),
        "duration_s": info.get("duration"),
        "description": info.get("description") or "",
        "chapters": [{"start_s": c.get("start_time", 0), "title": c.get("title", "")} for c in (info.get("chapters") or [])],
        "interaction": {k: info.get(k) for k in ("view_count", "like_count", "comment_count", "repost_count") if info.get(k) is not None},
    }
    if not meta["chapters"]:
        meta["chapters"] = chapters_from_text(meta["description"], meta["duration_s"])
    key, kind = _pick_sub(info, lang, platform)
    if key:
        flag = "--write-subs" if kind == "subs_manual" or key.startswith("ai-") else "--write-auto-subs"
        r2 = run(base + cookies + ["--skip-download", flag, "--sub-langs", key, "--convert-subs", "srt",
                                   "-o", str(raw / "sub.%(ext)s"), url], timeout=300)
        files = sorted(raw.glob("sub*.srt"))
        if r2.returncode == 0 and files:
            cues = parse_subtitles(files[0].read_text(encoding="utf-8", errors="replace"))
            segs = cues_to_segments(cues, lang)
            if segs:
                meta["subtitle"] = {"lang": key, "kind": kind}
                return result(meta=meta, kind=kind, segments=segs, lang=lang)
    r3 = run(base + cookies + ["-f", "bestaudio[ext=m4a]/bestaudio/best", "-x", "--audio-format", "m4a",
                               "-o", str(raw / "audio.%(ext)s"), url], timeout=3600)
    audio = raw / "audio.m4a"
    if r3.returncode != 0 or not audio.exists():
        code, msg = _ytdlp_error(r3.stderr)
        return result(code, "没有可用字幕，下载音频也失败：" + msg, meta=meta)
    return result(meta=meta, kind="asr", audio=str(audio), lang=lang)


# ---------- 微信公众号 ----------

def acquire_wechat(url: str, job_dir: Path) -> dict:
    try:
        final, html = http_get(url)
    except Exception as e:  # noqa: BLE001
        return result("FAILED", f"打开公众号文章失败：{e}")
    (job_dir / "raw").mkdir(parents=True, exist_ok=True)
    (job_dir / "raw" / "page.html").write_text(html, encoding="utf-8")
    if "js_content" not in html:
        if "环境异常" in html or "完成验证" in html:
            return result("BLOCKED", "公众号要求验证，请在浏览器打开后复制正文，或稍后重试")
        if "__biz=" in url:
            return result("UNSUPPORTED", "公众号长链接服务器不直接返回正文：请给短链接（mp.weixin.qq.com/s/…）或复制正文")
        return result("NOT_FOUND", "页面里没有正文，文章可能已删除")

    def find(p):
        m = re.search(p, html)
        return m.group(1) if m else None

    title = find(r'property="og:title" content="([^"]*)"') or find(r"var msg_title = '([^']*)'") or ""
    account = find(r'var nickname = htmlDecode\("([^"]*)"\)') or find(r'nick_name:\s*"([^"]*)"') or find(r'<strong class="profile_nickname">([^<]*)<')
    ct = find(r"var createTime = '([^']*)'") or find(r'var ct = "(\d+)"') or find(r"create_time:\s*'(\d+)'")
    root = htmltext.parse(html)
    region = next((n for n in root.iter() if n.attrs.get("id") == "js_content"), None)
    blocks = htmltext.to_blocks(region) if region is not None else []
    sidx = re.search(r"/s/([A-Za-z0-9_-]+)", url)
    meta = {
        "platform": "wechat",
        "title": htmltext.parse(title).text().strip() if title else "",
        "url": url.split("#")[0],
        "stable_id": sidx.group(1) if sidx else None,
        "author": account,
        "source": f"微信公众号 · {account}" if account else "微信公众号",
        "published": ct,
        "published_date": beijing_date(ct),
        "description": find(r'property="og:description" content="([^"]*)"') or "",
    }
    if not blocks:
        return result("NO_TEXT", "正文为空（可能是纯图片或视频文章）", meta=meta)
    return result(meta=meta, kind="article", blocks=blocks, lang=lang_of("\n".join(blocks)))


# ---------- 飞书妙记 ----------

def acquire_feishu_minutes(url: str, job_dir: Path) -> dict:
    m = re.search(r"/minutes/([A-Za-z0-9]+)", url)
    if not m:
        return result("NOT_FOUND", "链接里没有妙记 token")
    token = m.group(1)
    raw = job_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    r = run(["lark-cli", "minutes", "minutes", "get", "--params", json.dumps({"minute_token": token}), "--as", "user"], timeout=120)
    text = (r.stdout or "") + (r.stderr or "")
    auth_err = re.search(r"need_user_authorization|token_missing|token_expired|refresh_token|verify_failed", text)
    if r.returncode != 0:
        if auth_err:
            return result("NEED_LOGIN", "飞书用户授权失效：按 references/lark-auth.md 重新授权")
        return result("FAILED", f"读取妙记信息失败：{text.strip()[:200]}")
    try:
        info = json.loads(r.stdout)
    except ValueError:
        info = {}
    data = info.get("data", info)
    minute = data.get("minute", data)
    r2 = run(["lark-cli", "minutes", "+detail", "--as", "user", "--minute-tokens", token,
              "--transcript", "--chapter", "--output-dir", "./out"], timeout=300, cwd=raw)
    files = sorted((raw / "out").rglob("*")) if (raw / "out").exists() else []
    texts = [f for f in files if f.is_file() and f.suffix.lower() in {".txt", ".md", ".srt", ".json"}]
    if r2.returncode != 0 or not texts:
        msg = ((r2.stdout or "") + (r2.stderr or "")).strip()[:200]
        if re.search(r"need_user_authorization|token_expired|refresh_token", msg):
            return result("NEED_LOGIN", "飞书用户授权失效：按 references/lark-auth.md 重新授权")
        return result("FAILED", f"读取妙记逐字稿失败：{msg}")
    transcript = next((f for f in texts if "transcript" in f.name.lower() or "逐字" in f.name), texts[0])
    lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    segs, pinfo = parse_labeled_lines(lines)
    created = minute.get("create_time")
    meta = {
        "platform": "feishu_minutes",
        "title": minute.get("title") or pinfo.get("title") or "",
        "url": url.split("?")[0],
        "stable_id": token,
        "author": (minute.get("owner") or {}).get("name") if isinstance(minute.get("owner"), dict) else None,
        "source": "飞书妙记",
        "published": created,
        "published_date": beijing_date(created),
        "duration_s": (int(minute.get("duration")) / 1000) if str(minute.get("duration") or "").isdigit() else None,
        "description": "",
        "chapters": [],
        "transcript_file": str(transcript),
    }
    if not segs:
        return result("NO_TEXT", "妙记逐字稿为空或格式无法识别", meta=meta)
    return result(meta=meta, kind="platform_transcript", segments=segs, lang=lang_of(" ".join(s["text"] for s in segs[:50])))


# ---------- 一般网页与 PDF ----------

def acquire_web(url: str, job_dir: Path, opts: dict) -> dict:
    raw = job_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    try:
        final, html = http_get(url)
    except Exception as e:  # noqa: BLE001
        code = "NOT_FOUND" if "404" in str(e) else "BLOCKED" if "403" in str(e) else "FAILED"
        return result(code, f"打开网页失败：{e}")
    (raw / "page.html").write_text(html, encoding="utf-8")
    meta_page, blocks, total = htmltext.extract(html)
    text_len = sum(len(b) for b in blocks)
    if text_len < 300 and meta_page.get("has_media"):
        return acquire_ytdlp(url, "ytdlp", job_dir, opts)
    host = urllib.parse.urlparse(final).netloc
    meta = {
        "platform": "web",
        "title": (meta_page.get("title") or "").strip(),
        "url": meta_page.get("url") or final,
        "stable_id": None,
        "author": meta_page.get("author"),
        "source": " · ".join(x for x in [meta_page.get("site") or host, meta_page.get("author")] if x),
        "published": meta_page.get("published"),
        "published_date": beijing_date(meta_page.get("published")),
        "description": meta_page.get("description") or "",
    }
    if not blocks:
        return result("NO_TEXT", "页面里没有抽到正文（可能要登录、付费或由脚本动态加载）", meta=meta)
    code, msg = "OK", ""
    if total > 3000 and text_len < 0.25 * total:
        full = htmltext.to_blocks(htmltext.parse(html))
        (raw / "page_blocks.md").write_text("\n\n".join(full), encoding="utf-8")
        code, msg = "PARTIAL", "正文可能没抽全：看 source.md 的段号，用 prep.py select 指定起止段"
        blocks = full
    return result(code, msg, meta=meta, kind="article", blocks=blocks, lang=lang_of("\n".join(blocks)))


def acquire_pdf(path_or_url: str, job_dir: Path) -> dict:
    raw = job_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    src = Path(path_or_url).expanduser()
    meta = {"platform": "pdf", "url": None, "stable_id": None}
    if not src.exists():
        pdf = raw / "doc.pdf"
        if not download(path_or_url, pdf):
            return result("FAILED", "PDF 下载失败")
        src = pdf
        meta["url"] = path_or_url
    paras, info = pdf_text(src)
    meta.update({
        "title": info.get("Title") or Path(urllib.parse.urlparse(path_or_url).path).stem,
        "author": info.get("Author"),
        "source": "PDF" + (f" · {info.get('Author')}" if info.get("Author") else ""),
        "published": info.get("CreationDate"),
        "published_date": beijing_date(info.get("CreationDate")),
        "pages": info.get("Pages"),
    })
    if info.get("error"):
        return result("FAILED", f"pdftotext 失败：{info['error']}", meta=meta)
    if info.get("scanned"):
        return result("NEEDS_VISION", "扫描件（几乎没有文字层），按图片处理", meta=meta, kind="images")
    return result(meta=meta, kind="document", blocks=paras, lang=lang_of("\n".join(paras[:200])))


# ---------- 本地文件 ----------

def acquire_local(path: str, typ: str, job_dir: Path) -> dict:
    p = Path(path).expanduser()
    meta = {"platform": "local", "title": p.stem, "url": None, "stable_id": None, "local_path": str(p), "source": None}
    if typ == "dir":
        imgs = sorted(x for x in p.iterdir() if x.suffix.lower() in IMAGE_EXT)
        if imgs:
            meta["images"] = [str(x) for x in imgs]
            return result("NEEDS_VISION", f"{len(imgs)} 张图片，需要看图", meta=meta, kind="images")
        return result("UNSUPPORTED", "目录里没有可处理的图片")
    if typ == "image":
        meta["images"] = [str(p)]
        return result("NEEDS_VISION", "图片需要看图", meta=meta, kind="images")
    if typ == "audio":
        r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags=title,artist,date,creation_time",
                 "-of", "json", str(p)], timeout=60)
        try:
            fmt = json.loads(r.stdout).get("format", {})
        except ValueError:
            fmt = {}
        tags = fmt.get("tags") or {}
        meta.update({"duration_s": float(fmt.get("duration") or 0) or None, "author": tags.get("artist"),
                     "published": tags.get("date") or tags.get("creation_time"),
                     "published_date": beijing_date(tags.get("date") or tags.get("creation_time")) if tags.get("date") else None})
        if tags.get("title"):
            meta["title"] = tags["title"]
        return result(meta=meta, kind="asr", audio=str(p), lang=None)
    if typ == "pdf":
        res = acquire_pdf(str(p), job_dir)
        res["meta"].update({"platform": "local", "local_path": str(p), "title": res["meta"].get("title") or p.stem})
        return res
    if typ in {"srt", "vtt"}:
        text = p.read_text(encoding="utf-8", errors="replace")
        lang = lang_of(text)
        return result(meta=meta, kind="subs_manual", segments=cues_to_segments(parse_subtitles(text), lang), lang=lang)
    if typ == "pptx":
        slides = pptx_slides(p)
        blocks = []
        for i, s in enumerate(slides, 1):
            blocks.append(f"### 第 {i} 页")
            blocks.extend(x for x in s.split("\n") if x)
        return result(meta=meta, kind="document", blocks=blocks, lang=lang_of("\n".join(slides)))
    if typ in {"html", "htm"}:
        m, blocks, _ = htmltext.extract(p.read_text(encoding="utf-8", errors="replace"))
        meta.update({k: v for k, v in m.items() if v and k in {"title", "author", "published", "description"}})
        meta["published_date"] = beijing_date(meta.get("published"))
        return result(meta=meta, kind="article", blocks=blocks, lang=lang_of("\n".join(blocks)))
    if typ == "docx":
        paras = docx_paragraphs(p)
    else:
        paras = p.read_text(encoding="utf-8", errors="replace").splitlines()
    segs, info = parse_labeled_lines(paras)
    if info["matched"] >= 0.5 and len(segs) >= 3:
        if info.get("title"):
            meta["title"] = info["title"]
        return result(meta=meta, kind="platform_transcript", segments=segs, lang=lang_of(" ".join(s["text"] for s in segs[:80])))
    text = "\n".join(paras)
    blocks = plain_paragraphs(text) if typ != "docx" else [x.strip() for x in paras if x.strip()]
    return result(meta=meta, kind="document", blocks=blocks, lang=lang_of(text))


def acquire(target: str, job_dir: Path, opts: dict) -> dict:
    d = detect(target)
    plat = d["platform"]
    if d["kind"] == "file":
        res = acquire_local(d["path"], d["type"], job_dir)
    elif plat == "xiaoyuzhou":
        res = acquire_xiaoyuzhou(d["url"], job_dir)
    elif plat == "apple":
        res = acquire_apple(d["url"], job_dir)
    elif plat == "wechat":
        res = acquire_wechat(d["url"], job_dir)
    elif plat == "feishu_minutes":
        res = acquire_feishu_minutes(d["url"], job_dir)
    elif plat == "shipinhao":
        res = result("UNSUPPORTED", "视频号没有公开抓取途径：请用户导出视频文件后给文件路径")
    elif plat == "pdf":
        res = acquire_pdf(d["url"], job_dir)
    elif plat == "web":
        res = acquire_web(d["url"], job_dir, opts)
    else:
        res = acquire_ytdlp(d["url"], plat, job_dir, opts)
    res["meta"].setdefault("platform", plat)
    res["meta"]["input"] = target
    return res
