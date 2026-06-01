from playwright.sync_api import sync_playwright
import json

def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        try:
            page.goto('https://scrydex.com/pokemon/cards/me2pt5-10')
            page.wait_for_load_state("networkidle")
            
            # get all data-field values
            data = page.evaluate("""() => {
                const els = document.querySelectorAll('[data-field]');
                const res = {};
                els.forEach(el => {
                    const field = el.getAttribute('data-field');
                    const parent = el.closest('div').previousElementSibling;
                    res[field] = parent ? parent.innerHTML : 'no parent';
                });
                return res;
            }""")
            print(json.dumps(data, indent=2))
        except Exception as e:
            print("Error:", e)
        b.close()

if __name__ == "__main__":
    main()
