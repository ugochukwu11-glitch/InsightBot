
import argparse
import json
import logging
import math
import random
import re
import time
from datetime import timezone
import schedule

import mysql.connector
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser, tz
from langdetect import detect, DetectorFactory
from urllib.parse import urljoin, urlparse

DetectorFactory.seed = 0

# ---------- Config ----------
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",        # set your password if any
    "database": "insightbot"  # must exist
}

TRAINING_SITES = [
    "https://www.independent.co.uk", "https://www.chinadaily.com.cn",
    "https://www.japantimes.co.jp", "https://www.france24.com",
    "https://www.dw.com", "https://www.haaretz.com",
    "https://www.scmp.com", "https://www.lemonde.fr",
    "https://www.latimes.com", "https://www.timesofindia.com"
]

TESTING_SITES = [
    "https://www.independent.co.uk", "https://www.chinadaily.com.cn",
    "https://www.japantimes.co.jp", "https://www.france24.com",
    "https://www.dw.com", "https://www.haaretz.com",
    "https://www.scmp.com", "https://www.lemonde.fr",
    "https://www.latimes.com", "https://www.timesofindia.com"
]

HEADERS_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/115.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/88.0.4324.96 Safari/537.36",
]

# timezone hints for ambiguous tz abbreviations
TZINFOS = {
    "EST": tz.gettz("America/New_York"),
    "EDT": tz.gettz("America/New_York"),
    "CST": tz.gettz("America/Chicago"),
    "CDT": tz.gettz("America/Chicago"),
    "MST": tz.gettz("America/Denver"),
    "MDT": tz.gettz("America/Denver"),
    "PST": tz.gettz("America/Los_Angeles"),
    "PDT": tz.gettz("America/Los_Angeles"),
    "PT":  tz.gettz("America/Los_Angeles"),
    "ET":  tz.gettz("America/New_York")
}

# heuristics
BAD_EXT_RE = re.compile(r".*\.(jpg|jpeg|png|gif|svg|pdf|mp4|mp3|zip|rss|ico)$", re.I)
BAD_SUBSTRINGS = ["mailto:", "tel:", "#", "signup", "login", "terms", "privacy", "javascript:"]
SECTION_HINTS = ["/section/", "/topic/", "/tag/", "/tags/", "/category/", "/categories/", "/topics/", "/collections/", "/series/"]
POSITIVE_SIGNS = ["/news/", "/article/", "/story/", "/world/", "/politics/", "/business/", "/202"]
BAD_PHRASES = [
    "related articles", "you may also like", "more stories",
    "follow us", "share this", "advertisement", "sponsored content",
    "recommended", "read more", "watch:", "photo:", "video:"
]

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("scraper.log"), logging.StreamHandler()]
)

# ---------- Helpers ----------
def pick_user_agent():
    return random.choice(HEADERS_POOL)

def fetch_url(session, url, retries=2, timeout=12):
    for attempt in range(retries+1):
        try:
            session.headers.update({"User-Agent": pick_user_agent()})
            r = session.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            logging.warning("Non-200 %s for %s", r.status_code, url)
        except Exception as e:
            logging.warning("Fetch error (%s) for %s", e, url)
        time.sleep(1 + random.random())
    return None

def normalize_domain(url):
    net = urlparse(url).netloc.lower()
    if net.startswith("www."):
        net = net[4:]
    return net

def normalize_date_to_mysql(dt_raw):
    """Return a string 'YYYY-MM-DD HH:MM:SS' in UTC or None"""
    if not dt_raw:
        return None
    try:
        dt = dateparser.parse(str(dt_raw), fuzzy=True, tzinfos=TZINFOS)
        # if parsed but has no tzinfo, assume UTC
        if dt.tzinfo:
            dt_utc = dt.astimezone(timezone.utc)
        else:
            dt_utc = dt.replace(tzinfo=timezone.utc)
        return dt_utc.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

# ---------- Title extraction & heuristics ----------
def clean_title(text):
    if not text:
        return "N/A"
    t = " ".join(text.split())
    return re.sub(r"\s+[–\-—|•·]\s+.*$", "", t).strip()

def extract_title_generic(soup):
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return clean_title(h1.get_text(" ", strip=True))
    meta = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name":"twitter:title"})
    if meta and meta.get("content"):
        return clean_title(meta.get("content"))
    if soup.title and soup.title.string:
        return clean_title(soup.title.string)
    return "N/A"

