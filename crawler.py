"""
2nd STREET 台灣 包包 新上架 / 降價 爬蟲（完整版）

安裝：pip install playwright && playwright install chromium
執行：python crawler.py          -> 產生 results.json（前端直接讀取）
除錯：python crawler.py --debug  -> 另存 debug/*.html 與截圖，用來確認選擇器
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

# ==================== 1. 設定區（只需要改這裡）====================
BASE = "https://store.2ndstreet.com.tw"

# 分類頁網址（整段貼上即可；如果誤貼成兩段網址黏在一起，程式會自動修正）
CATEGORY_URLS = {
    "new":  "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/442464",
    "sale": "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/442514",
    # 若有「全部包包」分類頁，把下一行的 # 拿掉並換成網址：
    # "all": "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/這裡換成分類編號",
}
PAGE_PARAM = "page"      # 翻頁參數；若網站不支援翻頁，程式會偵測到重複頁自動停止
MAX_PAGES = 1         # 91APP 分類頁是無限捲動，不支援 ?page=，只需載入第 1 頁

# 商品連結：91APP 商品頁格式為 /SalePage/Index/商品編號（也相容 /SalePage/商品編號）
SEL_ITEM = "a[href*='/SalePage/']"
ID_REGEX = r"/SalePage/(?:Index/)?(\d+)"

# 只保留包包：商品名稱格式為「【店名】品牌/品類//型號」，用「品類」判斷
BAG_ONLY = True
BAG_WORDS = ("包", "袋")
NOT_BAG_WORDS = ("錢包", "皮夾", "長夾", "短夾", "零錢", "卡夾", "卡包", "名片", "鑰匙", "包鞋", "包頭")

# 已售完 / 補貨中的商品不收錄
SOLD_RE = re.compile(r"售完|售罄|缺貨|補貨中|SOLD\s*OUT|已售出(?!\s*[\d,]+)", re.I)
DIAG = True   # 在 log 印出卡片原始文字與 API 範例，用來檢查價格；確認沒問題後可改 False

DATA_FILE = Path("results.json")  # 前端 fetch("./results.json") 讀的就是這個檔
NEW_DAYS = 3                      # 首次出現後幾天內算「新上架」
MIN_KEEP_RATIO = 0.5              # 本次數量低於上次 50% 視為異常，不覆蓋資料
# ================================================================


def category_of(name):
    """'【桃園統領店】MOORER/背心//FIRE-WK' -> '背心'"""
    head = (name or "").split("//")[0]
    parts = [p.strip() for p in head.split("/") if p.strip()]
    return parts[-1] if len(parts) >= 2 else head


def brand_of(name):
    """'【桃園統領店】MOORER/背心//FIRE-WK' -> 'MOORER'；抓不到回傳 '其他'"""
    head = (name or "").split("//")[0]
    parts = [p.strip() for p in head.split("/") if p.strip()]
    if len(parts) < 2:
        return "其他"
    brand = re.sub(r"^【[^】]*】\s*", "", parts[0])
    brand = re.sub(r"\s+", " ", brand).strip().upper()
    return brand or "其他"


def is_bag(name):
    cat = category_of(name)
    if any(w in cat for w in NOT_BAG_WORDS):
        return False
    return any(w in cat for w in BAG_WORDS)


def parse_price(text):
    """'NT$1,280' -> 1280；抓不到回傳 None"""
    nums = re.findall(r"\d[\d,]*", text or "")
    return int(nums[0].replace(",", "")) if nums else None


def scroll_to_bottom(page):
    """處理無限捲動：一直往下捲，連續 4 次數量沒增加才停（最多 80 次）"""
    last, still = -1, 0
    start = time.time()
    for _ in range(80):
        if time.time() - start > 240:   # 單一分類最多捲 4 分鐘
            print("  捲動超過 4 分鐘，先用目前載入的商品")
            break
        page.mouse.wheel(0, 5000)
        time.sleep(1.0)
        n = page.locator(SEL_ITEM).count()
        if n == last:
            still += 1
            if still >= 4:
                break
        else:
            still = 0
        last = n


JS_CARDS = r"""
(sel) => {
  const out = [];
  document.querySelectorAll(sel).forEach((a) => {
    let card = a;
    for (let i = 0; i < 5 && card && !/\$|NT/.test(card.innerText || ""); i++) {
      card = card.parentElement;
    }
    if (!card) return;
    const imgs = Array.from(card.querySelectorAll("img")).filter(
      (i) => !/ProductBadge|badge/i.test((i.src || "") + (i.alt || "") + (i.getAttribute("data-qe-id") || ""))
    );
    const img = imgs[0] || null;
    const soldEl = card.querySelector(
      '[class*="soldout" i], [class*="sold-out" i], [class*="sold_out" i], [class*="out-of-stock" i], [class*="outofstock" i]'
    );
    out.push({
      sold: !!soldEl,
      href: a.getAttribute("href") || "",
      text: card.innerText || "",
      img: img ? (img.currentSrc || img.src || img.getAttribute("data-src") || "") : "",
      alt: img ? (img.alt || "") : "",
    });
  });
  return out;
}
"""


def parse_card(raw):
    """從卡片文字抓出名稱與價格：最低價 = 現價，最高價 = 原價"""
    text = raw["text"]
    if len(text) > 400:      # 抓到整個列表容器，不是單一商品
        return None
    amounts = [int(x.replace(",", ""))
               for x in re.findall(r"(?:NT\$|\$)\s*([\d,]+)", text)]
    amounts = [a for a in amounts if a > 0]
    if not amounts:   # 沒有 $ 符號時，改抓「整行只有數字」的價格行
        for l in text.splitlines():
            l = l.strip()
            if re.fullmatch(r"[\d,]{3,}", l):
                amounts.append(int(l.replace(",", "")))
    if not amounts:
        return None
    price = min(amounts)
    orig = max(amounts) if max(amounts) > price else None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    names = [l for l in lines if not re.search(r"\$|NT|折|^\d+$", l)]
    name = max(names, key=len) if names else (raw.get("alt") or "")
    return {"name": name or raw.get("alt", ""), "price": price, "orig_price": orig}


def extract_items(page, category, samples=None):
    out = {}
    for raw in page.evaluate(JS_CARDS, SEL_ITEM):
        m = re.search(ID_REGEX, raw["href"])
        if not m:
            continue
        info = parse_card(raw)
        if not info:
            continue
        if samples is not None and len(samples) < 4:
            samples.append(raw["text"])
        out[m.group(1)] = {
            "sold": bool(raw.get("sold")) or bool(SOLD_RE.search(raw["text"])),
            "id": m.group(1),
            "name": info["name"],
            "price": info["price"],
            "orig_price": info["orig_price"],
            "url": urljoin(BASE, raw["href"]),
            "img": urljoin(BASE, raw["img"]) if raw["img"] else "",
            "category": category,
        }
    return list(out.values())


def clean_url(url):
    """修正誤貼成 https://a.com/https://b.com/... 的網址"""
    if url.count("https://") > 1:
        url = "https://" + url.split("https://")[-1]
    return url


