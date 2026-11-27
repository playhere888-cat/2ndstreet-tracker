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
    # 往上找最多 4 層，只要只有 1 個 SalePage 連結且包含商品資訊，就當作單張商品卡
    best = link
    for xpath in ["xpath=..", "xpath=../..", "xpath=../../..", "xpath=../../../.."]:
        try:
            parent = link.locator(xpath).first
            if parent.count() == 0:
                continue
            sale_links_cnt = parent.locator("a[href*='/SalePage/Index/']").count()
            txt = " ".join((parent.inner_text() or "").split())
            if sale_links_cnt == 1 and 0 < len(txt) <= 260:
                best = parent
            elif sale_links_cnt > 1:
                break
        except Exception:
            continue
    return best


def extract_card_details(container):
    try:
        info = container.evaluate(
            """(el) => {
                const texts = [];
                const alts = [];
                const allEls = [el, ...el.querySelectorAll('*')].slice(0, 50);
                for (const node of allEls) {
                    if (node.tagName === 'IMG') {
                        const a = node.getAttribute('alt');
                        if (a) alts.push(a.trim());
                    }
                    const directText = Array.from(node.childNodes || [])
                        .filter(c => c.nodeType === Node.TEXT_NODE)
                        .map(c => c.textContent.trim())
                        .filter(Boolean)
                        .join(' ');
                    if (directText) texts.push(directText);
                }
                return {
                    fullText: (el.innerText || '').trim(),
                    parts: texts,
                    alts: alts
                };
            }"""
        )
        full_text = " ".join((info.get("fullText") or "").split())
        parts_text = " ".join(info.get("parts") or [])
        alts_text = " ".join(info.get("alts") or [])
        return full_text, f"{full_text} {parts_text} {alts_text}"
    except Exception:
        txt = " ".join((container.inner_text() or "").split())
        return txt, txt


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

                for _ in range(7):
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
                        card_text, combined_text = extract_card_details(card_el)
                        title = clean_title(link.inner_text()) or clean_title(card_text)
                        img_src = pick_image(card_el)

                        if "已售完" in card_text or "已售完" in title:
                            continue

                        if not title and not img_src:
                            continue

                        seen_urls.add(full_url)
                        lower_all = combined_text.lower()

                        is_new = (
                            from_new_search
                            or ("新上架" in combined_text)
                            or bool(re.search(r"\bnew\b", lower_all))
                        )
                        is_price_drop = (
                            ("已降價" in combined_text)
                            or ("降價" in combined_text)
                        )

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
    for sample in results[:10]:
        print(
            f"- title={sample['title'][:40]} | new={sample['is_new']} | drop={sample['is_price_drop']}"
        )


if __name__ == "__main__":
    run()
