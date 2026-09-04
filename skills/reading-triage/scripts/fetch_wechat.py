#!/usr/bin/env python3
"""批量抓取微信公众号文章：元数据 + 正文。

用法:
  python3 fetch_wechat.py urls.txt --out txt/            # 抓正文
  python3 fetch_wechat.py urls.txt --meta                # 只要元数据(TSV)

注意: 只有 mp.weixin.qq.com/s/<hash> 短链能抓到内容。
      /s?__biz=...&mid=...&idx=...&sn=... 长链服务端不直出(换 UA 也没用),
      但在浏览器里正常 —— 遇到长链请原样交付给用户,不要声称"链接无效"。
"""
import re, sys, html, subprocess, os, argparse, threading, concurrent.futures as cf

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122 Safari/537.36")

def get(url, timeout=30):
    return subprocess.run(["curl", "-sL", "--max-time", str(timeout), "-A", UA, url],
                          capture_output=True, text=True).stdout

def meta(h):
    f = lambda p: (re.findall(p, h) or [None])[0]
    return {"time":  f(r"createTime = '([^']*)'"),
            "title": f(r'property="og:title" content="([^"]*)"'),
            "acct":  f(r'var nickname = htmlDecode\("([^"]*)"\)')}

def body(h):
    m = (re.search(r'<div class="rich_media_content[^>]*id="js_content"[^>]*>(.*?)</div>\s*</div>\s*<script', h, re.S)
         or re.search(r'id="js_content"[^>]*>(.*)', h, re.S))
    b = m.group(1) if m else h
    b = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', b, flags=re.S)
    b = re.sub(r'<br\s*/?>|</p>|</h\d>|</li>|</section>', '\n', b, flags=re.I)
    b = html.unescape(re.sub(r'<[^>]+>', '', b))
    return re.sub(r'\n\s*\n+', '\n', re.sub(r'[ \t\xa0]+', ' ', b)).strip()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urlfile"); ap.add_argument("--out", default=None)
    ap.add_argument("--meta", action="store_true"); ap.add_argument("-j", type=int, default=5)
    a = ap.parse_args()
    urls = [l.strip() for l in open(a.urlfile) if l.strip().startswith("http")]
    if a.out: os.makedirs(a.out, exist_ok=True)

    lock = threading.Lock()

    def one(u):
        h = get(u); m = meta(h); b = body(h) if a.out else ""
        if a.out and m["title"]:
            # 不同文章可能同标题(转载、系列、截断后同名)——加序号,绝不静默覆盖。
            # 建目录/查存在/写入必须在同一把锁里,否则并发下仍会撞。
            n = re.sub(r'[\\/:*?"<>|\s]+', '_', m["title"])[:50] or "untitled"
            with lock:
                p = os.path.join(a.out, n + ".txt"); i = 2
                while os.path.exists(p):
                    p = os.path.join(a.out, f"{n}_{i}.txt"); i += 1
                open(p, "w", encoding="utf-8").write(b)
        return u, m, len(b)

    ok = 0
    with cf.ThreadPoolExecutor(a.j) as ex:
        for u, m, n in ex.map(one, urls):
            if m["title"]: ok += 1
            print(f"{u}\t{m['time'] or 'NA'}\t{m['acct'] or 'NA'}\t{m['title'] or '抓取失败'}\t{n}")
    print(f"\n# {ok}/{len(urls)} 成功", file=sys.stderr)

if __name__ == "__main__": main()