def diagnose(page, cat):
    """抓到 0 筆時，把頁面狀況印在 log，判斷是被擋還是選擇器錯"""
    print(f"----- 診斷 [{cat}] -----")
    print("最終網址:", page.url)
    print("頁面標題:", page.title())
    body = page.evaluate("document.body ? document.body.innerText : ''") or ""
    print("內文前 200 字:", body.strip()[:200].replace("\n", " | "))
    print("a 數量:", page.locator("a").count(), "／ img 數量:", page.locator("img").count())
    hrefs = page.evaluate(
        "Array.from(document.querySelectorAll('a[href]')).map(a=>a.getAttribute('href'))"
    )
    seen = []
    for h in hrefs:
        if h and h not in seen and not h.startswith(("#", "javascript")):
            seen.append(h)
    print("連結範例（前 25 個）:")
    for h in seen[:25]:
        print("  ", h)
    cards = page.evaluate(
        "Array.from(document.querySelectorAll('li,article,div')).filter(e=>e.querySelector('img')&&e.querySelector('a')&&/\\d{3,}/.test(e.innerText)&&e.innerText.length<200).slice(0,2).map(e=>e.outerHTML.slice(0,600))"
    )
    print("疑似商品卡片 HTML 範例:")
    for c in cards:
        print("  ", c.replace("\n", " "))
    print("----- 診斷結束 -----")


def show_api_samples(responses, limit=3):
    """診斷：印出含價格的 JSON API 回應開頭，用來確認價格欄位"""
    shown = 0
    for r in responses[:60]:
        try:
            body = r.text()
        except Exception:
            continue
        if len(body) > 200 and re.search(r"price", body, re.I):
            print("  API:", r.url[:120])
            print("      ", body[:500].replace("\n", " "))
            shown += 1
            if shown >= limit:
                break


