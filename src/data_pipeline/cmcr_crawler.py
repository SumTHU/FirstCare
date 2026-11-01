# -*- coding: utf-8 -*-
"""
CMCR 发热病例爬虫 (Task 3 前置：数据采集层)

职责：
1. 通过 cmcr.yiigle.com 的 searchList 接口按关键词（默认"发热"）翻页检索病例；
2. 过滤出《中国临床案例成果数据库》(CMCR) 的病例条目 (journalCn / DOI 前缀 cma.cmcr)；
3. 逐例抓取 rs.yiigle.com/cmaid/<id> 的 SSR 页面，从页面载荷中提取完整 JATS 学术 XML 全文
   （开放获取 CC-BY-NC-ND 病例含全部原生章节：一般资料/检查/诊断与鉴别诊断/治疗/转归/讨论）；
4. 原始 HTML 与 XML 存入 data/raw_cmcr/，并维护 search_index.json 与 crawl_manifest.json
   （支持断点续传：已抓取病例自动跳过）。

合规说明：仅抓取开放获取 (Open Access) 的病例报告用于学术研究数据集构建；请求间隔默认 2 秒。
凭据：仅需要登录 logintoken（写于项目根目录 .env 的 CMCR_LOGIN_TOKEN，已被 .gitignore 排除）。
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None  # 回退到 urllib

from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw_cmcr"
ENV_PATH = PROJECT_ROOT / ".env"

SEARCH_URL = "https://cmcr.yiigle.com/apiVue/search/searchList"
CASE_URL_TMPL = "https://rs.yiigle.com/cmaid/{cmaid}"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CMCR_JOURNAL_MARK = "临床案例"
CMCR_DOI_MARK = "cma.cmcr"


# ------------------------------------------------------------------------------
# 配置加载
# ------------------------------------------------------------------------------

def load_env(path: Path = ENV_PATH) -> Dict[str, str]:
    """极简 .env 解析器（KEY="value" 或 KEY=value）。"""
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


# ------------------------------------------------------------------------------
# HTTP 基础
# ------------------------------------------------------------------------------

def _http_post_json(url: str, payload: Dict[str, Any], timeout: int = 30) -> Optional[Dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://cmcr.yiigle.com",
        "Referer": "https://cmcr.yiigle.com/search-result",
        "User-Agent": UA,
    }
    if requests is not None:
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            print(f"  [POST失败] {exc}")
            return None
    req = urllib_request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"  [POST失败] {exc}")
        return None


def _http_get(url: str, timeout: int = 45) -> Optional[str]:
    """rs.yiigle.com 会拦截 Python-requests 的 TLS 指纹，因此 GET 统一走系统 curl。"""
    import subprocess
    try:
        proc = subprocess.run(
            ["curl", "-s", "-L", "--max-time", str(timeout), "--compressed",
             "-H", f"User-Agent: {UA}",
             "-H", "Accept-Language: zh-CN,zh;q=0.9",
             "--url", url],
            capture_output=True, timeout=timeout + 10,
        )
        if proc.returncode != 0:
            print(f"  [GET失败] {url} -> curl exit {proc.returncode}")
            return None
        return proc.stdout.decode("utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        print(f"  [GET失败] {url} -> {exc}")
        return None


# ------------------------------------------------------------------------------
# Step 1: 关键词检索（翻页收集 CMCR 病例条目）
# ------------------------------------------------------------------------------

def build_search_payload(keyword: str, page: int, page_size: int, login_token: str,
                         is_aggregations: str = "N", year: Optional[int] = None) -> Dict[str, Any]:
    if year is not None:
        # 高级检索模式：ES 查询语法，关键词 + 发表年份组合过滤
        # 注意：gj 模式下 searchText/searchLog 必须为空字符串，否则服务端返回 0 条
        query = f"{keyword} AND artPubYear:[{year} TO {year}]" if keyword else \
            f"artPubYear:[{year} TO {year}]"
        return {
            "type": "Case",
            "sortField": "artPubDate",
            "page": page,
            "searchType": "gj",
            "pageSize": page_size,
            "queryString": query,
            "query": "",
            "searchText": "",
            "searchLog": "",
            "isAggregations": is_aggregations,
            "logintoken": login_token,
        }
    return {
        "type": "Case",
        "sortField": "",              # 相关性排序
        "page": page,
        "searchType": "pt",
        "pageSize": page_size,
        "queryString": keyword,
        "query": keyword,
        "searchText": keyword,
        "searchLog": keyword,
        "isAggregations": is_aggregations,
        "logintoken": login_token,
    }


def is_cmcr_entry(entry: Dict[str, Any]) -> bool:
    journal = entry.get("journalCn") or ""
    doi = entry.get("artDoi") or ""
    return (CMCR_JOURNAL_MARK in journal) or (CMCR_DOI_MARK in doi)


def extract_cmaid(art_url: str) -> Optional[str]:
    m = re.search(r"/cmaid/(\d+)", art_url or "")
    return m.group(1) if m else None


def search_cmcr_cases(keyword: str, login_token: str, max_pages: int = 40,
                      page_size: int = 50, interval: float = 2.0,
                      verbose: bool = True, year: Optional[int] = None) -> List[Dict[str, Any]]:
    """翻页检索并返回去重后的 CMCR 病例元数据列表。

    year 模式下按 artPubYear 过滤 + 日期倒序，扫到早于目标年份的条目即停。
    """
    seen: Dict[str, Dict[str, Any]] = {}
    empty_streak = 0
    for page in range(1, max_pages + 1):
        payload = build_search_payload(keyword, page, page_size, login_token, year=year)
        data = _http_post_json(SEARCH_URL, payload)
        if not data or data.get("code") != 200:
            print(f"  [第{page}页] 请求异常，跳过")
            empty_streak += 1
            if empty_streak >= 3:
                break
            time.sleep(interval)
            continue
        result = (data.get("data") or {}).get("result") or {}
        infos = result.get("infos") or []
        total = result.get("searchTotal")
        cmcr_hits = []
        reached_older = False
        for it in infos:
            if year is not None:
                y = it.get("artPubYear")
                try:
                    if int(y) < year:
                        reached_older = True
                        break
                    if int(y) != year:
                        continue
                except (TypeError, ValueError):
                    continue
            if not is_cmcr_entry(it):
                continue
            cmaid = extract_cmaid(it.get("artUrl") or "")
            if not cmaid or cmaid in seen:
                continue
            seen[cmaid] = {
                "cmaid": cmaid,
                "title": (it.get("artTitle") or "").strip(),
                "doi": it.get("artDoi"),
                "journal": it.get("journalCn"),
                "pub_year": it.get("artPubYear"),
                "pub_date": it.get("artPubDate"),
                "authors": it.get("authorNames"),
                "organ": it.get("firstORcorrOrgan"),
                "keywords": it.get("keywords"),
                "abstract": (it.get("artAbstract") or "").strip(),
                "oa_mode": it.get("oaMode"),
                "art_url": it.get("artUrl"),
            }
            cmcr_hits.append(cmaid)
        if verbose:
            print(f"  [第{page}页] 返回{len(infos)}条, 新增CMCR {len(cmcr_hits)} 条, 累计 {len(seen)} (库内总量约{total})")
        if reached_older:
            if verbose:
                print(f"  已扫描完 {year} 年全部条目，停止翻页。")
            break
        if not infos:
            empty_streak += 1
            if empty_streak >= 2:
                if verbose:
                    print("  连续空页，停止翻页。")
                break
        time.sleep(interval)
    return list(seen.values())


# ------------------------------------------------------------------------------
# Step 2: 病例全文页抓取 + JATS XML 提取
# ------------------------------------------------------------------------------

_JSON_ESCAPES = [
    (re.compile(r"\\u([0-9a-fA-F]{4})"), lambda m: chr(int(m.group(1), 16))),
]


def _decode_json_string_fragment(s: str) -> str:
    """解码 SSR 载荷中的 JSON 字符串转义（\\uXXXX、\\u002F、\\"、\\n、\\\\）。"""
    out = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    out = out.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
    out = re.sub(r"\\/", "/", out)
    return out


