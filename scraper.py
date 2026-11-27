import json
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

# 2nd STREET 官方「包包類」與「最新上架包包」分類頁面 URL
TARGET_URLS = [
    "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886",  # 包包/配件分類
    "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886?o=0&sortType=1",  # 包包類-最新上架排序
    "https://store.2ndstreet.com.tw/v2/Search?q=%E5%8C%85%E5%8C%85&shopId=41320",
]

SALE_PAGE_RE = re.compile(r"/SalePage/Index/(\d+)")

# 排除非包包類的關鍵字（過濾服飾、鞋類、配件）
EXCLUDE_KEYWORDS = ["上身", "外套", "褲", "裙", "鞋", "襯衫", "T恤", "套裝", "帽子", "皮帶", "飾品", "戒指", "項鍊"]


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
            if sale_links_cnt <= 1 and 0 < len(txt) <= 300:
                best = parent
            elif sale_links_cnt > 1:
                break
        except Exception:
            continue
    return best


def is_bag_item(title, card_text):
    combined = f"{title} {card_text}"
    # 如果包含明確非包包關鍵字，排除
    for kw in EXCLUDE_KEYWORDS:
        if kw in combined:
            return False
    return True


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
                        card_text = " ".join((card_el.inner_text() or "").split())
                        title = clean_title(link.inner_text()) or clean_title(card_text)
                        img_src = pick_image(card_el)

                        # 排除已售完與非包包品項
                        if "已售完" in card_text or "已售完" in title:
                            continue
                        if not is_bag_item(title, card_text):
                            continue
                        if not title and not img_src:
                            continue

                        # 判定網站真實標示：必須卡片文字中真有「新上架」或「已降價」
                        is_new = "新上架" in card_text
                        is_price_drop = ("已降價" in card_text) or ("降價" in card_text)

                        # 只保留網站上有標示「新上架」或「已降價」的包包
                        if not (is_new or is_price_drop):
                            continue

                        seen_urls.add(full_url)
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
    print(f"完成！共篩選出 {len(results)} 件包包（新上架 {new_cnt} 件、已降價 {drop_cnt} 件）")


if __name__ == "__main__":
    run()