def score_container(el):
    ps = el.find_all("p")
    if not ps:
        return 0
    total_len = sum(len(p.get_text(" ", strip=True)) for p in ps)
    num_p = len(ps)
    num_a = len(el.find_all("a"))
    score = total_len * (1 + math.log1p(num_p)) - (num_a * 30)
    return score

def find_best_container(soup, min_score=180):
    candidates = []
    for tag in ("article", "main", "section", "div"):
        for el in soup.find_all(tag):
            try:
                s = score_container(el)
                if s > 0:
                    candidates.append((s, el))
            except Exception:
                continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_el = candidates[0]
    return best_el if best_score >= min_score else None

def extract_paragraphs_from_el(el, min_len=40, max_paras=120):
    paras = []
    if not el:
        return paras
    for p in el.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) < min_len:
            continue
        low = text.lower()
        if any(bad in low for bad in BAD_PHRASES):
            continue
        if len(text.split()) <= 6 and text.endswith(":"):
            continue
        paras.append(text)
        if len(paras) >= max_paras:
            break
    return paras

# ---------- Generic article extraction (fallback) ----------
def extract_article_generic(session, url):
    r = fetch_url(session, url)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "lxml")

    title = extract_title_generic(soup)
    container = find_best_container(soup)
    body_paras = extract_paragraphs_from_el(container) if container else []

    # final fallback: long <p>
    if not body_paras:
        all_ps = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        body_paras = [p for p in all_ps if len(p) > 80][:120]

    body_text = " ".join(body_paras).strip()

    if title == "N/A" or len(title.split()) < 3:
        logging.debug("Bad title for %s -> %s", url, title)
        return None
    if len(body_paras) < 2 or len(body_text) < 150:
        logging.debug("Insufficient body for %s (paras=%d, len=%d)", url, len(body_paras), len(body_text))
        return None

    # published detection (best-effort)
    published = parse_published_generic(soup, url)
    published_norm = normalize_date_to_mysql(published)

    lang = "unknown"
    try:
        if body_text:
            lang = detect(body_text)
    except Exception:
        pass

    return {
        "url": url,
        "title": title,
        "body": body_text,
        "published": published_norm,
        "length": len(body_text),
        "source": normalize_domain(url),
        "language": lang
    }

# ---------- Generic published parsing ----------
def parse_published_generic(soup, url):
    texts = []
    meta_keys = [
        ("meta", {"property":"article:published_time"}),
        ("meta", {"name":"pubdate"}),
        ("meta", {"name":"publish-date"}),
        ("meta", {"name":"publication_date"}),
        ("meta", {"itemprop":"datePublished"}),
        ("meta", {"property":"og:updated_time"}),
        ("meta", {"name":"date"}),
    ]
    for tag, attrs in meta_keys:
        el = soup.find(tag, attrs=attrs)
        if el:
            val = el.get("content") or el.get("value") or el.get_text(" ", strip=True)
            if val:
                texts.append(val.strip())

    for t in soup.find_all("time"):
        dt = t.get("datetime")
        if dt:
            texts.append(dt.strip())
        else:
            txt = t.get_text(" ", strip=True)
            if txt:
                texts.append(txt.strip())

    for sel in ["span.pubdate", ".published-date", ".article-date", ".date", ".byline time", ".meta__date"]:
        el = soup.select_one(sel)
        if el:
            txt = el.get("datetime") or el.get_text(" ", strip=True)
            if txt:
                texts.append(txt.strip())

    page_text = soup.get_text(" ", strip=True)
    m = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", page_text)
    if m:
        texts.append(m.group(0))

    for t in texts:
        try:
            dt = dateparser.parse(t, fuzzy=True, tzinfos=TZINFOS)
            return dt
        except Exception:
            continue

    parsed = urlparse(url)
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", parsed.path)
    if m:
        try:
            dt = dateparser.parse(f"{m.group(1)}-{m.group(2)}-{m.group(3)}", fuzzy=True)
            return dt
        except Exception:
            pass

    return None

