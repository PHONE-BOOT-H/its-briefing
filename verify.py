"""수집 결과 자가검증. 스키마·날짜·점수분포·중복."""
import json, os, sys, glob, collections, random
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
REQUIRED = ['id', 'kind', 'source', 'title', 'link', 'published']

files = sorted(glob.glob(os.path.join(DATA, 'tenders', '*.json')))
path = files[-1]
items = json.load(open(path, encoding='utf-8'))
news_files = sorted(glob.glob(os.path.join(DATA, 'news', '*.json')))
news = json.load(open(news_files[-1], encoding='utf-8')) if news_files else []
items += news
print('대상: %s (발주 %d + 기사 %d = %d건)\n'
      % (os.path.basename(path), len(items) - len(news), len(news), len(items)))

# 1. 스키마 자가검사
print('=== 1. 스키마')
miss = collections.Counter()
for it in items:
    for k in REQUIRED:
        v = it.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            miss[k] += 1
print('  필수필드 빈 값:', dict(miss) if miss else '없음')

bad_date = [it['id'] for it in items
            if it.get('published') and not (len(it['published']) == 10 and it['published'][4] == '-')]
print('  published 형식 오류: %d건' % len(bad_date))

no_deadline = [it for it in items if not it.get('deadline')]
today = date.today().isoformat()
past = [it for it in items if it.get('deadline') and it['deadline'] < today]
print('  마감일 없음: %d건 / 이미 지난 마감: %d건' % (len(no_deadline), len(past)))
if past:
    for it in past[:5]:
        print('    - %s | %s | %s' % (it['deadline'], it['country'], (it['title'] or '')[:60]))

scores = [it['score'] for it in items]
dist = collections.Counter(scores)
print('  score 분포: %s' % dict(sorted(dist.items(), reverse=True)))
print('  0점 %d건 / 최고 %d / 평균 %.1f' % (dist.get(0, 0), max(scores), sum(scores) / len(scores)))
if max(scores) == 0:
    print('  ** 채점기 고장 의심 **')

# 4. 중복 검사
print('\n=== 4. 중복')
ids = [it['id'] for it in items]
dup = [k for k, v in collections.Counter(ids).items() if v > 1]
print('  실행 내 중복 id: %d건' % len(dup))
links = [it['link'] for it in items]
dupl = [k for k, v in collections.Counter(links).items() if v > 1]
print('  링크 중복: %d건' % len(dupl))

seen = json.load(open(os.path.join(DATA, 'seen.json'), encoding='utf-8'))
mismatch = [it['id'] for it in items if seen.get(it['id']) != it.get('first_seen')]
print('  장부와 first_seen 불일치: %d건' % len(mismatch))
print('  장부 크기: %d' % len(seen))

# 3용 표본 출력
print('\n=== 점수 상위 10건 (관련성 눈검사용)')
for it in sorted(items, key=lambda x: -x['score'])[:10]:
    print('  [%3d] %-6s %-22s %s' % (it['score'], it['country'], ','.join(it['score_hits'])[:22], (it['title'] or '')[:78]))

print('\n=== 무작위 표본 5건 (원문 대조용)')
random.seed(42)
for it in random.sample(items, min(5, len(items))):
    print('  %s | %s | 마감 %s | 점수 %d' % (it['ref_no'], it['country'], it.get('deadline'), it['score']))
    print('    %s' % (it['title'] or '')[:95])
    print('    %s' % it['link'])

# ── CI 게이트: 아래는 데이터가 망가진 것이므로 실패로 끝낸다
hard = []
if miss:
    hard.append('필수필드 빈 값 %s' % dict(miss))
if bad_date:
    hard.append('날짜 형식 오류 %d건' % len(bad_date))
if max(scores) == 0:
    hard.append('전 항목 0점 — 채점기 고장')
if dup:
    hard.append('중복 id %d건' % len(dup))
if mismatch:
    hard.append('장부와 first_seen 불일치 %d건' % len(mismatch))

print()
if hard:
    print('검증 실패: ' + ' / '.join(hard))
    sys.exit(1)
print('검증 통과')
