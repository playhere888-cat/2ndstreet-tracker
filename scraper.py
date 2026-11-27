import json
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

# 設定抓取目標：包包類別與新上架搜尋
TARGET_URLS = [
    "https://store.2ndstreet.com.tw/v2/Search?q=%E5%8C%85%E5%8C%85&shopId=41320",  # 搜尋「包包」
    "https://store.2ndstreet.com.tw/v2/official/SalePageCategory/467886",  # 男包/女包分類頁
]


def run():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for url in TARGET_URLS:
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)

                for _ in range(3):
                    page.mouse.wheel(0, 1200)
                    page.wait_for_timeout(1500)

                links = page.locator("a").all()
                seen = set()
                for item in links:
                    try:
                        href = item.get_attribute("href") or ""
                        text = " ".join((item.inner_text() or "").split())
                        if not text or not href:
                            continue
                        if len(text) >= 4 and href not in seen:
                            seen.add(href)
                            full_url = (
                                href
                                if href.startswith("http")
                                else f"https://store.2ndstreet.com.tw{href}"
                            )
                            results.append({
                                "source_url": url,
                                "title_or_text": text[:200],
                                "url": full_url,
                            })
                    except Exception:
                        continue
            except Exception as e:
                print(f"抓取 {url} 時發生錯誤: {e}")

        browser.close()

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_items": len(results),
        "items": results,
    }

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"抓取完成，共收集 {len(results)} 筆包包資料")


if __name__ == "__main__":
    run()
