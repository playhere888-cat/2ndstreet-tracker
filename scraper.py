import json
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

TARGET_URLS = [
    "https://store.2ndstreet.com.tw/v2/Search?q=%E5%8C%85%E5%8C%85&shopId=41320",
    "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886",
]


def run():
    results = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for url in TARGET_URLS:
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)

                # 下滾幾次讓動態圖片與商品資料完全載入
                for _ in range(5):
                    page.mouse.wheel(0, 1500)
                    page.wait_for_timeout(1500)

                # 尋找所有包含 SalePage 連結的商品卡片元素
                links = page.locator("a[href*='SalePage']").all()

                for link in links:
                    try:
                        href = link.get_attribute("href") or ""
                        if not href or href in seen_urls:
                            continue

                        full_url = (
                            href
                            if href.startswith("http")
                            else f"https://store.2ndstreet.com.tw{href}"
                        )

                        # 取得文字內容（包含品名與價格）
                        raw_text = " ".join(
                            (link.inner_text() or "").split()
                        )

                        # 提取商品圖片網址
                        img_src = ""
                        img_el = link.locator("img").first
                        if img_el.count() > 0:
                            img_src = (
                                img_el.get_attribute("src")
                                or img_el.get_attribute("data-src")
                                or img_el.get_attribute("data-original")
                                or ""
                            )

                        if img_src.startswith("//"):
                            img_src = "https:" + img_src

                        # 如果有圖片或連結結構符合商品，即存入結果
                        if img_src or len(raw_text) > 3:
                            seen_urls.add(href)
                            title_display = (
                                raw_text if raw_text else "2nd STREET 包包商品"
                            )

                            results.append({
                                "title": title_display,
                                "url": full_url,
                                "image": img_src,
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

    print(f"完成！共抓取 {len(results)} 筆商品圖文資料")


if __name__ == "__main__":
    run()
