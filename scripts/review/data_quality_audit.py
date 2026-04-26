"""Scan the live `article` table for data-quality issues.

Categories scanned:
  A. Duplicates (URL / title / content / URL-without-querystring)
  B. Title quality  (== blog name, source-name leak, too short, HTML, placeholder, ...)
  C. Content quality (empty, too short, length mismatch, login-wall)
  D. URL quality    (login/paywall, listing/home, host mismatch with blog)
  E. Metadata       (published_at sanity, lang, thumbnail)
  F. Cross-table    (orphan blog_id, also-in-rejected)

Output:
  Markdown report (default stdout, --output to file)
  --format json for machine consumption (bite-monitor)

Usage (from harvest_post/):
    doppler run -- uv run python scripts/review/data_quality_audit.py \\
        --samples 10 --output docs/data_quality_audit_$(date +%Y%m%d).md

    # Quick sanity on two categories only
    doppler run -- uv run python scripts/review/data_quality_audit.py \\
        --categories A1,B2 --samples 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db_conn import Connection  # noqa: E402


def _q(conn: Connection, sql: str):
    """Run a SELECT, escaping `%` so pymysql doesn't interpret it as a format char.

    All queries in this script are static (no params); doubling `%` is safe.
    """
    return conn.execute(sql.replace("%", "%%"))


# Source/platform names that, if found alone in title, indicate a fallback leak.
# Lowercased; matched against TRIM(LOWER(title)).
SOURCE_TOKENS = [
    "clova", "linkedin", "medium", "github", "naver", "kakao",
    "brunch", "tistory", "velog", "qiita", "dev.to", "substack",
    "youtube", "twitter", "x", "facebook", "instagram", "rss",
    "feed", "blog", "home", "main",
]

# Login/paywall content markers (lowercased substring match on first 2KB of content)
LOGIN_WALL_MARKERS = [
    "please log in", "sign up to read", "this page isn't available",
    "403 forbidden", "access denied", "robot check", "just a moment",
    "log in to continue", "sign in to continue", "members only",
]

# URL path suffixes that are listing/home pages (not articles)
LISTING_PATH_SUFFIXES = ["/", "/posts/", "/blog/", "/articles/", "/index", "/main"]

# URL substrings indicating login/paywall page
LOGIN_URL_PATTERNS = ["/login", "/signin", "/m/signin", "/auth/", "/subscribe"]

# Title placeholder patterns (case-insensitive regex via REGEXP)
TITLE_PLACEHOLDER_REGEX = (
    r"^[[:space:]]*(untitled|page not found|404|access denied|loading|"
    r"sign in|log in|robot check|just a moment|forbidden|error)[.[:space:]]*$"
)


@dataclass
class CategoryResult:
    code: str
    name: str
    count: int
    samples: list[dict] = field(default_factory=list)
    note: str = ""


def _samples_query(where_or_join: str, sample_n: int, extra_select: str = "") -> str:
    cols = "a.article_id, a.blog_id, a.url, a.title, a.created_at"
    if extra_select:
        cols += ", " + extra_select
    return f"SELECT {cols} FROM article a {where_or_join} ORDER BY a.created_at DESC LIMIT {sample_n}"


def scan_a1_dup_url(conn: Connection, samples: int) -> CategoryResult:
    df = _q(conn, 
        "SELECT url, COUNT(*) AS n FROM article "
        "WHERE url IS NOT NULL AND url <> '' "
        "GROUP BY url HAVING n > 1"
    )
    total_extra = int((df["n"] - 1).sum()) if not df.empty else 0
    note = f"{len(df)} unique URLs duplicated; {total_extra} rows are extras"
    sample_rows = []
    if not df.empty:
        urls = df["url"].head(samples).tolist()
        urls_sql = ",".join(["'" + u.replace("'", "''") + "'" for u in urls])
        sdf = _q(conn, 
            f"SELECT a.article_id, a.blog_id, a.url, a.title, a.created_at "
            f"FROM article a WHERE a.url IN ({urls_sql}) "
            f"ORDER BY a.url, a.created_at"
        )
        sample_rows = sdf.to_dict(orient="records")
    return CategoryResult("A1", "URL 중복 (정규화 누락)", total_extra, sample_rows, note)


def scan_a2_dup_title(conn: Connection, samples: int) -> CategoryResult:
    df = _q(conn, 
        "SELECT title, COUNT(*) AS n FROM article "
        "WHERE title IS NOT NULL AND CHAR_LENGTH(title) >= 8 "
        "GROUP BY title HAVING n > 1"
    )
    total_extra = int((df["n"] - 1).sum()) if not df.empty else 0
    note = f"{len(df)} unique titles duplicated; {total_extra} extras (≥8자 title 만)"
    sample_rows = []
    if not df.empty:
        titles = df["title"].head(samples).tolist()
        titles_sql = ",".join(["'" + t.replace("'", "''") + "'" for t in titles])
        sdf = _q(conn, 
            f"SELECT a.article_id, a.blog_id, a.url, a.title, a.created_at "
            f"FROM article a WHERE a.title IN ({titles_sql}) "
            f"ORDER BY a.title, a.created_at"
        )
        sample_rows = sdf.to_dict(orient="records")
    return CategoryResult("A2", "Title 중복 (8자 이상)", total_extra, sample_rows, note)


def scan_a3_dup_content(conn: Connection, samples: int) -> CategoryResult:
    df = _q(conn, 
        "SELECT content_md5, COUNT(*) AS n FROM ("
        "  SELECT MD5(content) AS content_md5 FROM article "
        "  WHERE content IS NOT NULL AND content_length >= 500"
        ") t GROUP BY content_md5 HAVING n > 1"
    )
    total_extra = int((df["n"] - 1).sum()) if not df.empty else 0
    note = f"{len(df)} unique content hashes duplicated; {total_extra} extras (500자 이상 본문만)"
    sample_rows = []
    if not df.empty:
        hashes = df["content_md5"].head(samples).tolist()
        hashes_sql = ",".join([f"'{h}'" for h in hashes])
        sdf = _q(conn, 
            f"SELECT a.article_id, a.blog_id, a.url, a.title, a.created_at, "
            f"MD5(a.content) AS content_md5 "
            f"FROM article a WHERE MD5(a.content) IN ({hashes_sql}) "
            f"ORDER BY content_md5, a.created_at"
        )
        sample_rows = sdf.to_dict(orient="records")
    return CategoryResult("A3", "Content 중복 (MD5 일치)", total_extra, sample_rows, note)


def scan_a4_dup_url_no_qs(conn: Connection, samples: int) -> CategoryResult:
    """Same URL except for querystring — LinkedIn 의 ?lipi=, utm_*, source= 등."""
    df = _q(conn, 
        "SELECT url_base, COUNT(*) AS n FROM ("
        "  SELECT SUBSTRING_INDEX(url, '?', 1) AS url_base FROM article "
        "  WHERE url IS NOT NULL AND url LIKE '%?%'"
        ") t GROUP BY url_base HAVING n > 1"
    )
    total_extra = int((df["n"] - 1).sum()) if not df.empty else 0
    note = f"{len(df)} unique base URLs with querystring duplicates; {total_extra} extras"
    sample_rows = []
    if not df.empty:
        bases = df["url_base"].head(samples).tolist()
        bases_sql = ",".join(["'" + b.replace("'", "''") + "'" for b in bases])
        sdf = _q(conn, 
            f"SELECT a.article_id, a.blog_id, a.url, a.title, a.created_at "
            f"FROM article a WHERE SUBSTRING_INDEX(a.url, '?', 1) IN ({bases_sql}) "
            f"ORDER BY SUBSTRING_INDEX(a.url, '?', 1), a.created_at"
        )
        sample_rows = sdf.to_dict(orient="records")
    return CategoryResult("A4", "URL base 중복 (querystring 만 다름)", total_extra, sample_rows, note)


def scan_b1_title_eq_blog(conn: Connection, samples: int) -> CategoryResult:
    where = (
        "JOIN blog b ON a.blog_id = b.blog_id "
        "WHERE a.title IS NOT NULL AND b.title IS NOT NULL "
        "AND LOWER(TRIM(a.title)) = LOWER(TRIM(b.title))"
    )
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples, "b.title AS blog_title"))
    return CategoryResult("B1", "Title == blog 이름", cnt, samp.to_dict(orient="records"))


def scan_b2_title_source_token(conn: Connection, samples: int) -> CategoryResult:
    tokens_sql = ",".join([f"'{t}'" for t in SOURCE_TOKENS])
    where = (
        f"WHERE a.title IS NOT NULL AND LOWER(TRIM(a.title)) IN ({tokens_sql})"
    )
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("B2", "Title 이 source/플랫폼 토큰 (clova 등)", cnt, samp.to_dict(orient="records"))


def scan_b3_title_too_short(conn: Connection, samples: int) -> CategoryResult:
    where_5 = "WHERE a.title IS NOT NULL AND CHAR_LENGTH(TRIM(a.title)) <= 5"
    where_10 = "WHERE a.title IS NOT NULL AND CHAR_LENGTH(TRIM(a.title)) BETWEEN 6 AND 10"
    cnt5 = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where_5}")["n"].iloc[0])
    cnt10 = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where_10}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where_5, samples))
    note = f"≤5자: {cnt5}건 / 6-10자: {cnt10}건. 샘플은 ≤5자만."
    return CategoryResult("B3", "Title 너무 짧음", cnt5 + cnt10, samp.to_dict(orient="records"), note)


def scan_b4_title_markup(conn: Connection, samples: int) -> CategoryResult:
    where = (
        "WHERE a.title IS NOT NULL AND ("
        "a.title LIKE '%<%' OR a.title LIKE '%&amp;%' OR a.title LIKE '%&#%' "
        "OR a.title LIKE '%{{%' OR a.title LIKE '%</%')"
    )
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("B4", "Title 에 HTML/마크업 누설", cnt, samp.to_dict(orient="records"))


def scan_b5_title_placeholder(conn: Connection, samples: int) -> CategoryResult:
    where = f"WHERE a.title IS NOT NULL AND LOWER(TRIM(a.title)) REGEXP '{TITLE_PLACEHOLDER_REGEX}'"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("B5", "Title 이 placeholder (untitled, 404 등)", cnt, samp.to_dict(orient="records"))


def scan_b6_title_url_like(conn: Connection, samples: int) -> CategoryResult:
    where = "WHERE a.title IS NOT NULL AND (a.title LIKE 'http%' OR a.title LIKE 'www.%')"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("B6", "Title 이 URL", cnt, samp.to_dict(orient="records"))


def scan_b7_title_eq_description(conn: Connection, samples: int) -> CategoryResult:
    where = (
        "WHERE a.title IS NOT NULL AND a.description IS NOT NULL "
        "AND CHAR_LENGTH(a.title) > 5 "
        "AND TRIM(a.title) = TRIM(a.description)"
    )
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("B7", "Title == description", cnt, samp.to_dict(orient="records"))


def scan_c1_content_empty(conn: Connection, samples: int) -> CategoryResult:
    where = "WHERE a.content IS NULL OR TRIM(a.content) = ''"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("C1", "Content NULL / 공백", cnt, samp.to_dict(orient="records"))


def scan_c2_content_short(conn: Connection, samples: int) -> CategoryResult:
    where = "WHERE a.content_length IS NOT NULL AND a.content_length < 500"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples, "a.content_length"))
    return CategoryResult("C2", "Content < 500자", cnt, samp.to_dict(orient="records"))


def scan_c3_length_mismatch(conn: Connection, samples: int) -> CategoryResult:
    where = (
        "WHERE a.content IS NOT NULL AND a.content_length IS NOT NULL "
        "AND CHAR_LENGTH(a.content) <> a.content_length"
    )
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(
        where, samples, "a.content_length, CHAR_LENGTH(a.content) AS actual_len"
    ))
    return CategoryResult("C3", "content_length 정합성 깨짐", cnt, samp.to_dict(orient="records"))


def scan_c4_login_wall(conn: Connection, samples: int) -> CategoryResult:
    likes = " OR ".join(
        f"LOWER(LEFT(a.content, 2000)) LIKE '%{m.replace(chr(39), chr(39) * 2)}%'"
        for m in LOGIN_WALL_MARKERS
    )
    where = f"WHERE a.content IS NOT NULL AND ({likes})"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("C4", "Content 가 로그인/에러 wall", cnt, samp.to_dict(orient="records"))


def scan_d1_login_url(conn: Connection, samples: int) -> CategoryResult:
    likes = " OR ".join([f"a.url LIKE '%{p}%'" for p in LOGIN_URL_PATTERNS])
    where = f"WHERE a.url IS NOT NULL AND ({likes})"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("D1", "Login/paywall URL", cnt, samp.to_dict(orient="records"))


def scan_d2_listing_url(conn: Connection, samples: int) -> CategoryResult:
    likes = " OR ".join([f"SUBSTRING_INDEX(a.url, '?', 1) LIKE '%{s}'" for s in LISTING_PATH_SUFFIXES])
    where = f"WHERE a.url IS NOT NULL AND ({likes})"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("D2", "홈/리스팅 URL (콘텐츠 페이지 아님)", cnt, samp.to_dict(orient="records"))


def scan_d3_url_host_mismatch(conn: Connection, samples: int) -> CategoryResult:
    """blog.url 의 host 와 article.url 의 host 가 다른 경우.

    host 추출은 단순 substring 으로 — 'https://' 이후 첫 '/' 전까지.
    """
    where = (
        "JOIN blog b ON a.blog_id = b.blog_id "
        "WHERE a.url IS NOT NULL AND b.url IS NOT NULL "
        "AND SUBSTRING_INDEX(SUBSTRING_INDEX(a.url, '://', -1), '/', 1) "
        "  <> SUBSTRING_INDEX(SUBSTRING_INDEX(b.url, '://', -1), '/', 1)"
    )
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples, "b.url AS blog_url"))
    return CategoryResult("D3", "URL host 가 blog host 와 불일치", cnt, samp.to_dict(orient="records"))


def scan_e1_published_future(conn: Connection, samples: int) -> CategoryResult:
    where = "WHERE a.published_at > DATE_ADD(NOW(), INTERVAL 1 DAY)"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples, "a.published_at"))
    return CategoryResult("E1", "published_at 이 미래", cnt, samp.to_dict(orient="records"))


def scan_e2_published_ancient(conn: Connection, samples: int) -> CategoryResult:
    where = "WHERE a.published_at IS NOT NULL AND a.published_at < '2000-01-01'"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples, "a.published_at"))
    return CategoryResult("E2", "published_at 이 2000년 이전", cnt, samp.to_dict(orient="records"))


def scan_e3_lang(conn: Connection, samples: int) -> CategoryResult:
    where = "WHERE a.lang IS NULL OR a.lang NOT IN ('ko', 'en', 'ja', 'zh')"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples, "a.lang"))
    return CategoryResult("E3", "lang NULL 또는 비정상", cnt, samp.to_dict(orient="records"))


def scan_e4_thumbnail(conn: Connection, samples: int) -> CategoryResult:
    where = "WHERE a.thumbnail IS NULL OR a.thumbnail = ''"
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("E4", "thumbnail 누락", cnt, samp.to_dict(orient="records"))


def scan_f1_orphan_blog(conn: Connection, samples: int) -> CategoryResult:
    where = (
        "LEFT JOIN blog b ON a.blog_id = b.blog_id "
        "WHERE a.blog_id IS NOT NULL AND b.blog_id IS NULL"
    )
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(where, samples))
    return CategoryResult("F1", "blog_id orphan (blog 테이블에 없음)", cnt, samp.to_dict(orient="records"))


def scan_f2_also_in_rejected(conn: Connection, samples: int) -> CategoryResult:
    where = (
        "JOIN article_rejected r ON a.article_id = r.article_id"
    )
    cnt = int(_q(conn, f"SELECT COUNT(*) AS n FROM article a {where}")["n"].iloc[0])
    samp = _q(conn, _samples_query(
        where, samples, "r.reject_reason, r.rejected_at"
    ))
    return CategoryResult("F2", "article 와 article_rejected 양쪽에 존재 (이중 상태)", cnt, samp.to_dict(orient="records"))


SCANS: dict[str, Callable[[Connection, int], CategoryResult]] = {
    "A1": scan_a1_dup_url,
    "A2": scan_a2_dup_title,
    "A3": scan_a3_dup_content,
    "A4": scan_a4_dup_url_no_qs,
    "B1": scan_b1_title_eq_blog,
    "B2": scan_b2_title_source_token,
    "B3": scan_b3_title_too_short,
    "B4": scan_b4_title_markup,
    "B5": scan_b5_title_placeholder,
    "B6": scan_b6_title_url_like,
    "B7": scan_b7_title_eq_description,
    "C1": scan_c1_content_empty,
    "C2": scan_c2_content_short,
    "C3": scan_c3_length_mismatch,
    "C4": scan_c4_login_wall,
    "D1": scan_d1_login_url,
    "D2": scan_d2_listing_url,
    "D3": scan_d3_url_host_mismatch,
    "E1": scan_e1_published_future,
    "E2": scan_e2_published_ancient,
    "E3": scan_e3_lang,
    "E4": scan_e4_thumbnail,
    "F1": scan_f1_orphan_blog,
    "F2": scan_f2_also_in_rejected,
}


def render_markdown(total_rows: int, results: list[CategoryResult]) -> str:
    out = []
    out.append("# Data Quality Audit — `article` 테이블")
    out.append("")
    out.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    out.append(f"Total rows scanned: **{total_rows:,}**")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append("| Code | Issue | Count | % |")
    out.append("|---|---|---:|---:|")
    for r in results:
        pct = (r.count / total_rows * 100) if total_rows else 0.0
        out.append(f"| {r.code} | {r.name} | {r.count:,} | {pct:.2f}% |")
    out.append("")
    out.append("## Details")
    out.append("")
    for r in results:
        out.append(f"### {r.code}. {r.name} — {r.count:,}건")
        if r.note:
            out.append(f"_{r.note}_")
        out.append("")
        if not r.samples:
            out.append("(샘플 없음)")
            out.append("")
            continue
        cols = list(r.samples[0].keys())
        out.append("| " + " | ".join(cols) + " |")
        out.append("|" + "|".join(["---"] * len(cols)) + "|")
        for row in r.samples:
            cells = []
            for c in cols:
                v = row.get(c)
                s = "" if v is None else str(v)
                s = s.replace("\n", " ").replace("|", "\\|")
                if len(s) > 80:
                    s = s[:77] + "..."
                cells.append(s)
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)


def render_json(total_rows: int, results: list[CategoryResult]) -> str:
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_rows": total_rows,
        "categories": [
            {
                "code": r.code,
                "name": r.name,
                "count": r.count,
                "note": r.note,
                "samples": [
                    {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in s.items()}
                    for s in r.samples
                ],
            }
            for r in results
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--samples", type=int, default=10, help="샘플 row 수 per 카테고리 (기본 10)")
    p.add_argument("--categories", type=str, default="",
                   help="실행할 카테고리 코드 콤마 구분 (예: A1,B2). 비우면 전체.")
    p.add_argument("--format", choices=["markdown", "json"], default="markdown")
    p.add_argument("--output", type=str, default="", help="출력 파일 경로 (비우면 stdout)")
    args = p.parse_args()

    selected = set(c.strip().upper() for c in args.categories.split(",") if c.strip()) or set(SCANS.keys())
    unknown = selected - set(SCANS.keys())
    if unknown:
        print(f"[ERROR] Unknown categories: {sorted(unknown)}. Valid: {sorted(SCANS.keys())}",
              file=sys.stderr)
        sys.exit(2)

    conn = Connection()
    total_rows = int(_q(conn, "SELECT COUNT(*) AS n FROM article")["n"].iloc[0])
    print(f"[INFO] article rows: {total_rows:,}", file=sys.stderr)

    results: list[CategoryResult] = []
    for code in sorted(selected):
        print(f"[INFO] scanning {code} ...", file=sys.stderr)
        try:
            results.append(SCANS[code](conn, args.samples))
        except Exception as e:
            print(f"[ERROR] {code} failed: {e}", file=sys.stderr)
            results.append(CategoryResult(code, f"[FAILED] {e}", 0))

    output = (render_json if args.format == "json" else render_markdown)(total_rows, results)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"[INFO] wrote {args.output}", file=sys.stderr)
    else:
        print(output)

    conn.close()


if __name__ == "__main__":
    main()
