"""
Scrydex Pokemon Card Scraper
Sử dụng Playwright để cào toàn bộ dữ liệu chi tiết thẻ bài Pokemon.
Hỗ trợ phân trang, trích xuất giá, lịch sử giá, attacks, abilities.

Cách dùng:
  python scrape_scrydex.py <expansion_url>
  python scrape_scrydex.py https://scrydex.com/pokemon/expansions/perfect-order/me3
"""

import json, re, sys, time, random, os
from datetime import datetime
from playwright.sync_api import sync_playwright

__all__ = ["create_browser", "get_card_links", "scrape_card_detail", "random_delay"]

# --- Config ---
DELAY_MIN = 1.0  # Giây chờ tối thiểu giữa các request (đã giảm)
DELAY_MAX = 2.0  # Giây chờ tối đa (đã giảm)

# Cập nhật OUTPUT_DIR trỏ thẳng vào thư mục data của project
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "data"))

def random_delay():
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

def create_browser(playwright, headless=None):
    """Tạo browser + context. headless đọc từ env nếu không truyền."""
    if headless is None:
        headless = os.environ.get("SCRAPER_HEADLESS", "true").lower() == "true"
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800}
    )
    return browser, context

def get_card_links(page, expansion_url):
    """Lấy tất cả link thẻ từ trang expansion (xử lý phân trang)."""
    all_links = []
    current_url = expansion_url
    page_num = 0

    while current_url:
        page_num += 1
        print(f"  📄 Đang tải trang danh sách #{page_num}: {current_url}")
        page.goto(current_url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("#search_results_frame a", timeout=15000)
        except Exception:
            print(f"    ⚠️ Không tìm thấy thẻ nào trên trang {page_num}, kết thúc phân trang.")
            break
        time.sleep(3)  # Đợi Turbo Frame render xong (tăng lên 3s cho pagination)

        # Trích xuất link thẻ từ trang hiện tại
        links = page.evaluate("""() => {
            const cards = document.querySelectorAll('#search_results_frame a[href*="/pokemon/cards/"]');
            return Array.from(cards).map(a => {
                const spans = a.querySelectorAll('span');
                const priceSpan = a.querySelector('.font-bold');
                const trendIcon = a.querySelector('i[class*="fa-arrow"]');
                const card = a.querySelector('.card');
                return {
                    href: a.getAttribute('href'),
                    card_id: card?.getAttribute('data-id') || '',
                    name_number: spans[0]?.textContent?.trim() || '',
                    list_price: priceSpan?.textContent?.trim() || '',
                    trend: trendIcon?.className?.includes('down') ? 'down' : trendIcon?.className?.includes('up') ? 'up' : 'none',
                    image_url: a.querySelector('img')?.getAttribute('src') || ''
                };
            });
        }""")
        all_links.extend(links)
        print(f"    → Tìm thấy {len(links)} entries")

        # Kiểm tra nút trang tiếp theo (fix: hỗ trợ Turbo Frame pagination)
        next_url = page.evaluate("""() => {
            // 1. Thử rel="next" (chuẩn HTML)
            let nextBtn = document.querySelector('a[rel="next"]');
            if (nextBtn) return nextBtn.getAttribute('href');

            // 2. Tìm trong pagination container (Turbo Frame rendered)
            const paginationLinks = document.querySelectorAll('nav a, .pagination a, [aria-label="pagination"] a, .pagy a');
            for (const link of paginationLinks) {
                const rel = link.getAttribute('rel');
                if (rel === 'next') return link.getAttribute('href');
            }

            // 3. Tìm nút ">" hoặc "Next" hoặc aria-label="next"
            for (const link of paginationLinks) {
                const text = link.textContent.trim();
                const ariaLabel = (link.getAttribute('aria-label') || '').toLowerCase();
                if (text === '>' || text === '›' || text === '»' || text === 'Next'
                    || ariaLabel === 'next' || ariaLabel === 'next page') {
                    // Đảm bảo link không bị disabled
                    if (!link.closest('.disabled') && !link.classList.contains('disabled')
                        && link.getAttribute('href') && link.getAttribute('href') !== '#') {
                        return link.getAttribute('href');
                    }
                }
            }

            // 4. Tìm page number cao hơn trang hiện tại
            const currentPage = document.querySelector('nav .active, .pagination .active, .pagy .current');
            if (currentPage) {
                const currentNum = parseInt(currentPage.textContent.trim());
                if (!isNaN(currentNum)) {
                    for (const link of paginationLinks) {
                        const linkNum = parseInt(link.textContent.trim());
                        if (linkNum === currentNum + 1) {
                            return link.getAttribute('href');
                        }
                    }
                }
            }

            return null;
        }""")

        if next_url:
            current_url = f"https://scrydex.com{next_url}" if next_url.startswith("/") else next_url
            print(f"    → Phát hiện trang tiếp theo: {current_url}")
            random_delay()
        elif len(links) >= 250:
            # Fallback: nếu nhận đúng 250 items (page size của Scrydex),
            # rất có thể còn trang tiếp nhưng pagination chưa render
            next_page = page_num + 1
            if "?" in expansion_url:
                current_url = f"{expansion_url}&page={next_page}"
            else:
                current_url = f"{expansion_url}?page={next_page}"
            print(f"    ⚠️ Nhận đúng 250 entries, thử trang tiếp: {current_url}")
            random_delay()
        else:
            current_url = None

    print(f"  ✅ Tổng: {len(all_links)} card entries từ {page_num} trang")
    return all_links

def scrape_card_detail(page, card_url):
    """Cào toàn bộ dữ liệu chi tiết từ 1 trang thẻ bài."""
    full_url = f"https://scrydex.com{card_url}" if card_url.startswith("/") else card_url
    page.goto(full_url, wait_until="domcontentloaded")
    page.wait_for_selector("[data-controller='card']", timeout=15000)
    # Bỏ time.sleep() ở đây để tối ưu tốc độ full scrape

    data = page.evaluate("""() => {
        const result = {};

        // Helper: lấy text sạch (loại bỏ tooltip data-field ẩn)
        function cleanText(el) {
            if (!el) return '';
            const clone = el.cloneNode(true);
            clone.querySelectorAll('[data-field]').forEach(df => df.remove());
            return clone.textContent?.trim() || '';
        }

        // === Thông tin cơ bản ===
        result.page_title = document.title;
        result.url = window.location.href;

        // === Header info ===
        const bodyText = document.body.innerText;
        const hpMatch = bodyText.match(/HP\\s*(\\d+)/);
        result.hp = hpMatch ? hpMatch[1] : null;

        // === Variants ===
        const variantDivs = document.querySelectorAll('[data-card-target="variant"]');
        result.variants = Array.from(variantDivs).map(v => ({
            name: v.getAttribute('data-variant-name'),
            label: v.textContent.trim(),
            image: v.getAttribute('data-variant-image')
        }));

        // === Attacks ===
        result.attacks = [];
        const attackRows = document.querySelectorAll('table tbody tr');
        let currentAttack = null;
        attackRows.forEach(tr => {
            const cells = tr.querySelectorAll('td');
            if (cells.length >= 3) {
                // Hàng chính: cost | name | damage
                const costImgs = cells[0].querySelectorAll('img');
                const costTypes = Array.from(costImgs).map(img => {
                    const src = img.getAttribute('src') || '';
                    const match = src.match(/assets\\/([a-z]+)-/);
                    return match ? match[1] : 'unknown';
                });
                currentAttack = {
                    cost: costTypes,
                    name: cleanText(cells[1]),
                    damage: cleanText(cells[2]),
                    text: ''
                };
                result.attacks.push(currentAttack);
            } else if (cells.length === 1 && currentAttack) {
                // Hàng mô tả
                currentAttack.text = cleanText(cells[0]);
            }
        });

        // === Pricing (tất cả variants x companies x conditions) ===
        result.pricing = [];
        const containers = document.querySelectorAll('[data-prices-target="pricesContainer"]');
        containers.forEach(pc => {
            const company = pc.getAttribute('data-company');
            const variant = pc.getAttribute('data-variant');
            // Tìm các hàng giá (Near Mint, Lightly Played, etc.)
            const rows = pc.querySelectorAll('.flex.items-center.justify-between, .grid');
            const priceData = { company, variant, conditions: {} };

            // Parse text-based pricing
            const text = pc.innerText;
            const condMatches = text.matchAll(/(Near Mint|Lightly Played|Moderately Played|Heavily Played|Damaged)\\n\\$([\\d,.]+)(?:\\n\\$([\\d,.]+))?/g);
            for (const m of condMatches) {
                priceData.conditions[m[1]] = {
                    market: m[2] || null,
                    low: m[3] || null
                };
            }
            if (Object.keys(priceData.conditions).length > 0) {
                result.pricing.push(priceData);
            }
        });

        // === Price History (từ Chartkick scripts) ===
        result.price_history = [];
        const scripts = document.querySelectorAll('script');
        scripts.forEach(s => {
            const text = s.textContent;
            if (!text.includes('Chartkick')) return;
            const idMatch = text.match(/"([^"]+_history)"/);
            const dataMatch = text.match(/\\[\\{[^\\}]*"data":\\[\\[.*?\\]\\]\\}\\]/);
            if (idMatch && dataMatch) {
                try {
                    const chartData = JSON.parse(dataMatch[0]);
                    result.price_history.push({
                        chart_id: idMatch[1],
                        series: chartData
                    });
                } catch(e) {}
            }
        });

        // === Recent Sales Stats ===
        const salesMatch = bodyText.match(/TOTAL SALES\\n(\\d+)\\nSALES \\/ WEEK\\n(\\d+)\\nSALES \\/ MONTH\\n(\\d+)/);
        result.sales_stats = salesMatch ? {
            total: parseInt(salesMatch[1]),
            per_week: parseInt(salesMatch[2]),
            per_month: parseInt(salesMatch[3])
        } : null;

        // === Details section ===
        // Weakness
        const weakEl = document.querySelector('[data-field="weaknesses"]');
        if (weakEl) {
            // DOM: [value div] -> [data-field hidden] -> ...
            // weakEl = hidden overlay, previousElementSibling = actual value div
            const weakValueDiv = weakEl.previousElementSibling;
            const weakImg = weakValueDiv?.querySelector('img');
            const weakSrc = weakImg?.getAttribute('src') || '';
            const weakType = weakSrc.match(/assets\\/([a-z]+)-/);
            result.weakness = {
                type: weakType ? weakType[1] : null,
                value: weakValueDiv?.textContent?.replace(/\\s+/g, ' ')?.trim() || null
            };
        }

        // Retreat cost
        const retreatEl = document.querySelector('[data-field="retreat_cost"]');
        if (retreatEl) {
            const retreatParent = retreatEl.closest('div')?.previousElementSibling;
            const retreatImgs = retreatParent?.querySelectorAll('img') || [];
            result.retreat_cost = Array.from(retreatImgs).map(img => {
                const src = img.getAttribute('src') || '';
                const match = src.match(/assets\\/([a-z]+)-/);
                return match ? match[1] : 'unknown';
            });
        }

        // Simple text fields
        const textFields = ['artist', 'rarity', 'language', 'printed_number', 'expansion.name', 'expansion.series'];
        textFields.forEach(f => {
            const el = document.querySelector('[data-field="' + f + '"]');
            if (el) {
                const parent = el.closest('div')?.previousElementSibling;
                result[f.replace('.', '_')] = cleanText(parent);
            }
        });

        // Supertype & subtypes
        const stEl = document.querySelector('[data-field="supertype"]');
        if (stEl) {
            const stParent = stEl.closest('div')?.previousElementSibling;
            result.supertype = cleanText(stParent);
        }
        const subEl = document.querySelector('[data-field="subtypes"]');
        if (subEl) {
            const subParent = subEl.closest('div')?.previousElementSibling;
            result.subtypes = cleanText(subParent)?.split('\\n')?.map(s => s.trim()).filter(Boolean) || [];
        }

        // Types
        const typeEl = document.querySelector('[data-field="types"]');
        if (typeEl) {
            const typeParent = typeEl.closest('div')?.previousElementSibling;
            const typeImgs = typeParent?.querySelectorAll('img') || [];
            result.types = Array.from(typeImgs).map(img => {
                const src = img.getAttribute('src') || '';
                const match = src.match(/assets\\/([a-z]+)-/);
                return match ? match[1] : 'unknown';
            });
        }

        // Rules (Trainer / ex rules)
        const rulesEls = document.querySelectorAll('[data-field="rules"]');
        if (rulesEls.length > 0) {
            result.rules = Array.from(rulesEls).map(el => {
                const parent = el.closest('div')?.previousElementSibling;
                return cleanText(parent);
            }).filter(Boolean);
        }

        // Abilities
        const abilNameEls = document.querySelectorAll('[data-field="abilities.name"]');
        const abilTextEls = document.querySelectorAll('[data-field="abilities.text"]');
        if (abilNameEls.length > 0) {
            result.abilities = [];
            abilNameEls.forEach((el, i) => {
                const nameParent = el.closest('div')?.previousElementSibling;
                const textParent = abilTextEls[i]?.closest('div')?.previousElementSibling;
                result.abilities.push({
                    name: cleanText(nameParent),
                    text: cleanText(textParent)
                });
            });
        }

        // Name
        const nameEl = document.querySelector('[data-field="name"]');
        if (nameEl) {
            const nameParent = nameEl.closest('div')?.previousElementSibling;
            let rawName = cleanText(nameParent);
            // Dọn text bẩn từ UI (nút "View API" bị dính vào tên)
            result.name = rawName.replace('View API', '').trim();
        }

        return result;
    }""")

    return data

def main():
    if len(sys.argv) < 2:
        print("Cách dùng: python scrape_scrydex.py <expansion_url>")
        print("Ví dụ:     python scrape_scrydex.py https://scrydex.com/pokemon/expansions/perfect-order/me3")
        sys.exit(1)

    expansion_url = sys.argv[1]

    # Tạo tên file output từ URL. Lưu vào data/ và đặt tên theo chuẩn store_id
    slug = expansion_url.rstrip("/").split("/")[-2] + "_" + expansion_url.rstrip("/").split("/")[-1]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Không dùng timestamp nữa để ghi đè file cũ, giúp FastAPI app luôn đọc file mới nhất
    output_file = os.path.join(OUTPUT_DIR, f"{slug}.json")

    print(f"🔍 Scrydex Pokemon Scraper")
    print(f"   URL: {expansion_url}")
    print(f"   Output: {output_file}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # headless=False để vượt Cloudflare
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        # Bước 1: Lấy danh sách link
        print("📋 Bước 1: Thu thập danh sách thẻ từ trang expansion...")
        card_entries = get_card_links(page, expansion_url)

        # Loại bỏ duplicate (cùng href)
        seen = set()
        unique_entries = []
        for entry in card_entries:
            if entry["href"] not in seen:
                seen.add(entry["href"])
                unique_entries.append(entry)
        print(f"   → {len(unique_entries)} thẻ unique (sau khi loại trùng)")
        print()

        # Bước 2: Cào chi tiết từng thẻ
        print(f"📝 Bước 2: Cào chi tiết {len(unique_entries)} thẻ...")
        all_cards = []
        errors = []

        for i, entry in enumerate(unique_entries):
            progress = f"[{i+1}/{len(unique_entries)}]"
            print(f"  {progress} {entry['name_number']} ({entry['href'].split('?')[-1]})...", end=" ", flush=True)

            try:
                detail = scrape_card_detail(page, entry["href"])
                # Merge dữ liệu từ list page + detail page
                detail["_list_data"] = {
                    "card_id": entry["card_id"],
                    "list_price": entry["list_price"],
                    "price_trend": entry["trend"],
                    "image_url": entry["image_url"],
                }
                all_cards.append(detail)
                print("✅")
            except Exception as e:
                print(f"❌ Lỗi: {e}")
                errors.append({"href": entry["href"], "error": str(e)})

            # Lưu tiến trình mỗi 10 thẻ
            if (i + 1) % 10 == 0:
                _save_json(output_file, all_cards, errors)
                print(f"  💾 Đã lưu tiến trình ({len(all_cards)} thẻ)")

            random_delay()

        # Lưu kết quả cuối cùng
        _save_json(output_file, all_cards, errors)
        browser.close()

    print()
    print(f"🎉 Hoàn tất!")
    print(f"   ✅ Thành công: {len(all_cards)} thẻ")
    print(f"   ❌ Lỗi: {len(errors)} thẻ")
    print(f"   📁 File: {output_file}")

def _save_json(path, cards, errors):
    output = {
        "scraped_at": datetime.now().isoformat(),
        "total_cards": len(cards),
        "total_errors": len(errors),
        "cards": cards,
        "errors": errors,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
