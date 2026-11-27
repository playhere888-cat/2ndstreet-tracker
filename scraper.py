import json
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

TARGET_URLS = [
    "https://store.2ndstreet.com.tw/v2/Search?q=%E5%8C%85%E5%8C%85&shopId=41320",
    "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886",
]


def inspect_images(container):
    best_img = ""
    img_hints = []
    imgs = container.locator("img").all()
    for img in imgs:
        alt = (img.get_attribute("alt") or "").strip()
        cls = (img.get_attribute("class") or "").strip()
        if alt:
            img_hints.append(alt)
        if cls:
            img_hints.append(cls)

        picked = ""
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
                picked = val
                break

        if not picked:
            srcset = (img.get_attribute("srcset") or "").strip()
            if srcset:
                first_url = srcset.split(",")[0].strip().split(" ")[0]
                if first_url:
                    picked = "https:" + first_url if first_url.startswith("//") else first_url

        if picked:
            img_hints.append(picked)
            if not best_img:
                best_img = picked

    return best_img, " ".join(img_hints)


def clean_title(raw):
    text = " ".join((raw or "").split())
    if len(re.findall(r"(?:NT\$|US\$|\$)\s*\d", text)) > 2:
        return ""
    if len(text) > 180:
        return ""
    return text


def get_card_context(link):
    for xpath in [
        "xpath=ancestor::*[self::li or self::article or contains(@class,'card') or contains(@class,'item') or contains(@class,'product')][1]",
        "xpath=..",
    ]:
        try:
            parent = link.locator(xpath).first
            if parent.count() > 0:
                txt = " ".join((parent.inner_text() or "").split())
                cls = (parent.get_attribute("class") or "").strip()
                if 0 < len(txt) <= 260:
                    return parent, f"{txt} {cls}"
        except Exception:
            continue
    return link, " ".join((link.inner_text() or "").split())


def run():
    results = []
    seen_urls = set()

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
                        if not href:
                            continue

                        full_url = (
                            href
                            if href.startswith("http")
                            else f"https://store.2ndstreet.com.tw{href.split('?')[0]}"
                        )
                        if full_url in seen_urls:
                            continue

                        card_el, card_text = get_card_context(link)
                        title = clean_title(link.inner_text()) or clean_title(card_text)
                        img_src, img_hints = inspect_images(card_el)

                        if not title and not img_src:
                            continue

                        seen_urls.add(full_url)
                        combined = f"{title} {card_text} {img_hints}".lower()
                        is_new = (
                            ("新上架" in combined)
                            or ("最新" in combined)
                            or bool(re.search(r"\bnew\b", combined))
                            or ("/new" in combined)
                        )
                        is_price_drop = (
                            ("已降價" in combined)
                            or ("降價" in combined)
                            or ("sale" in combined)
                            or ("discount" in combined)
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
    for sample in results[:15]:
        print(
            f"- title={sample['title'][:40]} | new={sample['is_new']} | drop={sample['is_price_drop']} | img={sample['image'][:60]}"
        )


if __name__ == "__main__":
    run()
