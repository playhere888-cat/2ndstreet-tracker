"""
2nd STREET 台灣 包包 新上架 / 降價 爬蟲（Telegram 串接與測試版）

執行一般爬蟲：python crawler.py
測試 TG 推播：python crawler.py --test-tg
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

# ==================== 1. 設定區 ====================
BASE = "https://store.2ndstreet.com.tw"

CATEGORY_URLS = {
    "new":  "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/442464",
    "sale": "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/442514",
}
PAGE_PARAM = "page"
MAX_PAGES = 1

SEL_ITEM = "a[href*='/SalePage/']"
ID_REGEX = r"/SalePage/(?:Index/)?(\d+)"

BAG_ONLY = True
BAG_WORDS = ("包", "袋")
NOT_BAG_WORDS = ("錢包", "皮夾", "長夾", "短夾", "零錢", "卡夾", "卡包", "名片", "鑰匙", "包鞋", "包頭")

TARGET_BRANDS_RAW = [
    "Christian Dior", "BOTTEGA VENETA", "FENDI", "Salvatore Ferragamo",
    "LOEWE", "BURBERRY", "Chloe", "HERMES", "BALENCIAGA", "SAINT LAURENT",
    "MIU MIU", "YVES SAINT LAURENT", "BURBERRY LONDON", "LOUIS VUITTON",
    "GUCCI", "CHANEL", "CELINE", "PRADA",
]

SOLD_RE = re.compile(r"售完|售罄|缺貨|補貨中|SOLD\s*OUT|已售出(?!\s*[\d,]+)", re.I)
USD_TO_TWD = 32.5

DATA_FILE = Path("results.json")
NEW_DAYS = 3
MIN_KEEP_RATIO = 0.05
# ==================================================


def normalize_brand_name(raw):
    return re.sub(r"\s+", " ", str(raw or "")).strip().upper()


TARGET_BRANDS = {
    normalize_brand_name(b) for b in (TARGET_BRANDS_RAW or []) if normalize_brand_name(b)
}


def category_of(name):
    head = (name or "").split("//")[0]
    parts = [p.strip() for p in head.split("/") if p.strip()]
    return parts[-1] if len(parts) >= 2 else head


def brand_of(name):
    head = (name or "").split("//")[0]
    parts = [p.strip() for p in head.split("/") if p.strip()]
    if len(parts) < 2:
        return "其他"
    brand = re.sub(r"^【[^】]*】\s*", "", parts[0])
    brand = normalize_brand_name(brand)
    return brand or "其他"


def is_target_brand(name):
    if not TARGET_BRANDS:
        return True
    return brand_of(name) in TARGET_BRANDS


def is_bag(name):
    cat = category_of(name)
    if any(w in cat for w in NOT_BAG_WORDS):
        return False
    return any(w in cat for w in BAG_WORDS)


def scroll_to_bottom(page):
    last, still = -1, 0
    start = time.time()
    for _ in range(80):
        if time.time() - start > 240:
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
    for (let i = 0; i < 6 && card && !/\$|NT|[\d,]{3,}/.test(card.innerText || ""); i++) {
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
    const strikeTexts = Array.from(card.querySelectorAll("del, s, strike, [class*='line-through' i], [style*='line-through' i]"))
      .map((el) => (el.innerText || "").trim())
      .filter(Boolean);
    out.push({
      sold: !!soldEl,
      href: a.getAttribute("href") || "",
      text: card.innerText || "",
      strikeText: strikeTexts.join(" "),
      img: img ? (img.currentSrc || img.src || img.getAttribute("data-src") || "") : "",
      alt: img ? (img.alt || "") : "",
    });
  });
  return out;
}
"""