# ---------- Site-specific scrapers ----------
def scrape_cnn(session, url):
    r = fetch_url(session, url)
    if not r: return None
    soup = BeautifulSoup(r.text, "lxml")

    title = extract_title_generic(soup)

    selectors = ["div.l-container article p", "div.pg-rail-tall__body p", "article p", "div.zn-body__paragraph"]
    paras = []
    for sel in selectors:
        for p in soup.select(sel):
            txt = p.get_text(" ", strip=True)
            if txt: paras.append(txt)
        if len(paras) >= 4:
            break
    if not paras:
        paras = extract_paragraphs_from_el(soup.select_one("article")) if soup.select_one("article") else []

    body = " ".join(paras).strip()
    if len(body) < 150: return None

    published = None
    meta = soup.find("meta", attrs={"itemprop":"datePublished"}) or soup.find("meta", attrs={"name":"pubdate"})
    if meta and meta.get("content"):
        published = meta.get("content")
    else:
        t = soup.find("meta", property="article:published_time")
        if t and t.get("content"):
            published = t.get("content")
    published_norm = normalize_date_to_mysql(published) if published else normalize_date_to_mysql(parse_published_generic(soup, url))

    lang = "en"
    return {
        "url": url, "title": title, "body": body, "published": published_norm,
        "length": len(body), "source": normalize_domain(url), "language": lang
    }

def scrape_bbc(session, url):
    r = fetch_url(session, url)
    if not r: return None
    soup = BeautifulSoup(r.text, "lxml")

    title = extract_title_generic(soup)
    # BBC article p tags often within article or .ssrcss-*
    paras = [p.get_text(" ", strip=True) for p in soup.select("article p, .ssrcss-uf6wea-RichTextComponentWrapper p, .story-body__inner p")]
    paras = [p for p in paras if p and len(p) > 40]
    body = " ".join(paras).strip()
    if len(body) < 150: return None

    # time tag
    time_tag = soup.find("time")
    published = time_tag.get("datetime") if time_tag and time_tag.get("datetime") else None
    published_norm = normalize_date_to_mysql(published) if published else normalize_date_to_mysql(parse_published_generic(soup, url))

    lang = "en"
    return {"url": url, "title": title, "body": body, "published": published_norm, "length": len(body), "source": normalize_domain(url), "language": lang}

def scrape_nytimes(session, url):
    r = fetch_url(session, url)
    if not r: return None
    soup = BeautifulSoup(r.text, "lxml")
    title = extract_title_generic(soup)
    # NYT article paragraphs often in section[name="articleBody"] p or div[class*='StoryBodyCompanionColumn'] p
    paras = [p.get_text(" ", strip=True) for p in soup.select("section[name='articleBody'] p, .css-53u6y8 p, article p")]
    paras = [p for p in paras if p and len(p) > 30]
    body = " ".join(paras).strip()
    if len(body) < 150: return None
    # published
    meta = soup.find("meta", attrs={"name":"ptime"}) or soup.find("meta", attrs={"property":"article:published"})
    published = None
    if meta and meta.get("content"):
        published = meta.get("content")
    else:
        time_tag = soup.find("time")
        published = time_tag.get("datetime") if time_tag and time_tag.get("datetime") else None
    published_norm = normalize_date_to_mysql(published) if published else normalize_date_to_mysql(parse_published_generic(soup, url))
    lang = "en"
    return {"url": url, "title": title, "body": body, "published": published_norm, "length": len(body), "source": normalize_domain(url), "language": lang}

def scrape_guardian(session, url):
    r = fetch_url(session, url)
    if not r: return None
    soup = BeautifulSoup(r.text, "lxml")
    title = extract_title_generic(soup)
    paras = [p.get_text(" ", strip=True) for p in soup.select("div[itemprop='articleBody'] p, article p, .content__article-body p")]
    paras = [p for p in paras if p and len(p) > 30]
    body = " ".join(paras).strip()
    if len(body) < 150: return None
    time_tag = soup.find("time")
    published = time_tag.get("datetime") if time_tag and time_tag.get("datetime") else None
    published_norm = normalize_date_to_mysql(published) if published else normalize_date_to_mysql(parse_published_generic(soup, url))
    lang = "en"
    return {"url": url, "title": title, "body": body, "published": published_norm, "length": len(body), "source": normalize_domain(url), "language": lang}

def scrape_reuters(session, url):
    r = fetch_url(session, url)
    if not r: return None
    soup = BeautifulSoup(r.text, "lxml")
    title = extract_title_generic(soup)
    paras = [p.get_text(" ", strip=True) for p in soup.select("div.ArticleBodyWrapper p, .article-body__content p, .StandardArticleBody_body p, article p")]
    paras = [p for p in paras if p and len(p) > 30]
    body = " ".join(paras).strip()
    if len(body) < 150: return None
    t = soup.find("meta", {"property":"article:published_time"}) or soup.find("time")
    published = t.get("content") if t and t.get("content") else (t.get("datetime") if t and t.get("datetime") else None)
    published_norm = normalize_date_to_mysql(published) if published else normalize_date_to_mysql(parse_published_generic(soup, url))
    lang = "en"
    return {"url": url, "title": title, "body": body, "published": published_norm, "length": len(body), "source": normalize_domain(url), "language": lang}

