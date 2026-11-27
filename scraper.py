import json
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

TARGET_URLS = [
    ("https://store.2ndstreet.com.tw/v2/Search?q=%E5%8C%85%E5%8C%85&shopId=41320", False),
    ("https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886", False),
    ("https://store.2ndstreet.com.tw/v2/Search?q=%E6%96%B0%E4%B8%8A%E6%9E%B6&shopId=41320", True),
]

SALE_PAGE_RE = re.compile(r"/SalePage/Index/(\d+)")


def normalize_product_url(href):
    if not href:
        return ""
    m = SALE_PAGE_RE.search(href)
    if not m:
        return ""
    return f"https://store.2ndstreet.com.tw/SalePage/Index/{m.group(1)}"


def pick_image(container):
    imgs = container.locator("img").all()
    for img in imgs:
        for attr in ["src", "data-src", "data-original", "data-lazy-src"]:
            val = (img.get_attribute(attr) or "").strip()
            if (
                val
                and not val.startswith("data:image")
                and "placeholder" not in val.lower()
                and "logo" not in val.lower()
                and "icon" not in val.lower()
            ):
                if val.startswith("//"):
                    val = "https:" + val
                elif val.startswith("/"):
                    val = "https://store.2ndstreet.com.tw" + val
                return val
        srcset = (img.get_attribute("srcset") or "").strip()
        if srcset:
            first_url = srcset.split(",")[0].strip().split(" ")[0]
            if first_url:
                return "https:" + first_url if first_url.startswith("//") else first_url
    return ""


def clean_title(raw):
    text = " ".join((raw or "").split())
    text = re.sub(r"\b(Previous|Next|Prev)\b", "", text, flags=re.I)
    text = " ".join(text.split()).strip()

    # 若同一段文字重複出現多次，只保留第一段
    parts = [p.strip() for p in re.split(r"(?=【[^】]+】)", text) if p.strip()]
    if len(parts) > 1:
        text = parts[0]

    if len(re.findall(r"(?:NT\$|US\$|\$)\s*\d", text)) > 2:
        return ""
    if len(text) > 140:
        return text[:140]
    return text


def get_single_card_container(link):
    # 只找包含 1 個 SalePage 連結的最小合理容器，避免抓到整個輪播列
    for xpath in ["xpath=..", "xpath=../.."]:
        try:
            parent = link.locator(xpath).first
            if parent.count() > 0:
                sale_links_cnt = parent.locator("a[href*='/SalePage/Index/']").count()
                txt = " ".join((parent.inner_text() or "").split())
                if sale_links_cnt == 1 and 0 < len(txt) <= 220:
                    return parent
        except Exception:
            continue
    return link


def collect_hints(container):
    hints = []
    try:
        raw_list = container.evaluate(
            """(el) => {
                const out = [];
                const nodes = [el, ...el.querySelectorAll('*')].slice(0, 35);
                for (const n of nodes) {
                    for (const p of ['::before', '::after']) {
                        const c = getComputedStyle(n, p).content;
                        if (c && c !== 'none' && c !== 'normal') {
                            out.push(c.replace(/^['"]|['"]$/g, ''));
                        }
                    }
                    for (const a of ['alt', 'title', 'aria-label', 'data-tag', 'data-label', 'data-status']) {
                        const v = n.getAttribute && n.getAttribute(a);
                        if (v) out.push(v);
                    }
                    const cls = n.getAttribute && n.getAttribute('class');
                    if (cls && /(new|badge|tag|drop|sale|price)/i.test(cls)) {
                        out.push(cls);
                    }
                }
                return out;
            }"""
        )
        for item in raw_list or []:
            s = str(item).strip()
            if s and len(s) <= 80:
                hints.append(s)
    except Exception:
        pass
    return " ".join(hints)


def run():
    results = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        )
        page = context.new_page()

        for url, from_new_search in TARGET_URLS:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                for _ in range(6):
                    page.mouse.wheel(0, 1500)
                    page.wait_for_timeout(1500)

                links = page.locator("a[href*='/SalePage/Index/']").all()

                for link in links:
                    try:
                        href = (link.get_attribute("href") or "").strip()
                        full_url = normalize_product_url(href)
                        if not full_url or full_url in seen_urls:
                            continue

                        card_el = get_single_card_container(link)
                        card_text = " ".join((card_el.inner_text() or "").split())
                        title = clean_title(link.inner_text()) or clean_title(card_text)
                        img_src = pick_image(card_el)
                        hints = collect_hints(card_el)

                        if "已售完" in title or "已售完" in card_text:
                            continue

                        if not title and not img_src:
                            continue

                        seen_urls.add(full_url)
                        pool = f"{title} {card_text} {hints}".lower()
                        is_new = (
                            from_new_search
                            or ("新上架" in pool)
                            or bool(re.search(r"\bnew\b", pool))
                        )
                        is_price_drop = ("已降價" in pool) or ("降價" in pool)

                        results.append({
                            "title": title if title else "2nd STREET 商品",
                            "url": full_url,
                            "image": img_src,
                            "is_new": is_new,
                            "is_price_drop": is_price_drop,
                        })
                    except Exception:
                        continue

            except Exception as e:
                print(f"抓取 {url} 失敗: {e}")

        browser.close()

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_items": len(results),
        "items": results,
    }

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    new_cnt = sum(1 for x in results if x["is_new"])
    drop_cnt = sum(1 for x in results if x["is_price_drop"])
    print(f"完成！共 {len(results)} 件（新上架 {new_cnt} 件、已降價 {