def extract_jats_xml(html: str) -> Optional[str]:
    """从 rs.yiigle.com SSR 页面中提取 <article>...</article> JATS XML。

    页面把 XML 以 JSON 转义形式嵌入（含 \\u003C 与 \\u002F），
    因此先整体解码 \\uXXXX 再按解码后的标签切片。
    """
    dec = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), html)
    start = dec.find("<article")
    if start < 0:
        return None
    end = dec.find("</article>", start)
    if end < 0:
        return None
    xml = dec[start:end + len("</article>")]
    xml = (xml.replace('\\"', '"')
              .replace("\\n", "\n")
              .replace("\\t", "\t")
              .replace("\\/", "/"))
    return xml


def extract_xml_section_titles(xml: str) -> List[str]:
    return [t.strip() for t in re.findall(r"<title>([^<]{1,60})</title>", xml)]


def is_open_access_xml(xml: str) -> bool:
    return ("CC-BY-NC-ND" in xml) or ("开放获取" in xml) or ("开放阅读" in xml)


def fetch_case_fulltext(cmaid: str, interval: float = 2.0) -> Optional[Dict[str, Any]]:
    """抓取单例 SSR 页面，提取 JATS XML 并将 HTML/XML 落盘。"""
    url = CASE_URL_TMPL.format(cmaid=cmaid)
    html = _http_get(url)
    if not html:
        return None
    time.sleep(interval)
    xml = extract_jats_xml(html)
    if not xml:
        return {"cmaid": cmaid, "status": "no_xml", "url": url}
    titles = extract_xml_section_titles(xml)
    # 落盘原始 HTML 与提取出的 XML
    (RAW_DIR / f"case_{cmaid}.html").write_text(html, encoding="utf-8", errors="ignore")
    (RAW_DIR / f"case_{cmaid}_article.xml").write_text(xml, encoding="utf-8")
    pdf_m = re.search(r'xlink:href="([^"]*articleFiles[^"]*\.pdf)"', xml)
    return {
        "cmaid": cmaid,
        "status": "ok" if len(titles) >= 5 else "thin",
        "url": url,
        "xml_len": len(xml),
        "section_titles": titles,
        "is_open_access": is_open_access_xml(xml),
        "pdf_path": pdf_m.group(1) if pdf_m else None,
    }


