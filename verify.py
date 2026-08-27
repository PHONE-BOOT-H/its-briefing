"""수집 결과 자가검증. 스키마·날짜·점수분포·중복."""
import json, os, sys, glob, collections, random
from datetime import datetime, timedelta, timezone

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
today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()  # 러너는 UTC
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

# ── 채점기 회귀 테스트
# 실측에서 실제로 틀렸던 3건을 고정 입력으로 박아둔다.
# 채점 규칙을 만질 때 이 셋이 깨지면 예전 실수로 돌아간 것이다.
print('\n=== 채점기 회귀 테스트')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect

LITMUS = [
    # (설명, 제목, CPV, 소스 기본점, 기대 조건)
    ('체코 과속단속 레이더 — CPV가 ITS인데 제목엔 ITS 단어가 없다',
     'Czechia – Radar sets – Mesto Chrudim - Mereni rychlosti v obcich',
     ['34932000', '34971000', '34996000'], 0, lambda s: s >= 50),
    ('스페인 잡화 조달 — CPV 39개 중 ITS는 하나뿐',
     'Spain – Construction structures and materials',
     ['14210000', '18100000', '24000000', '31000000', '31500000', '32000000',
      '33192000', '34928000', '34996000', '35111300', '37400000', '39110000',
      '42600000', '44000000', '45212221'], 0, lambda s: s <= 15),
    ('브르노 ITS 3단계 — 부수 CPV가 붙어도 깎이면 안 된다',
     'Czechia – Control, safety or signalling equipment for roads – Rozvoj ITS v Brne',
     ['32333200', '32500000', '34996000', '35125300', '45000000'], 0, lambda s: s >= 60),
    # 아래 둘은 짝이다. 비ITS 수자원은 깎되, 제목에 교통어가 없는 진짜 ITS 건은 남겨야 한다.
    ('자무나 수자원 — 섹터 태그만 교통이고 실체는 강 관리 사업',
     'Bangladesh - SOUTH ASIA- P172499- Jamuna River Sustainable Management Project 1',
     [], 40, lambda s: s <= 25),
    ('케냐 Horn of Africa Gateway — 제목에 교통 단어가 없는 진짜 ITS 사업',
     'Kenya - EASTERN AND SOUTHERN AFRICA- P161305- Horn of Africa Gateway Development Project',
     [], 40, lambda s: s >= 40),
]
reg_fail = []
for desc, title, cpv, base_extra, ok in LITMUS:
    sc, _ = collect.score_item(title, cpv, base_extra=base_extra)
    good = ok(sc)
    print('  %s %3d점  %s' % ('OK ' if good else '실패', sc, desc))
    if not good:
        reg_fail.append(desc)

# ── 기사 채점기 회귀 테스트
# 2026-08-27 실측에서 고친 것들이다. 되돌아가면 여기서 잡힌다.
print('\n=== 기사 채점기 회귀 테스트 (국내)')
NEWS_LITMUS = [
    ('행사 홍보는 깎는다 — 산업전 개막 기사가 상위를 먹던 문제',
     '자율주행 모빌리티 산업전 개막', '', lambda s: s <= 15),
    ('단 행사장에서 나온 실제 계약은 안 깎는다',
     '자율주행모빌리티산업전서 200억 규모 공급 계약 체결', '', lambda s: s >= 24),
    ('사업 신호어 — 예타 통과가 무득점이던 문제',
     '김포시, 태리IC 교통체계 확 바뀐다…530억 확장사업 예타 통과', '', lambda s: s >= 18),
    ('본문 채점 — 제목에 없고 본문에만 있는 하이패스를 본다',
     '튀르키예 고속도로 15년 굴리는 도로공사…해외수주 7495억 쌓았다',
     '한국형 하이패스 첫 수출, 말레이시아 전국 유료도로 2286km 다차로 무정차톨링 구축',
     lambda s: s >= 28),
    ('본문의 흔한 말로는 안 오른다 — 무관한 보도자료가 오르던 문제',
     '제273차 대외경제장관회의 개최', '원전 수주 지원 방안을 논의했다', lambda s: s <= 12),
]
news_fail = []
for desc, title, body, ok in NEWS_LITMUS:
    sc, _ = collect.score_news(title, True, body=body)
    good = ok(sc)
    print('  %s %3d점  %s' % ('OK ' if good else '실패', sc, desc))
    if not good:
        news_fail.append(desc)

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
if reg_fail:
    hard.append('채점기 회귀 %d건' % len(reg_fail))
if news_fail:
    hard.append('기사 채점기 회귀 %d건' % len(news_fail))

print()
if hard:
    print('검증 실패: ' + ' / '.join(hard))
    sys.exit(1)
print('검증 통과')