def crawl(debug=False):
    items = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="zh-TW",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        api_responses = []
        if DIAG:
            def _collect(r):
                try:
                    if "json" in (r.headers.get("content-type") or ""):
                        api_responses.append(r)
                except Exception:
                    pass
            page.on("response", _collect)
        for cat, url in CATEGORY_URLS.items():
            url = clean_url(url)
            api_responses.clear()
            for pg in range(1, MAX_PAGES + 1):
                sep = "&" if "?" in url else "?"
                page.goto(f"{url}{sep}{PAGE_PARAM}={pg}",
                          wait_until="networkidle", timeout=60000)
                scroll_to_bottom(page)
                if debug:
                    try:
                        Path("debug").mkdir(exist_ok=True)
                        Path(f"debug/{cat}_{pg}.html").write_text(page.content(), encoding="utf-8")
                        page.screenshot(path=f"debug/{cat}_{pg}.png", timeout=10000)
                    except Exception as e:   # 除錯檔失敗不影響主流程
                        print("  除錯檔儲存失敗（略過）:", str(e)[:80])

                samples = [] if DIAG else None
                got = extract_items(page, cat, samples)
                total = len(got)
                if DIAG:
                    print(f"--- [{cat}] 卡片原始文字範例（| 代表換行）---")
                    for t in samples:
                        print("   ", t.replace("\n", " | ")[:160])
                    show_api_samples(api_responses)
                if BAG_ONLY:
                    got = [i for i in got if is_bag(i["name"])]
                sold_n = sum(1 for i in got if i["sold"])
                if got and sold_n / len(got) > 0.6:
                    print(f"  警告：{sold_n}/{len(got)} 件被判定售完，疑似誤判，本頁不排除售完商品")
                else:
                    got = [i for i in got if not i["sold"]]
                    print(f"  已排除售完/補貨中 {sold_n} 件")
                fresh = [i for i in got
                         if i["id"] not in items or cat not in items[i["id"]]["tags"]]
                print(f"[{cat}] page {pg}: 共 {total} 件，其中包包 {len(got)} 件")
                if total == 0 and pg == 1:
                    diagnose(page, cat)
                if not got or not fresh:   # 沒東西或整頁重複 = 已到最後一頁
                    break

                for it in got:
                    if it["id"] in items:
                        items[it["id"]]["tags"].add(cat)
                        if it["orig_price"]:   # 降價頁通常有原價，優先採用
                            items[it["id"]].update(price=it["price"],
                                                   orig_price=it["orig_price"])
                    else:
                        it["tags"] = {cat}
                        items[it["id"]] = it
                time.sleep(1.2)   # 禮貌延遲
        browser.close()
    return items


def merge_with_previous(current):
    """和上次快照比對，算出『新上架』與『降價』"""
    prev = {}
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text("utf-8"))
        except ValueError:
            data = {}
        rows = data.get("items", []) if isinstance(data, dict) else data
        prev = {
            i["id"]: i for i in rows
            if isinstance(i, dict) and {"id", "first_seen", "price"} <= set(i)
        }
        if BAG_ONLY:
            prev = {k: v for k, v in prev.items() if is_bag(v.get("name", ""))}
        if rows and not prev:
            print("舊版 results.json 格式不相容，視為第一次執行。")

    if prev and len(current) < len(prev) * MIN_KEEP_RATIO:
        sys.exit(f"異常：本次只抓到 {len(current)} 筆（上次 {len(prev)}），"
                 "可能被擋或選擇器失效，已中止、不覆蓋資料。")

    now_dt = datetime.now(timezone.utc).replace(microsecond=0)
    now = now_dt.isoformat()
    result = []
    for pid, it in current.items():
        old = prev.get(pid)
        it["tags"] = sorted(it["tags"])
        on_sale_page = bool(it["orig_price"] and it["orig_price"] > it["price"])

        if old is None:
            # 第一次執行只是建立基準：首次出現時間記為很早以前，避免全部被當成新上架
            it["first_seen"] = now if prev else "1970-01-01T00:00:00+00:00"
            # 第一次執行（沒有舊資料）時，不把整站都算成新上架
            it["is_new"] = bool(prev) or "new" in it["tags"]
            it["price_history"] = [{"t": now, "p": it["price"]}]
            it["price_dropped"] = on_sale_page
        else:
            it["first_seen"] = old["first_seen"]
            hist = old.get("price_history", [])
            if not hist or hist[-1]["p"] != it["price"]:
                hist.append({"t": now, "p": it["price"]})
            it["price_history"] = hist
            age = now_dt - datetime.fromisoformat(old["first_seen"])
            it["is_new"] = age.days < NEW_DAYS or "new" in it["tags"]
            it["price_dropped"] = (it["price"] < old["price"]) or on_sale_page

        # 「新上架」與「已降價」不重複顯示：有降價的只算降價
        if it["price_dropped"]:
            it["is_new"] = False

        it["brand"] = brand_of(it["name"])

        # 對應前端 normalizeItem 讀的欄位
        it["title"] = it["name"]
        it["image"] = it["img"]
        it["is_price_drop"] = it["price_dropped"]
        result.append(it)
    return result, now


def main():
    debug = "--debug" in sys.argv
    if any("請貼上" in u for u in CATEGORY_URLS.values()):
        sys.exit("請先在設定區 CATEGORY_URLS 填入真實的分類頁網址。")

    current = crawl(debug)
    if not current:
        sys.exit("一筆都沒抓到：請用 --debug 檢查 debug/*.html 是否為驗證頁，並核對選擇器。")

    items, now = merge_with_previous(current)
    DATA_FILE.write_text(
        json.dumps({"updated": now, "items": items}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"完成：{len(items)} 筆，新上架 {sum(i['is_new'] for i in items)}，"
          f"降價 {sum(i['price_dropped'] for i in items)}")


if __name__ == "__main__":
    main()
