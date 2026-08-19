"""화면 스크린샷 — 검증·공유용."""
import os
from playwright.sync_api import sync_playwright

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shots')
os.makedirs(OUT, exist_ok=True)
BASE = 'http://localhost:8765/index.html'

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1180, 'height': 900}, device_scale_factor=2)

    # 1. 기본 화면 (이번 주 · 신규만 · 관련도순)
    pg.goto(BASE, wait_until='networkidle')
    pg.wait_for_timeout(500)
    pg.screenshot(path=os.path.join(OUT, '1-목록.png'))
    print('1 목록:', pg.locator('#count').inner_text())

    # 2. 상세 모달
    pg.locator('.card').first.click()
    pg.wait_for_timeout(400)
    pg.screenshot(path=os.path.join(OUT, '2-상세.png'))
    print('2 상세:', pg.locator('#dTitle').inner_text()[:60])

    # 3. 마감임박순 + 전체 기간
    pg.keyboard.press('Escape')
    pg.goto(BASE + '?tab=tenders&range=0&new=0&sort=deadline', wait_until='networkidle')
    pg.wait_for_timeout(500)
    pg.screenshot(path=os.path.join(OUT, '3-마감임박순.png'))
    print('3 마감임박순:', pg.locator('#count').inner_text())

    # 4. 검색
    pg.goto(BASE + '?tab=tenders&range=0&new=0&sort=score&q=signal', wait_until='networkidle')
    pg.wait_for_timeout(500)
    pg.screenshot(path=os.path.join(OUT, '4-검색.png'))
    print('4 검색(signal):', pg.locator('#count').inner_text())

    # 5. 기사 탭 (아직 비어 있음)
    pg.goto(BASE + '?tab=news', wait_until='networkidle')
    pg.wait_for_timeout(400)
    pg.screenshot(path=os.path.join(OUT, '5-기사탭.png'))

    # 6. 다크모드
    pg2 = b.new_page(viewport={'width': 1180, 'height': 900}, device_scale_factor=2,
                     color_scheme='dark')
    pg2.goto(BASE, wait_until='networkidle')
    pg2.wait_for_timeout(500)
    pg2.screenshot(path=os.path.join(OUT, '6-다크모드.png'))

    # 7. 모바일 폭
    pg3 = b.new_page(viewport={'width': 420, 'height': 820}, device_scale_factor=2)
    pg3.goto(BASE, wait_until='networkidle')
    pg3.wait_for_timeout(500)
    pg3.screenshot(path=os.path.join(OUT, '7-모바일.png'))

    b.close()
print('저장 위치:', OUT)