def _extract_prices_from_json(obj, found):
    if isinstance(obj, dict):
        pid = obj.get("salePageId") or obj.get("SalePageId") or obj.get("productId") or obj.get("ProductId")
        raw_price = None
        raw_orig = None
        for k in ("price", "Price", "salePrice", "SalePrice", "finalPrice", "FinalPrice", "twdPrice"):
            if k in obj and obj[k] is not None:
                raw_price = obj[k]
                break
        for k in ("suggestedPrice", "SuggestedPrice", "listPrice", "ListPrice", "originPrice"):
            if k in obj and obj[k] is not None:
                raw_orig = obj[k]
                break

        if pid is not None and raw_price is not None:
            try:
                p_val = float(str(raw_price).replace(",", ""))
                if p_val >= 100 and "." not in str(raw_price):
                    p_int = int(round(p_val))
                    o_int = None
                    if raw_orig is not None:
                        o_val = float(str(raw_orig).replace(",", ""))
                        if o_val > p_int and "." not in str(raw_orig):
                            o_int = int(round(o_val))
                    found[str(pid)] = {"price": p_int, "orig_price": o_int}
            except ValueError:
                pass

        for v in obj.values():
            _extract_prices_from_json(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _extract_prices_from_json(item, found)


def _extract_num_list(text, is_us, has_decimal):
    raw_matches = re.findall(r"(?:US\$|NT\$|\$|TWD|USD)?\s*([\d,]+(?:\.\d+)?)", text or "")
    vals = []
    for m in raw_matches:
        try:
            v = float(m.replace(",", ""))
            if v > 0:
                vals.append(v)
        except ValueError:
            continue
    if not vals:
        return []
    if is_us or has_decimal:
        return [round(v * USD_TO_TWD) for v in vals]
    return [round(v) for v in vals if v >= 10]


def parse_card(raw):
    text = raw["text"]
    if len(text) > 500:
        return None

    is_us = bool(re.search(r"\bUS\$|\bUSD\b", text, flags=re.I))
    has_decimal = bool(re.search(r"\d[\d,]*\.\d+", text))
    amounts = _extract_num_list(text, is_us, has_decimal)

    if not amounts:
        for l in text.splitlines():
            l = l.strip()
            if re.fullmatch(r"[\d,]{3,}", l):
                val = int(l.replace(",", ""))
                amounts.append(round(val * USD_TO_TWD) if is_us else val)

    if not amounts:
        return None

    price = min(amounts)
    orig = max(amounts) if max(amounts) > price else None

    strike_amounts = _extract_num_list(raw.get("strikeText", ""), is_us, has_decimal)
    if strike_amounts:
        max_strike = max(strike_amounts)
        if max_strike > price:
            orig = max_strike

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    names = [l for l in lines if not re.search(r"\$|NT|USD|TWD|折|^[\d,\.]+$", l, flags=re.I)]
    name = max(names, key=len) if names else (raw.get("alt") or "")
    return {"name": name or raw.get("alt", ""), "price": price, "orig_price": orig}


def extract_items(page, category, api_price_map=None):
    out = {}
    api_price_map = api_price_map or {}
    for raw in page.evaluate(JS_CARDS, SEL_ITEM):
        m = re.search(ID_REGEX, raw["href"])
        if not m:
            continue
        pid = m.group(1)
        info = parse_card(raw)

        api_info = api_price_map.get(pid)
        if api_info and api_info.get("price"):
            price = api_info["price"]
            orig_price = api_info.get("orig_price")
            name = info["name"] if info and info.get("name") else (raw.get("alt") or "")
        else:
            if not info:
                continue
            price = info["price"]
            orig_price = info["orig_price"]
            name = info["name"]

        out[pid] = {
            "sold": bool(raw.get("sold")) or bool(SOLD_RE.search(raw["text"])),
            "id": pid,
            "name": name,
            "price": price,
            "orig_price": orig_price,
            "url": urljoin(BASE, raw["href"]),
            "img": urljoin(BASE, raw["img"]) if raw["img"] else "",
            "category": category,
        }
    return list(out.values())


def clean_url(url):
    if url.count("https://") > 1:
        url = "https://" + url.split("https://")[-1]
    return url


def crawl(debug=False):
    items = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="zh-TW",
            timezone_id="Asia/Taipei",
            geolocation={"latitude": 25.0330, "longitude": 121.5654},
            permissions=["geolocation"],
            extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        )
        page = ctx.new_page()
        api_price_map = {}

        def _on_response(r):
            try:
                ct = r.headers.get("content-type") or ""
                if "json" in ct:
                    _extract_prices_from_json(r.json(), api_price_map)
            except Exception:
                pass

        page.on("response", _on_response)

        for cat, url in CATEGORY_URLS.items():
            url = clean_url(url)
            for pg in range(1, MAX_PAGES + 1):
                sep = "&" if "?" in url else "?"
                page.goto(f"{url}{sep}{PAGE_PARAM}={pg}", wait_until="networkidle", timeout=60000)
                scroll_to_bottom(page)
                got = extract_items(page, cat, api_price_map=api_price_map)
                total = len(got)

                if BAG_ONLY:
                    got = [i for i in got if is_bag(i["name"])]

                if TARGET_BRANDS:
                    got = [i for i in got if is_target_brand(i["name"])]

                sold_n = sum(1 for i in got if i["sold"])
                if not (got and sold_n / len(got) > 0.6):
                    got = [i for i in got if not i["sold"]]

                fresh = [i for i in got if i["id"] not in items or cat not in items[i["id"]]["tags"]]
                print(f"[{cat}] page {pg}: 共 {total} 件，保留 {len(got)} 件")
                if not got or not fresh:
                    break

                for it in got:
                    if it["id"] in items:
                        items[it["id"]]["tags"].add(cat)
                        if it["orig_price"]:
                            items[it["id"]].update(price=it["price"], orig_price=it["orig_price"])
                    else:
                        it["tags"] = {cat}
                        items[it["id"]] = it
                time.sleep(1.2)
        browser.close()
    return items


