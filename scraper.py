import json
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

TARGET_URLS = [
    "https://store.2ndstreet.com.tw/v2/Search?q=%E5%8C%85%E5%8C%85&shopId=41320",
    "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886",
    "https://store.2ndstreet.com.tw/v2/Search?q=%E6%96%B0%E4%B8%8A%E6%9E%B6&shopId=41320",
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
    text = re.sub(r"^(Previous\s+Next|Prev\s+Next)\s*", "", text, flags=re.I).strip()
    if len(re.findall(r"(?:NT\$|US\$|\$)\s*\d", text)) > 2:
        return ""
    if len(text) > 180:
        return ""
    return text


def get_card_container(link):
    for xpath in [
        "xpath=ancestor::*[self::li or self::article or contains(@class,'card') or contains(@class,'item') or contains(@class,'product')][1]",
        "xpath=../..",
        "xpath=..",
    ]:
        try:
            parent = link.locator(xpath).first
            if parent.count() > 0:
                txt = " ".join((parent.inner_text() or "").split())
                if 0 < len(txt) <= 300:
                    return parent
        except Exception:
            continue
    return link


def collect_pseudo_and_attr_hints(page, container):
    try:
        hints = container.evaluate(
            """(el) => {
                const out = [];
                const targets = [el, ...el.querySelectorAll('*')].slice(0, 40);
                for (const node of targets) {
                    for (const pseudo of ['::before', '::after']) {
                        const c = getComputedStyle(node, pseudo).content;
                        if (c && c !== 'none' && c !== 'normal') {
                            out.push(c.replace(/^['"]|['"]$/g, ''));
                        }
                    }
                    for (const attr of ['aria-label', 'title', 'data-tag', 'data-label', 'data-status']) {
                        const v = node.getAttribute && node.getAttribute(attr);
                        if (v) out.push(v);
                    }
                }
                return out;
            }"""
        )
        return " ".join([str(x).strip() for x in (hints or []) if str(x).strip()])
    except Exception:
        return ""


def collect_visible_badges(container):
    badge_texts = []
    for img in container.locator("img").all():
        alt = (img.get_attribute("alt") or "").strip()
        if alt and len(alt) <= 25:
            badge_texts.append(alt)
    for el in container.locator("span, div, p, em, strong").all()[:25]:
        try:
            txt = " ".join((el.inner_text() or "").split())
            if 0 < len(txt) <= 15:
                badge_texts.append(txt)
        except Exception:
            continue
    return " ".join(badge_texts)


def run():
    results = []
    seen_urls = set()
    debug_hints = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        )
        page = context.new_page()

        for url in TARGET_URLS:
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

                        card_el = get_card_container(link)
                        card_text = " ".join((card_el.inner_text() or "").split())
                        title = clean_title(link.inner_text()) or clean_title(card_text)
                        img_src = pick_image(card_el)
                        badges_text = collect_visible_badges(card_el)
                        pseudo_hints = collect_pseudo_and_attr_hints(page, card_el)

                        if "已售完" in title or "已售完" in card_text:
                            continue

                        if not title and not img_src:
                            continue

                        seen_urls.add(full_url)
                        combined_hints = f"{badges_text} {pseudo_hints}".strip()
                        if len(debug_hints) < 20 and combined_hints:
                            debug_hints.append(combined_hints[:80])

                        badge_pool = f"{title} {combined_hints}".lower()
                        is_new = ("新上架" in badge_pool) or bool(re.search(r"\bnew\b", badge_pool))
                        is_price_drop = ("已降價" in badge_pool) or ("降價" in badge_pool)

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
    print(f"完成！共 {len(results)} 件（新上架 {new_cnt} 件、已降價 {drop_cnt} 件）")
    print("標籤/偽元素樣本：", debug_hints[:10])


if __name__ == "__main__":
    run()
