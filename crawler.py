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
BASE = "https://2ndstreet.com.tw"

# 到網站上手動點進各分類頁，把網址整段貼進來
CATEGORY_URLS = {
    "all":  "https://2ndstreet.com.tw/請貼上包包全部商品頁網址",
    "new":  "https://2ndstreet.com.tw/請貼上新上架頁網址",
    "sale": "https://2ndstreet.com.tw/請貼上降價頁網址",
}
PAGE_PARAM = "page"      # 翻頁參數；若網站是 ?p=2 就改成 "p"
MAX_PAGES = 30

# 用瀏覽器 F12 檢查一張商品卡片後填入（以下是常見寫法，請對照實際 DOM 修改）
SEL_ITEM = "a[href*='/goods/'], a[href*='/product']"   # 商品卡片（必須是含連結的元素）
SEL_NAME = ".name, .title, h3, h4"
SEL_PRICE = ".price, .now, [class*='price']"
SEL_ORIG = "del, s, .origin, [class*='original']"      # 原價（有降價才會出現）
SEL_IMG = "img"
ID_REGEX = r"/(?:goods|product)s?/([\w-]+)"            # 從連結抓商品 ID

DATA_FILE = Path("results.json")  # 前端 fetch("./results.json") 讀的就是這個檔
NEW_DAYS = 3                      # 首次出現後幾天內算「新上架」
MIN_KEEP_RATIO = 0.5              # 本次數量低於上次 50% 視為異常，不覆蓋資料
# ================================================================


def parse_price(text):
    """'NT$1,280' -> 1280；抓不到回傳 None"""
    nums = re.findall(r"\d[\d,]*", text or "")
    return int(nums[0].replace(",", "")) if nums else None


def scroll_to_bottom(page):
    """處理 lazy load：一路捲到底，直到商品數不再增加"""
    last = -1
    for _ in range(15):
        page.mouse.wheel(0, 4000)
        time.sleep(0.8)
        n = page.locator(SEL_ITEM).count()
        if n == last:
            break
        last = n


def extract_items(page, category):
    out = []
    for el in page.locator(SEL_ITEM).all():
        href = el.get_attribute("href") or ""
        m = re.search(ID_REGEX, href)
        if not m:
            continue

        def txt(sel):
            loc = el.locator(sel).first
            return loc.inner_text().strip() if loc.count() else ""

        price = parse_price(txt(SEL_PRICE))
        if price is None:
            continue  # 抓不到價格就跳過，避免髒資料
        orig = parse_price(txt(SEL_ORIG))
        img = el.locator(SEL_IMG).first
        img_src = ""
        if img.count():
            img_src = img.get_attribute("src") or img.get_attribute("data-src") or ""
        out.append({
            "id": m.group(1),
            "name": txt(SEL_NAME),
            "price": price,
            "orig_price": orig,
            "url": urljoin(BASE, href),
            "img": urljoin(BASE, img_src) if img_src else "",
            "category": category,
        })
    return out


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
        for cat, url in CATEGORY_URLS.items():
            for pg in range(1, MAX_PAGES + 1):
                sep = "&" if "?" in url else "?"
                page.goto(f"{url}{sep}{PAGE_PARAM}={pg}",
                          wait_until="networkidle", timeout=60000)
                scroll_to_bottom(page)
                if debug:
                    Path("debug").mkdir(exist_ok=True)
                    Path(f"debug/{cat}_{pg}.html").write_text(page.content(), encoding="utf-8")
                    page.screenshot(path=f"debug/{cat}_{pg}.png", full_page=True)

                got = extract_items(page, cat)
                fresh = [i for i in got
                         if i["id"] not in items or cat not in items[i["id"]]["tags"]]
                print(f"[{cat}] page {pg}: {len(got)} items")
                if not got and pg == 1:
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
        data = json.loads(DATA_FILE.read_text("utf-8"))
        prev = {i["id"]: i for i in data.get("items", [])}

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
            it["first_seen"] = now
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