def scrape_aljazeera(session, url):
    r = fetch_url(session, url)
    if not r: return None
    soup = BeautifulSoup(r.text, "lxml")
    title = extract_title_generic(soup)
    paras = [p.get_text(" ", strip=True) for p in soup.select("div.wysiwyg p, article p")]
    paras = [p for p in paras if p and len(p) > 30]
    body = " ".join(paras).strip()
    if len(body) < 150: return None
    time_tag = soup.find("time")
    published = time_tag.get("datetime") if time_tag and time_tag.get("datetime") else None
    published_norm = normalize_date_to_mysql(published) if published else normalize_date_to_mysql(parse_published_generic(soup, url))
    lang = "ar" if "aljazeera.net" in normalize_domain(url) and "/arabic" in url else "en"
    return {"url": url, "title": title, "body": body, "published": published_norm, "length": len(body), "source": normalize_domain(url), "language": lang}

SITE_SCRAPERS = {
    "cnn.com": scrape_cnn,
    "bbc.com": scrape_bbc,
    "nytimes.com": scrape_nytimes,
    "theguardian.com": scrape_guardian,
    "reuters.com": scrape_reuters,
    "aljazeera.net": scrape_aljazeera
}

def extract_article(session, url):
    domain = normalize_domain(url)
    for key, fn in SITE_SCRAPERS.items():
        if key in domain:
            try:
                return fn(session, url)
            except Exception:
                logging.exception("Site-specific scraper failed for %s", url)
                return None
    # fallback
    return extract_article_generic(session, url)

# ---------- Link collection (homepage) ----------
def is_probable_article(href, anchor_text=""):
    if not href:
        return False
    href_l = href.lower()
    if BAD_EXT_RE.match(href_l) or any(b in href_l for b in BAD_SUBSTRINGS):
        return False
    parsed = urlparse(href_l)
    path = parsed.path or ""
    if any(seg in path for seg in SECTION_HINTS):
        return False
    if path.endswith("/") and path.count("/") <= 3:
        return False
    if re.search(r"/\d{4}/\d{2}/\d{2}/", path):
        return True
    if any(k in href_l for k in POSITIVE_SIGNS):
        return True
    if path.count("-") >= 2 and len(path) > 25:
        return True
    if len(anchor_text.strip().split()) >= 4:
        return True
    return False

def collect_article_links(session, site_url, limit=12):
    r = fetch_url(session, site_url)
    if not r:
        logging.warning("Failed to fetch homepage %s", site_url)
        return []
    soup = BeautifulSoup(r.text, "lxml")
    links = []
    seen = set()

    # 1) headlines inside h1/h2
    for tag in soup.find_all(["h1", "h2"]):
        a = tag.find("a", href=True)
        if not a:
            continue
        full = urljoin(site_url, a["href"])
        if full in seen:
            continue
        if is_probable_article(full, a.get_text(" ", strip=True)):
            seen.add(full); links.append(full)
            if len(links) >= limit:
                return links

    # 2) promo/card selectors (common)
    selectors = ["a.card", "a.promo", ".headline a", "a[href]"]
    for sel in selectors:
        for a in soup.select(sel):
            href = a.get("href")
            if not href:
                continue
            full = urljoin(site_url, href)
            if full in seen:
                continue
            txt = a.get_text(" ", strip=True)
            if is_probable_article(full, txt):
                seen.add(full); links.append(full)
                if len(links) >= limit:
                    return links

    # 3) fallback: all anchors
    for a in soup.find_all("a", href=True):
        full = urljoin(site_url, a["href"])
        if full in seen:
            continue
        txt = a.get_text(" ", strip=True)
        if is_probable_article(full, txt):
            seen.add(full); links.append(full)
            if len(links) >= limit:
                return links

    # 4) final fallback: long hyphen slugs
    if len(links) < limit:
        for a in soup.find_all("a", href=True):
            full = urljoin(site_url, a["href"])
            if full in seen:
                continue
            path = urlparse(full).path
            if path and path.count("-") >= 2 and len(path) > 25:
                seen.add(full); links.append(full)
                if len(links) >= limit:
                    break

    return links

