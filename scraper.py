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

                # 往下滾動載入更多商品圖片
                for _ in range(4):
                    page.mouse.wheel(0, 1500)
                    page.wait_for_timeout(1500)

                # 尋找所有商品區塊（包含圖、文、價、連結）
                cards = page.locator("a").all()

                for card in cards:
                    try:
                        href = card.get_attribute("href") or ""
                        text = " ".join((card.inner_text() or "").split())

                        # 過濾無效連結與重複商品
                        if not href or href in seen_urls:
                            continue

                        # 找尋商品圖片
                        img = card.locator("img").first
                        img_src = ""
                        if img.count() > 0:
                            img_src = (
                                img.get_attribute("src")
                                or img.get_attribute("data-src")
                                or ""
                            )

                        # 如果有圖片或明確包含商品資訊
                        if img_src and (
                            "NT$" in text
                             or "$" in text
                             or "包" in text.lower()
                        ):
                            seen_urls.add(href)
                            full_url = (
                                href
                                if href.startswith("http")
                                else f"https://store.2ndstreet.com.tw{href}"
                            )
                            if img_src.startswith("//"):
                                img_src = "https:" + img_src

                            results.append({
                                "title": text if text else "2nd STREET 精選包包",
                                "url": full_url,
                                "image": img_src,
                            })
                    except Exception:
                        continue

            except Exception as e:
                print(f"抓取失敗: {e}")

        browser.close()

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_items": len(results),
        "items": results,
    }

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"完成！共抓取 {len(results)} 筆圖文商品資料")


if __name__ == "__main__":
    run()
