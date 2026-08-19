"""화면 기능 자가검증 — 필터·URL상태·복사·표시값 대조."""
import json, os, glob
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = 'http://localhost:8765/index.html'
items = json.load(open(sorted(glob.glob(os.path.join(ROOT, 'data/tenders/*.json')))[-1], encoding='utf-8'))
byref = {i['ref_no']: i for i in items}
fails = []

def check(name, got, want):
    ok = got == want
    print(('  OK  ' if ok else '  실패 ') + '%s: %s%s' % (name, got, '' if ok else ' (기대 %s)' % want))
    if not ok:
        fails.append(name)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(permissions=['clipboard-read', 'clipboard-write'])
    pg = ctx.new_page()

    print('== 필터')
    pg.goto(BASE, wait_until='networkidle'); pg.wait_for_timeout(400)
    check('기본 화면 건수', pg.locator('#count').inner_text(), '%d건' % len(items))
    check('카드 개수', str(pg.locator('.card').count()), str(len(items)))

    pg.goto(BASE + '?range=0&new=0&q=Ireland', wait_until='networkidle'); pg.wait_for_timeout(400)
    check('검색 Ireland', pg.locator('#count').inner_text(), '1건')

    pg.goto(BASE + '?range=0&new=0&sort=deadline', wait_until='networkidle'); pg.wait_for_timeout(400)
    first = pg.locator('.card').first.inner_text()
    earliest = min(i['deadline'] for i in items if i.get('deadline'))
    check('마감임박순 첫 카드 마감일', earliest in first, True)

    print('== URL 상태 복원')
    pg.goto(BASE + '?tab=news&range=3&new=0&sort=published', wait_until='networkidle')
    pg.wait_for_timeout(300)
    check('탭 복원', pg.locator('[role=tab][data-tab=news]').get_attribute('aria-selected'), 'true')
    check('신규만 해제 복원', pg.locator('#onlyNew').get_attribute('aria-pressed'), 'false')
    check('정렬 복원', pg.locator('#sort').input_value(), 'published')
    check('기사탭 안내문', '아직 연결되지 않았' in pg.locator('.empty').inner_text(), True)

    print('== 표시값 대조 (JSON ↔ 화면)')
    pg.goto(BASE, wait_until='networkidle'); pg.wait_for_timeout(400)
    pg.locator('.card').first.click(); pg.wait_for_timeout(300)
    ref = pg.locator('#dFields').inner_text().split('참조번호')[1].split('\n')[1].strip()
    src = byref[ref]
    body = pg.locator('#dFields').inner_text()
    check('발주처 일치', src['org'] in body, True)
    check('마감일 일치', src['deadline'] in body, True)
    check('참조번호 존재', ref in byref, True)
    check('원문 링크 일치', pg.locator('#dLink').get_attribute('href'), src['link'])
    check('번역 링크 papago', 'papago.naver.com' in pg.locator('#dTrans').get_attribute('href'), True)

    print('== 등록문 복사')
    pg.locator('#dCopy').click(); pg.wait_for_timeout(500)
    txt = pg.evaluate('navigator.clipboard.readText()')
    check('복사본에 원문 제목 포함', src['title'][:40] in txt, True)
    check('복사본에 링크 포함', src['link'] in txt, True)
    check('복사본에 세부내용 틀 포함', '□ 세부내용' in txt, True)
    check('버튼 피드백', pg.locator('#dCopy').inner_text(), '복사됨 ✓')

    b.close()

print('\n실패 %d건 %s' % (len(fails), fails if fails else ''))