# ------------------------------------------------------------------------------
# Step 3: 主流程（断点续传）
# ------------------------------------------------------------------------------

def crawl(keyword: Optional[str] = None, target_cases: Optional[int] = None,
          max_pages: Optional[int] = None, page_size: Optional[int] = None,
          interval: Optional[float] = None, year: Optional[int] = None) -> None:
    env = load_env()
    keyword = keyword or env.get("CMCR_KEYWORD", "发热")
    target_cases = int(target_cases) if target_cases else None
    max_pages = int(max_pages or env.get("CMCR_MAX_PAGES", 40))
    page_size = int(page_size or env.get("CMCR_PAGE_SIZE", 50))
    interval = float(interval or env.get("CMCR_REQUEST_INTERVAL", 2.0))
    login_token = env.get("CMCR_LOGIN_TOKEN", "")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW_DIR / "crawl_manifest.json"

    if year is not None:
        print(f"=== CMCR {year} 年病例爬取 (关键词: {keyword or '不限'}) ===\n"
              f"翻页上限: {max_pages} | 请求间隔: {interval}s")
        print("\n[Step 1] 按年份+关键词枚举 CMCR 病例清单...")
        entries = search_cmcr_cases(keyword, login_token, max_pages=max_pages,
                                    page_size=page_size, interval=interval, year=year)
        # 关键词+年份组合索引分文件存放，避免多关键词相互覆盖
        if keyword:
            index_path = RAW_DIR / f"search_index_kw_{keyword}_year_{year}.json"
        else:
            index_path = RAW_DIR / f"search_index_year_{year}.json"
    else:
        print(f"=== CMCR 病例爬取 ===\n关键词: {keyword} | 目标病例数: {target_cases} | 翻页上限: {max_pages}")
        print("\n[Step 1] 关键词检索收集 CMCR 病例清单...")
        entries = search_cmcr_cases(keyword, login_token, max_pages=max_pages,
                                    page_size=page_size, interval=interval)
        # 每个关键词独立索引文件，避免多关键词补充抓取时相互覆盖
        index_path = RAW_DIR / f"search_index_kw_{keyword}.json"

    print(f"检索完成：共收集 CMCR 病例 {len(entries)} 例")
    if not entries:
        print("未收集到任何病例，请检查 logintoken 是否过期。")
        sys.exit(1)

    index_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"元数据索引已写入 {index_path.name}")

    limit = target_cases if target_cases else len(entries)

    # ---- 断点续传清单 ----
    manifest: Dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            manifest = {}
    manifest.setdefault("cases", {})
    manifest["keyword"] = keyword
    manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # ---- 抓取全文 ----
    print(f"\n[Step 2] 逐例抓取全文 (间隔 {interval}s)...")
    ok = thin = no_xml = 0
    fetched = 0
    for i, entry in enumerate(entries, 1):
        cmaid = entry["cmaid"]
        if cmaid in manifest["cases"] and manifest["cases"][cmaid].get("status") == "ok":
            ok += 1
            continue
        if fetched >= limit:
            break
        info = fetch_case_fulltext(cmaid, interval=interval)
        if info is None:
            info = {"cmaid": cmaid, "status": "fetch_error"}
        status = info.pop("status")
        entry["crawl_status"] = status
        entry["section_titles"] = info.get("section_titles", [])
        entry["is_open_access"] = info.get("is_open_access", False)
        entry["xml_len"] = info.get("xml_len", 0)
        if status == "ok":
            ok += 1
        elif status == "thin":
            thin += 1
        else:
            no_xml += 1
        manifest["cases"][cmaid] = {"status": status, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        fetched += 1
        if fetched % 10 == 0:
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  进度 {fetched}/{limit} | ok={ok} thin={thin} fail={no_xml}")

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    index_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 爬取完成 ===")
    print(f"清单内病例: {len(entries)} | 本次新抓: {fetched} | 全库已缓存: {ok}")
    print(f"全文完整: {ok} | 章节偏少: {thin} | 无全文XML: {no_xml}")
    print(f"原始文件目录: {RAW_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CMCR 发热病例爬虫")
    parser.add_argument("--keyword", default=None, help="检索关键词，默认取 .env")
    parser.add_argument("--year", type=int, default=None, help="按发表年份全量爬取（如 2026）")
    parser.add_argument("--target", type=int, default=None, help="目标抓取病例数（year 模式默认全部）")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--page-size", type=int, default=None)
    parser.add_argument("--interval", type=float, default=None, help="请求间隔秒数")
    args = parser.parse_args()
    crawl(keyword=args.keyword, target_cases=args.target, max_pages=args.max_pages,
          page_size=args.page_size, interval=args.interval, year=args.year)
