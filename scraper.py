import json
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

TARGET_URLS = [
    "https://store.2ndstreet.com.tw/v2/Search?q=%E5%8C%85%E5%8C%85&shopId=41320",
    "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886",
]


def pick_image(link):
    imgs = link.locator("img").all()
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
                if first_url.startswith("//"):
                    first_url = "https:" + first_url
                return first_url
    return ""


def clean_text(raw):
    text = " ".join((raw or "").split())
    # 若出現多個價格或多個「查看商品/加入購物車」，代表包了多筆，不當單一商品
    if len(re.findall(r"(?:NT\$|US\$|\$)\s*\d", text)) > 2:
        return ""
    if len(text) > 180:
        return ""
    return text


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

                        title = clean_text(link.inner_text())
                        img_src = pick_image(link)

                        # 必須有單一商品文字或圖片，且不能是整頁大區塊
                        if not title and not img_src:
                            continue

                        seen_urls.add(full_url)
                        is_new = ("新上架" in title) or ("NEW" in title.upper())
                        is_price_drop = ("已降價" in title) or ("降價" in title)

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

    print(f"完成！共抓取 {len(results)} 件獨立商品")


if __name__ == "__main__":
    run()
