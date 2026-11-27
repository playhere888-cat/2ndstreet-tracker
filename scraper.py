import json
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

TARGET_URLS = [
    "https://store.2ndstreet.com.tw/v2/Search?q=%E5%8C%85%E5%8C%85&shopId=41320",
    "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886",
    "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886?o=0&sortType=1",
]

SALE_PAGE_RE = re.compile(r"/SalePage/Index/(\d+)")

# 明確非包包的服飾/鞋履關鍵字（避免把「背包/手提包/側背包」誤殺，只抓明顯衣物鞋款）
STRICT_NON_BAG_RE = re.compile(
    r"(?:T恤|襯衫|上身|外套|大衣|針織衫|毛衣|衛衣|連身裙|短裙|長褲|牛仔褲|短褲|運動鞋|休閒鞋|高跟鞋|涼鞋|靴子|拖鞋)"
)


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
    text = re.sub(r"^(已降價\s*↓?|新上架)\s*", "", text).strip()
    text = " ".join(text.split()).strip()

    parts = [p.strip() for p in re.split(r"(?=【[^】]+】)", text) if p.strip()]
    if len(parts) > 1:
        text = parts[0]

    if len(re.findall(r"(?:NT\$|US\$|\$)\s*\d", text)) > 3:
        return ""
    if len(text) > 150:
        return text[:150]
    return text


def get_single_card_container(link):
    best = link
    for xpath in ["xpath=..", "xpath=../..", "xpath=../../..", "xpath=../../../.."]:
        try:
            parent = link.locator(xpath).first
            if parent.count() == 0:
                continue
            sale_links_cnt = parent.locator("a[href*='/SalePage/Index/']").count()
            txt = " ".join((parent.inner_text() or "").split())
            if sale_links_cnt <= 1 and 0 < len(txt) <= 320:
                best = parent
            elif sale_links_cnt > 1:
                break
        except Exception:
            continue
    return best


def inspect_card(container):
    try:
        data = container.evaluate(
            """(el) => {
                const imgs = Array.from(el.querySelectorAll('img')).map(im => ({
                    alt: im.getAttribute('alt') || '',
                    src: im.getAttribute('src') || im.getAttribute('data-src') || '',
                    cls: im.getAttribute('class') || ''
                }));
                const allText = (el.innerText || '').trim();
                const html = (el.outerHTML || '').slice(0, 600);
                return { allText, imgs, html };
            }"""
        )
        all_text = " ".join((data.get("allText") or "").split())
        img_info = " ".join(
            f"{x.get('alt','')} {x.get('cls','')} {x.get('src','')}"
            for x in (data.get("imgs") or [])
        )
        return all_text, f"{all_text} {img_info}", data.get("html") or ""
    except Exception:
        txt = " ".join((container.inner_text() or "").split())
        return txt, txt, ""


def run():
    results = []
    seen_urls = set()
    samples_printed = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        )
        page = context.new_page()

        for url in TARGET_URLS:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)

                for _ in range(8):
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
                        card_text, combined, html_snip = inspect_card(card_el)
                        title = clean_title(link.inner_text()) or clean_title(card_text)
                        img_src = pick_image(card_el)

                        if "已售完" in card_text or "已售完" in title:
                            continue
                        if STRICT_NON_BAG_RE.search(f"{title} {card_text}"):
                            continue
                        if not title and not img_src:
                            continue

                        seen_urls.add(full_url)

                        is_new = ("新上架" in combined) or bool(re.search(r"\bnew\b", combined.lower()))
                        is_price_drop = ("已降價" in combined) or ("降價" in combined)

                        if samples_printed < 5:
                            samples_printed += 1
                            print(f"[SAMPLE {samples_printed}] title={title[:40]} | new={is_new} | drop={is_price_drop} | text={card_text[:90]}")

                        results.append({
                            "title": title if title else "2nd STREET 包包",
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


if __name__ == "__main__":
    run()