def _post_tg(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": str(chat_id).strip(),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API 回傳失敗: {body}")


def send_tg_notification(new_items, drop_items, test_mode=False):
    bot_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print("未設定 TG_BOT_TOKEN 或 TG_CHAT_ID，跳過 Telegram 通知。")
        return

    messages = []
    if test_mode:
        messages.append(
            "🧪 <b>【2nd STREET 機器人串接測試】</b>\n"
            "• 品牌：LOUIS VUITTON - NT$18,000\n"
            "  <a href='https://store.2ndstreet.com.tw'>測試商品連結</a>"
        )

    if new_items:
        msg = "✨ <b>【2nd STREET 包包新上架通知】</b>\n"
        for item in new_items[:8]:
            b = html.escape(str(item.get("brand", "品牌")))
            n = html.escape(str(item.get("name", "未命名商品")))
            u = html.escape(str(item.get("url", BASE)))
            p = int(item.get("price") or 0)
            msg += f"• <b>{b}</b> - NT${p:,}\n  <a href='{u}'>{n}</a>\n"
        messages.append(msg)

    if drop_items:
        msg = "🔥 <b>【2nd STREET 包包降價通知】</b>\n"
        for item in drop_items[:8]:
            b = html.escape(str(item.get("brand", "品牌")))
            n = html.escape(str(item.get("name", "未命名商品")))
            u = html.escape(str(item.get("url", BASE)))
            p = int(item.get("price") or 0)
            orig = item.get("orig_price")
            orig_str = f"NT${int(orig):,}" if orig else "---"
            msg += f"• <b>{b}</b> - <b>NT${p:,}</b> (原價: {orig_str})\n  <a href='{u}'>{n}</a>\n"
        messages.append(msg)

    for text in messages:
        try:
            _post_tg(bot_token, chat_id, text)
            print("Telegram 通知已發送。")
        except Exception as e:
            print("Telegram 通知發送失敗:", e)


def merge_with_previous(current):
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
        if TARGET_BRANDS:
            prev = {k: v for k, v in prev.items() if is_target_brand(v.get("name", ""))}

    if prev and len(current) < len(prev) * MIN_KEEP_RATIO:
        sys.exit("異常：本次抓取筆數過少，中止覆蓋資料。")

    now_dt = datetime.now(timezone.utc).replace(microsecond=0)
    now = now_dt.isoformat()
    result = []

    notify_new = []
    notify_drop = []

    for pid, it in current.items():
        old = prev.get(pid)
        it["tags"] = sorted(it["tags"])

        is_sale_cat = "sale" in it["tags"]
        has_orig_drop = bool(it["orig_price"] and it["orig_price"] > it["price"])

        if old is None:
            it["first_seen"] = now if prev else "1970-01-01T00:00:00+00:00"
            it["is_new"] = (bool(prev) or "new" in it["tags"]) and not is_sale_cat
            it["price_history"] = [{"t": now, "p": it["price"]}]
            it["price_dropped"] = is_sale_cat or has_orig_drop

            if prev and it["is_new"]:
                notify_new.append(it)
            elif prev and it["price_dropped"]:
                notify_drop.append(it)
        else:
            it["first_seen"] = old["first_seen"]
            hist = old.get("price_history", [])
            if not hist or hist[-1]["p"] != it["price"]:
                hist.append({"t": now, "p": it["price"]})
            it["price_history"] = hist
            age = now_dt - datetime.fromisoformat(old["first_seen"])
            is_hist_drop = it["price"] < old["price"]

            it["price_dropped"] = is_sale_cat or has_orig_drop or is_hist_drop
            it["is_new"] = (age.days < NEW_DAYS or "new" in it["tags"]) and not it["price_dropped"]

            if is_hist_drop:
                notify_drop.append(it)

        it["brand"] = brand_of(it["name"])
        it["title"] = it["name"]
        it["image"] = it["img"]
        it["is_price_drop"] = it["price_dropped"]
        result.append(it)

    send_tg_notification(notify_new, notify_drop, test_mode=False)
    return result, now


def main():
    if "--test-tg" in sys.argv:
        send_tg_notification([], [], test_mode=True)
        return

    debug = "--debug" in sys.argv
    current = crawl(debug)
    if not current:
        sys.exit("一筆都沒抓到。")

    items, now = merge_with_previous(current)
    DATA_FILE.write_text(
        json.dumps({"updated": now, "items": items}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"完成：{len(items)} 筆商品更新。")


if __name__ == "__main__":
    main()
