from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        try:
            page.goto('https://scrydex.com/pokemon/cards/me2pt5-10')
            page.wait_for_selector('[data-field="weaknesses"]', timeout=5000)
            html = page.evaluate('document.querySelector(\'[data-field="weaknesses"]\').parentElement.innerHTML')
            print(html)
        except Exception as e:
            print("Error:", e)
        b.close()

if __name__ == "__main__":
    main()
