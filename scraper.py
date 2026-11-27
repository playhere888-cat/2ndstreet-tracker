import json
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

TARGET_URLS = [
    "https://store.2ndstreet.com.tw/v2/Search?q=Supreme&shopId=41320",
    "https://store.2ndstreet.com.tw/tagCategory/supreme",
]


def run():
    results = []
    seen_urls = set()

    with sync_playwright() as p:
        # 開啟瀏覽器
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for target_url in TARGET_URLS:
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)

                # 模擬向下滾動以載入動態商品
                for _ in range(5):
                    page.mouse.wheel(0, 1500)
                    page.wait_for_timeout(1500)

                # 針對 2nd STREET 商品卡片進行精準定位
                # 抓取包含商品頁連結的卡片區塊
                product_cards = page.locator("a[href*='/v2/official/SalePageCategory/']").all()

                if not product_cards:
                    # 備用選擇器：抓取一般商品頁連結
                    product_cards = page.locator("a[href*='SalePage']").all()

                for card in product_cards:
                    try:
                        href = card.get_attribute("href") or ""
                        if not href or href in seen_urls:
                            continue

                        # 取得卡片內的完整文字（包含品名與價格）
                        text_content = " ".join((card.inner_text() or "").split())

                        # 只保留有效商品（有文字且長度足夠）
                        if len(text_content) > 5 and ("NT$" in text_content or "元" in text_content or "Supreme" in text_content or "supreme" in text_content):
                            seen_urls.add(href)
                            full_url = href if href.startswith("http") else f"https://store.2ndstreet.com.tw{href}"

                            results.append({
                                "title": text_content,
                                "url": full_url,
                                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                            })
                    except Exception:
                        continue
            except Exception as e:
                print(f"抓取 {target_url} 時發生錯誤: {e}")
                continue

        browser.close()

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_items": len(results),
        "items": results
    }

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"抓取完成，成功收集到 {len(results)} 項商品資料")


if __name__ == "__main__":
    run()