# ---------- DB helpers ----------
def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INT AUTO_INCREMENT PRIMARY KEY,
            url VARCHAR(500) NOT NULL UNIQUE,
            title VARCHAR(500) NOT NULL,
            body LONGTEXT NOT NULL,
            published DATETIME NULL,
            length INT,
            source VARCHAR(255),
            language VARCHAR(20)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

def save_to_mysql_batch(records):
    if not records:
        logging.info("No records to save")
        return
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        ensure_table(cursor)

        rows = []
        for a in records:
            rows.append((
                a.get("url"),
                a.get("title"),
                a.get("body"),
                a.get("published"),   # already normalized or None
                a.get("length"),
                a.get("source"),
                a.get("language")
            ))

        sql = """
        INSERT INTO articles (url, title, body, published, length, source, language)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          title=VALUES(title),
          body=VALUES(body),
          published=COALESCE(VALUES(published), published),
          length=VALUES(length),
          source=VALUES(source),
          language=VALUES(language)
        """
        cursor.executemany(sql, rows)
        conn.commit()
        logging.info("Inserted/updated %d rows into articles table", cursor.rowcount)
    except Exception as e:
        logging.exception("DB save error: %s", e)
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass

# ---------- Main scraping loop ----------
def scrape_all(sites, per_site_limit=6, pause=(1.0, 2.0)):
    session = requests.Session()
    results = []
    for site in sites:
        logging.info("SITE: %s", site)
        try:
            candidate_links = collect_article_links(session, site, limit=per_site_limit * 8)
            if not candidate_links:
                logging.warning("No candidate links found for %s", site)
            extracted = 0
            for link in candidate_links:
                if extracted >= per_site_limit:
                    break
                logging.info(" -> trying: %s", link)
                art = extract_article(session, link)
                if art:
                    # ensure published normalized if not already (site-specific scrapers generally return normalized)
                    if art.get("published") and not isinstance(art.get("published"), str):
                        art["published"] = normalize_date_to_mysql(art.get("published"))
                    elif art.get("published") and isinstance(art.get("published"), str):
                        # try to normalize string
                        art["published"] = normalize_date_to_mysql(art.get("published"))
                    else:
                        art["published"] = None

                    # language detection if missing
                    if not art.get("language") or art.get("language") == "unknown":
                        try:
                            art["language"] = detect(art.get("body") or "")
                        except Exception:
                            art["language"] = "unknown"

                    results.append(art)
                    extracted += 1
                    logging.info("   ✓ extracted (len=%d) %s", art["length"], link)
                else:
                    logging.info("   ✗ skipped %s", link)
                time.sleep(random.uniform(*pause))
            if extracted == 0:
                logging.warning("Extracted 0 articles for %s", site)
        except Exception as e:
            logging.exception("Error scraping site %s: %s", site, e)
    return results

# ---------- CLI Entrypoint ----------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["train", "test"], default="train",
                        help="train (40 sites) or test (10 unseen sites)")
    parser.add_argument("--per-site", type=int, default=3, help="articles to extract per site")
    parser.add_argument("--schedule", action="store_true",
                        help="Run scraper daily at 08:00 instead of once")
    args = parser.parse_args()

    # Pick sites and output files
    sites = TRAINING_SITES if args.mode == "train" else TESTING_SITES
    out_csv = "news_hybrid_training.csv" if args.mode == "train" else "news_hybrid_testing.csv"
    out_json = "news_hybrid_training.json" if args.mode == "train" else "news_hybrid_testing.json"

    def run_scraper():
        logging.info("Starting scraper (mode=%s, per-site=%d)", args.mode, args.per_site)
        articles = scrape_all(sites, per_site_limit=args.per_site)

        if articles:
            df = pd.DataFrame(articles)
            df = df[["url", "title", "body", "published", "length", "source", "language"]]
            df.to_csv(out_csv, index=False)
            with open(out_json, "w", encoding="utf-8") as f:
                for rec in articles:
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            logging.info("Saved %d articles to %s and %s", len(articles), out_csv, out_json)

            # Save to MySQL
            save_to_mysql_batch(articles)
        else:
            logging.warning("No articles were extracted — check site blocking or heuristics.")

    if args.schedule:
        run_scraper()

        # Then schedule every day at 08:00
        schedule.every().day.at("08:00").do(run_scraper)
        logging.info("Scheduler started — scraper will run daily at 08:00")

        while True:
            schedule.run_pending()
            time.sleep(60)
    else:

        run_scraper()
