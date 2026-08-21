"""ITS 레이더 수집기.

각 소스 = fetch_xxx() -> list[dict]. 한 소스가 죽어도 나머지는 수집된다.
전체 합계 0건이면 exit 1 (실패 메일 방아쇠).
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
import http.cookiejar
import email.utils
import collections
import hashlib
import time
import html as html_mod
from datetime import datetime, timezone, timedelta

SCHEMA_VERSION = 1
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
SEEN_PATH = os.path.join(DATA, 'seen.json')
BOARD_CACHE_PATH = os.path.join(DATA, 'board_cache.json')
KST = timezone(timedelta(hours=9))


def today_kst():
    """수집 기준일. 러너는 UTC라 date.today()를 쓰면 새벽 실행에서 하루가 밀린다."""
    return datetime.now(KST).date()

# ── 국가 코드 → 한글 (ISO3 기준, 필요한 것만)
COUNTRY_KO = {
    'DEU': '독일', 'ESP': '스페인', 'CZE': '체코', 'POL': '폴란드', 'ROU': '루마니아',
    'FRA': '프랑스', 'LTU': '리투아니아', 'ITA': '이탈리아', 'AUT': '오스트리아',
    'SVN': '슬로베니아', 'PRT': '포르투갈', 'NLD': '네덜란드', 'BEL': '벨기에',
    'SWE': '스웨덴', 'FIN': '핀란드', 'DNK': '덴마크', 'NOR': '노르웨이', 'IRL': '아일랜드',
    'HUN': '헝가리', 'SVK': '슬로바키아', 'BGR': '불가리아', 'HRV': '크로아티아',
    'GRC': '그리스', 'EST': '에스토니아', 'LVA': '라트비아', 'LUX': '룩셈부르크',
    'CYP': '키프로스', 'MLT': '몰타', 'CHE': '스위스', 'ISL': '아이슬란드',
    'SRB': '세르비아', 'MKD': '북마케도니아', 'BIH': '보스니아헤르체고비나',
    'MNE': '몬테네그로', 'ALB': '알바니아', 'UKR': '우크라이나', 'TUR': '튀르키예',
    'GBR': '영국', 'USA': '미국',
}

# 세계은행·구글뉴스는 국가명을 영문으로 준다. 자주 나오는 것만 한글로.
COUNTRY_NAME_KO = {
    'Peru': '페루', 'Pakistan': '파키스탄', 'Mauritius': '모리셔스', 'Ukraine': '우크라이나',
    'Tanzania': '탄자니아', 'Bangladesh': '방글라데시', 'Kenya': '케냐', 'Ghana': '가나',
    'Mozambique': '모잠비크', 'Liberia': '라이베리아', 'Zambia': '잠비아', 'Georgia': '조지아',
    'India': '인도', 'Indonesia': '인도네시아', 'Viet Nam': '베트남', 'Vietnam': '베트남',
    'Philippines': '필리핀', 'Nepal': '네팔', 'Sri Lanka': '스리랑카', 'Bhutan': '부탄',
    'Uzbekistan': '우즈베키스탄', 'Kazakhstan': '카자흐스탄', 'Kyrgyz Republic': '키르기스스탄',
    'Brazil': '브라질', 'Colombia': '콜롬비아', 'Argentina': '아르헨티나', 'Ecuador': '에콰도르',
    'Bolivia': '볼리비아', 'Haiti': '아이티', 'Jamaica': '자메이카',
    'Egypt, Arab Republic of': '이집트', 'Morocco': '모로코', 'Tunisia': '튀니지',
    'Nigeria': '나이지리아', 'Ethiopia': '에티오피아', 'Rwanda': '르완다', 'Uganda': '우간다',
    'Senegal': '세네갈', 'Cameroon': '카메룬', 'Chad': '차드', 'Niger': '니제르',
    'Guinea': '기니', 'Madagascar': '마다가스카르', 'Malawi': '말라위', 'Gabon': '가봉',
    'Congo, Democratic Republic of': '콩고민주공화국', 'Solomon Islands': '솔로몬제도',
    'Turkiye': '튀르키예', 'Serbia': '세르비아', 'Moldova': '몰도바', 'Kosovo': '코소보',
    'Bosnia and Herzegovina': '보스니아헤르체고비나', 'Central Africa': '중앙아프리카',
}

# ── ITS 밀착도 채점
#
# 구조: CPV 기본점(소스가 준 분류) + 영어 제목 키워드 보너스.
# TED 제목은 "국가 – CPV영문명 – 원어 사업명" 형태라 사업명이 현지어다.
# (notice-title이 24개 언어로 오지만 번역되는 건 국가명·CPV명뿐이고 사업명은 원어 그대로.
#  title-proc·description-proc도 원어만 온다 — 실측 확인.)
# 그래서 CPV로 바닥을 깔고, 같은 CPV 안에서의 순위는 제목 단어가 정한다.

CPV_BASE = [
    # (기본점, CPV 접두사들)
    (50, ['34996', '34970', '34971', '34972', '48813', '63712', '34932', '34992']),
    (40, ['34923', '34922', '34942', '35261', '34924', '34928']),
    (25, ['3494', '45316', '45314', '34990']),
    (15, ['45233', '45310', '71322', '71356', '71328', '34120', '34100']),
]

# 제목(영어 부분)에 걸리면 가산. CPV가 못 잡는 뉘앙스를 여기서 올린다.
KEYWORD_BONUS = [
    (20, ['intelligent transport', 'its system', 'c-its', 'traffic management',
          'traffic control', 'traffic-control']),
    (12, ['traffic signal', 'traffic light', 'signalling', 'signaling',
          'toll', 'tolling', 'road pricing', 'speed camera', 'enforcement',
          'anpr', 'number plate', 'variable message', 'passenger information',
          'traffic monitoring', 'traffic counting', 'radar', 'speed measurement',
          'detection', 'surveillance', 'bulletin board',
          # 실측에서 0점으로 떨어졌던 제목들에서 뽑은 말
          'work zone', 'work-zone', 'school zone', 'roadside', 'level crossing']),
    (6, ['bus rapid transit', 'public transport', 'mobility', 'road safety',
         'telematics', 'fare collection', 'ticketing', 'weigh-in-motion',
         'autonomous truck', 'self-driving', 'driverless', 'freight', 'cargo',
         'logistics', 'platooning', 'shuttle',
         'tram', 'light rail', 'metro', 'parking', 'interoperability',
         'smart city', 'maas', 'dashcam', 'black box']),
]
NEGATIVE = ['cleaning', 'catering', 'insurance', 'audit', 'accounting', 'furniture',
            'stationery', 'medical', 'food', 'gender', 'legal advice',
            'security service', 'guard service', 'physical protection']


def keyword_bonus(text, rules):
    """키워드 가산점. 등급별로 최대 2개, 두 번째는 절반.

    같은 개념이 두 번 세어지는 걸 막는다.
      - 긴 단어 우선: 'tolling'이 걸리면 'toll'로 또 더하지 않는다
      - 상위 등급에서 잡힌 말의 부분은 하위 등급에서 건너뛴다
        ('교통관제' 20점이 걸렸으면 '관제' 6점은 세지 않는다)
    실측에서 '단속'이 12점·6점 양쪽에 등재돼 27점짜리 기사가 나왔다.
    """
    t = (text or '').lower()
    total, hits, taken = 0, [], []
    for weight, words in rules:
        cands = sorted({w for w in words if w in t}, key=len, reverse=True)
        picked = []
        for w in cands:
            if any(w in prev for prev in taken):     # 이미 센 개념의 부분
                continue
            picked.append(w)
            taken.append(w)
            if len(picked) == 2:
                break
        for i, w in enumerate(picked):
            total += weight if i == 0 else weight // 2
            hits.append(w)
    return total, hits


def cpv_base(cpv_list):
    """CPV 코드로 기본점. 가장 높은 등급 하나만 적용."""
    best, hit = 0, None
    for code in cpv_list or []:
        c = str(code)
        for weight, prefixes in CPV_BASE:
            if any(c.startswith(p) for p in prefixes):
                if weight > best:
                    best, hit = weight, c
                break
    return best, hit


def its_ratio(cpv_list):
    """전체 CPV 중 ITS 계열 비율. 잡화 조달 판별용."""
    if not cpv_list:
        return 0.0
    n = 0
    for code in cpv_list:
        c = str(code)
        for weight, prefixes in CPV_BASE[:2]:  # 50·40점대만 ITS로 셈
            if any(c.startswith(p) for p in prefixes):
                n += 1
                break
    return n / len(cpv_list)


def score_item(title, cpv_list):
    """CPV 기본점 + 제목 키워드 보너스 - 잡화 감점. 0~100."""
    t = (title or '').lower()
    base, base_hit = cpv_base(cpv_list)
    hits = ['cpv:' + base_hit] if base_hit else []

    bonus, bonus_hits = keyword_bonus(t, KEYWORD_BONUS)
    hits += bonus_hits

    penalty = 0
    for w in NEGATIVE:
        if w in t:
            penalty += 15
            hits.append('-' + w)

    # 잡화 조달: CPV가 잔뜩인데 ITS 비중이 낮으면 본체가 ITS가 아니다
    ratio = its_ratio(cpv_list)
    n_cpv = len(cpv_list or [])
    # CPV 15개 이상이면서 ITS 비중이 낮을 때만 감점.
    # 대형 ITS 사업은 통신장비·CCTV·건설 CPV가 딸려붙어 비율이 떨어지므로
    # 개수 조건 없이 비율만 보면 진짜 ITS 사업을 깎는다 (브르노 ITS 3단계 사례).
    if n_cpv >= 15 and ratio < 0.25:
        penalty += 40
        hits.append('-잡화(CPV%d개, ITS%.0f%%)' % (n_cpv, ratio * 100))

    return max(0, min(100, base + bonus - penalty)), hits


# ── 공통 유틸
def http_json(url, payload=None, headers=None, timeout=60):
    hdr = {'User-Agent': 'its-briefing/1.0 (+https://github.com/PHONE-BOOT-H/its-briefing)'}
    if headers:
        hdr.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        hdr['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'ignore'))


def pick_lang(value, prefer='eng'):
    """TED의 다국어 dict에서 영어 우선, 없으면 첫 값."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return pick_lang(value[0]) if value else None
    if isinstance(value, dict):
        v = value.get(prefer)
        if v:
            return v[0] if isinstance(v, list) else v
        for k in value:
            v = value[k]
            if v:
                return v[0] if isinstance(v, list) else v
    return None


def first_of(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def iso_date(value):
    """'2026-08-19+02:00' / '2026-08-19T14:00:00+02:00' → '2026-08-19'."""
    v = first_of(value)
    if not v:
        return None
    m = re.match(r'(\d{4}-\d{2}-\d{2})', str(v))
    return m.group(1) if m else None


# ── 소스: TED (EU 조달공고)
TED_CPV = ['34996000', '34970000', '34923000', '48813000', '34922100', '63712700', '34942000']
TED_URL = 'https://api.ted.europa.eu/v3/notices/search'


def fetch_ted(days=7):
    query = (
        'classification-cpv IN (%s) AND publication-date >= today(-%d) '
        'AND form-type=competition SORT BY publication-date DESC' % (' '.join(TED_CPV), days)
    )
    payload = {
        'query': query,
        'fields': ['publication-number', 'publication-date', 'notice-title', 'buyer-name',
                   'buyer-country', 'deadline-receipt-request', 'classification-cpv',
                   'estimated-value-proc', 'estimated-value-cur-proc', 'contract-nature'],
        'limit': 250,
        'page': 1,
    }
    res = http_json(TED_URL, payload=payload)
    out = []
    for n in res.get('notices', []):
        pub_no = first_of(n.get('publication-number'))
        if not pub_no:
            continue
        title = pick_lang(n.get('notice-title'))
        country = first_of(n.get('buyer-country'))
        cpv = n.get('classification-cpv') or []
        if isinstance(cpv, str):
            cpv = [cpv]
        cpv = sorted(set(str(c) for c in cpv))
        sc, hits = score_item(title, cpv)

        amount = first_of(n.get('estimated-value-proc'))
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        budget = None
        if amount:
            budget = {'currency': first_of(n.get('estimated-value-cur-proc')) or 'EUR',
                      'amount': amount, 'is_project_total': False}
        out.append({
            'schema_version': SCHEMA_VERSION,
            'id': 'ted-%s' % pub_no,
            'kind': 'tender',
            'source': 'TED',
            'type': '입찰',
            'country': country,
            'country_ko': COUNTRY_KO.get(country, country),
            'title': title,
            'org': pick_lang(n.get('buyer-name')),
            'budget': budget,
            'nature': first_of(n.get('contract-nature')),
            'published': iso_date(n.get('publication-date')),
            'deadline': iso_date(n.get('deadline-receipt-request')),
            'ref_no': pub_no,
            'link': 'https://ted.europa.eu/en/notice/-/detail/%s' % pub_no,
            'cpv': cpv,
            'score': sc,
            'score_hits': hits,
            'already_posted': False,
        })
    return out


# ── 소스: SAM.gov (미국 연방조달)
#
# 문서화된 공식 API(api.sam.gov)는 키가 필요하고 한국 IP에서 404다.
# 웹사이트 내부 검색 API가 무인증으로 열려 있어 그쪽을 쓴다 (비공식 — 예고 없이 바뀔 수 있음).
# publish_date 필터는 형식을 못 찾았다(400/500). 대신 modifiedDate 내림차순으로 페이징하다
# 창을 벗어나면 멈춘다 — 최근에 게시된 공고는 반드시 그 기간에 수정 이력이 있으므로 누락이 없다.
SAM_URL = ('https://sam.gov/api/prod/sgs/v1/search/?index=opp&mode=search&is_active=true'
           '&naics=237310,541330,334290&sort=-modifiedDate&page=%d&size=100')
SAM_HEADERS = {'Accept': '*/*', 'User-Agent': 'Mozilla/5.0'}
SAM_MIN_BONUS = 12   # 제목·본문에 실제 ITS 키워드가 걸린 것만 (미 연방조달은 무관 건이 태반)
SAM_BASE = 25        # 교통 인접 NAICS로 이미 걸러진 상태라 기본점을 준다


def fetch_samgov(days=7):
    start = (today_kst() - timedelta(days=days)).isoformat()
    rows, out = [], []
    for page in range(6):
        d = http_json(SAM_URL % page, headers=SAM_HEADERS)
        res = (d.get('_embedded') or {}).get('results') or []
        if not res:
            break
        rows += res
        if min(r.get('modifiedDate', '')[:10] for r in res) < start:
            break

    for r in rows:
        pub = (r.get('publishDate') or '')[:10]
        if pub < start:
            continue
        title = r.get('title') or ''
        desc = ''
        for d0 in (r.get('descriptions') or [])[:1]:
            desc = strip_tags(d0.get('content'))[:600]
        bonus, bonus_hits = keyword_bonus(title + ' ' + desc, KEYWORD_BONUS)
        if bonus < SAM_MIN_BONUS:
            continue

        org = ''
        for o in (r.get('organizationHierarchy') or []):
            if o.get('name'):
                org = o['name'].title()
        out.append({
            'schema_version': SCHEMA_VERSION,
            'id': 'sam-%s' % r.get('_id'),
            'kind': 'tender',
            'source': 'SAM.gov',
            'type': '입찰',
            'country': 'USA',
            'country_ko': '미국',
            'title': title,
            'org': org,
            'budget': None,
            'published': pub,
            'deadline': (r.get('responseDate') or '')[:10] or None,
            'ref_no': r.get('solicitationNumber') or r.get('_id'),
            'link': 'https://sam.gov/opp/%s/view' % r.get('_id'),
            'cpv': [],
            'score': min(100, SAM_BASE + bonus),
            'score_hits': [h for h in bonus_hits if not h.startswith('cpv:')],
            'already_posted': False,
        })
    return out


# ── 소스: World Bank (조달공고 + 문서 3종)
#
# 게시판 기등록 91건 역추적에서 90건(99%)이 이 API들에서 발견됐다.
# 조달공고만 붙이면 30%밖에 못 잡는다 — 조달예측·기획단계·프로젝트승인이 전부 문서(WDS) 쪽이다.
WB_TERMS = ['transport', 'traffic', 'intelligent transport', 'mobility',
            'road safety', 'railway', 'toll', 'bus rapid transit']
WB_NOTICE = ('https://search.worldbank.org/api/v2/procnotices?format=json&qterm=%s'
             '&srt=noticedate&order=desc&rows=40&os=%d'
             '&fl=id,noticedate,notice_type,project_ctry_name,project_id,project_name,'
             'bid_description,submission_date,submission_deadline_date')

# 세계은행은 qterm·섹터 태그가 넓어서 항공권 구매·난민사업까지 딸려온다.
# 제목에 교통 관련어가 없고 ITS 키워드 보너스도 0이면 버린다.
TRANSPORTISH = ['transport', 'road', 'traffic', 'mobility', 'rail', 'metro', 'bus',
                'highway', 'corridor', 'bridge', 'tunnel', 'logistic', 'freight']


def transportish(text):
    t = (text or '').lower()
    return any(w in t for w in TRANSPORTISH)
WB_WDS = ('https://search.worldbank.org/api/v3/wds?format=json&rows=%d&srt=docdt&order=desc'
          '&docty=%s%s&fl=docdt,docty,display_title,projectid,count,guid')
WB_DOCTY_C = ['Project Information Document', 'Project Appraisal Document',
              'Loan Agreement', 'Project Agreement', 'Financing Agreement']


def wb_date(s):
    """'19-Aug-2026' → '2026-08-19'."""
    try:
        return datetime.strptime(s, '%d-%b-%Y').date().isoformat()
    except (TypeError, ValueError):
        return None


def fetch_worldbank(days=7):
    start = (today_kst() - timedelta(days=days)).isoformat()
    out = {}

    # A. 조달공고 → 입찰
    for term in WB_TERMS:
        for os_ in (0, 40):
            d = http_json(WB_NOTICE % (urllib.parse.quote(term), os_))
            res = d.get('procnotices') or []
            if not res:
                break
            oldest = '9999'
            for n in res:
                nd = wb_date(n.get('noticedate'))
                if not nd:
                    continue
                oldest = min(oldest, nd)
                if nd < start or n.get('notice_type') == 'Contract Award':
                    continue
                title = n.get('bid_description') or n.get('project_name') or ''
                ctx = title + ' ' + (n.get('project_name') or '')
                sc, hits = score_item(ctx, [])
                if sc == 0 and not transportish(ctx):
                    continue
                out['wb-%s' % n['id']] = {
                    'schema_version': SCHEMA_VERSION, 'id': 'wb-%s' % n['id'], 'kind': 'tender',
                    'source': 'World Bank', 'type': '입찰', 'stream': 'notice',
                    'country': n.get('project_ctry_name'),
                    'country_ko': COUNTRY_NAME_KO.get(n.get('project_ctry_name'), n.get('project_ctry_name')),
                    'title': title, 'org': n.get('project_name'), 'budget': None,
                    'published': nd, 'deadline': (n.get('submission_deadline_date') or '')[:10] or None,
                    'ref_no': n.get('project_id') or n['id'],
                    'project_id': n.get('project_id'),
                    'link': 'https://projects.worldbank.org/en/projects-operations/procurement-detail/%s' % n['id'],
                    'cpv': [], 'score': min(100, 25 + sc), 'score_hits': hits,
                    'already_posted': False,
                }
            if oldest < start:
                break

    # B·C. 문서 → 조달예측 / 기획단계 / 프로젝트승인
    docs = [('Procurement Plan', '&sectr_exact=' + urllib.parse.quote('FY17 - Transportation'), 60, '조달예측', 40)]
    docs += [(dt, '', 30, '기획단계' if dt.startswith('Project Information') else '프로젝트승인', 30)
             for dt in WB_DOCTY_C]

    for docty, extra, rows, ktype, base in docs:
        d = http_json(WB_WDS % (rows, urllib.parse.quote(docty), extra))
        for x in (d.get('documents') or {}).values():
            if not isinstance(x, dict):
                continue
            dt = (x.get('docdt') or '')[:10]
            if not dt or dt < start:
                continue
            title = x.get('display_title') or ''
            if not extra and not transportish(title):
                continue  # 섹터 필터가 없는 문서 종류만 제목으로 거른다
            sc, hits = score_item(title, [])
            guid = x.get('guid') or x.get('id')
            pid = x.get('projectid')
            out['wbd-%s' % guid] = {
                'schema_version': SCHEMA_VERSION, 'id': 'wbd-%s' % guid, 'kind': 'tender',
                'source': 'World Bank', 'type': ktype, 'stream': 'document',
                'country': x.get('count'),
                'country_ko': COUNTRY_NAME_KO.get(x.get('count'), x.get('count')),
                'title': title, 'org': None, 'budget': None,
                'published': dt, 'deadline': None,
                'ref_no': pid or guid, 'project_id': pid,
                'link': 'https://documents.worldbank.org/en/publication/documents-reports/documentdetail/%s' % guid,
                'cpv': [], 'score': min(100, base + sc), 'score_hits': hits,
                'already_posted': False,
            }

    # 같은 사업에서 조달공고와 문서가 함께 잡히면 문서를 버린다.
    # 단 공고끼리는 남긴다 — 한 사업에 용역·공사 공고가 따로 뜨는 일이 흔하고,
    # 프로젝트당 1건으로 접으면 그중 하나가 통째로 사라진다.
    notices = [it for it in out.values() if it['stream'] == 'notice']
    covered = {it.get('project_id') for it in notices if it.get('project_id')}
    docs = [it for it in out.values()
            if it['stream'] == 'document' and it.get('project_id') not in covered]
    return notices + docs


# ── 사내 게시판 대조 (이미 올린 건 표시)
#
# 게시판 API는 로그인 없이 열린다. 발주는 참조번호로 정확히 대조되지만,
# 기사는 게시판이 영어 제목이고 우리 수집분은 한국어라 문자 대조가 안 된다.
# 기업·기관 이름 대조로 느슨하게 잡는다 — 최종 판단은 어차피 사람이 한다.
BOARD_ORDERS = ('https://intl.its.go.kr/api/export/its-orders'
                '?curPage=%d&lang=ko&searchType=&keyword=&continent=&deadline=&pbanType=')
BOARD_ORDER_DETAIL = ('https://intl.its.go.kr/api/export/its-orders/%s'
                      '?curPage=1&continent=&deadline=&searchType=&keyword=&lang=ko')
BOARD_NOTICES = 'https://intl.its.go.kr/communication/notices?lang=en&curPage=%d'

# 게시판(영문) ↔ 국내 기사(한국어) 이름 대조표. 자주 나오는 것만 둔다.
BOARD_ALIAS = {
    'vueron': '뷰런', 'rideflux': '라이드플럭스', 'mars auto': '마스오토',
    'saesoltech': '새솔테크', 'autocrypt': '아우토크립트', 'autonomous a2z': '오토노머스',
    'koroad': '도로교통공단', 'molit': '국토교통부', 'kakao': '카카오',
    'hyundai': '현대', 'kia': '기아', 'tmoney': '티머니', 'naver': '네이버',
    'socar': '쏘카', 'seoul': '서울', 'busan': '부산', 'incheon': '인천',
    'gangneung': '강릉', 'deepx': '딥엑스', 'mobilint': '모빌린트',
    'stradvision': '스트라드비젼', 'thordrive': '토르드라이브', 'lg': 'LG', 'sk': 'SK',
}


def fetch_board_state():
    """게시판에 이미 올라간 것들. 실패해도 수집 자체는 계속되어야 한다.

    게시글은 한 번 올라오면 바뀌지 않으므로 상세는 캐시한다.
    캐시가 없던 때는 매일 30건의 상세를 전부 다시 받았다.
    """
    refs, titles = set(), []
    cache = {}
    if os.path.exists(BOARD_CACHE_PATH):
        try:
            with open(BOARD_CACHE_PATH, encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    fetched = 0
    try:
        for page in (1, 2, 3):
            d = http_json(BOARD_ORDERS % page)
            for r in d.get('resultResponses', []):
                bno = str(r['bno'])
                if bno not in cache:
                    try:
                        dd = http_json(BOARD_ORDER_DETAIL % bno)
                    except Exception:
                        continue
                    cache[bno] = {'refNo': (dd.get('refNo') or '').strip(),
                                  'link': (dd.get('link') or '').strip()}
                    fetched += 1
                for v in cache[bno].values():
                    if v:
                        refs.add(v)
        with open(BOARD_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
        print('  게시판 상세: 캐시 %d건, 신규 조회 %d건' % (len(cache), fetched), file=sys.stderr)
    except Exception as e:
        print('  게시판(발주) 대조 실패: %s' % e, file=sys.stderr)
    try:
        for page in (1, 2):
            h = http_text(BOARD_NOTICES % page)
            for _, t in re.findall(r'goToDetail\((\d+)\)">(.*?)</a>', h, re.S):
                titles.append(strip_tags(t))
    except Exception as e:
        print('  게시판(기사) 대조 실패: %s' % e, file=sys.stderr)
    return refs, titles


def mark_posted(items, refs, board_titles):
    """이미 게시판에 올라간 항목에 표시. 제외하지 않고 표시만 한다."""
    joined = ' | '.join(board_titles).lower()
    n = 0
    for it in items:
        if it['kind'] == 'tender':
            keys = [k for k in (it.get('ref_no'), it.get('project_id'),
                                (it.get('link') or '').rsplit('/', 1)[-1])
                    if k and len(str(k)) >= 6]   # 짧은 번호는 우연히 겹친다
            if any(any(str(k) in r or r in str(k) for r in refs) for k in keys):
                it['already_posted'] = True
                n += 1
        else:
            t = (it.get('title') or '').lower()
            for en, ko in BOARD_ALIAS.items():
                if en in joined and (ko.lower() in t or en in t):
                    # 같은 회사가 최근 게시판에 올랐다는 신호일 뿐, 같은 기사라는 뜻은 아니다
                    it['related_posted'] = en
                    n += 1
                    break
    return n


# ── 장부: 최초 발견일 기록
def load_seen():
    if os.path.exists(SEEN_PATH):
        with open(SEEN_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {}


def apply_first_seen(items, seen, today):
    """장부에 없으면 오늘로 기록, 있으면 기존 값 유지."""
    new_count = 0
    for it in items:
        if it['id'] in seen:
            it['first_seen'] = seen[it['id']]
        else:
            it['first_seen'] = today
            seen[it['id']] = today
            new_count += 1
    return new_count


def main():
    os.makedirs(os.path.join(DATA, 'tenders'), exist_ok=True)
    now = datetime.now(KST)
    today = now.date().isoformat()

    seen = load_seen()
    items, status = [], {}

    for name, fn in SOURCES:
        try:
            got = fn()
            for g in got:
                g['collector'] = name
            items.extend(got)
            status[name] = {'ok': True, 'count': len(got), 'at': now.isoformat()}
            print('[%s] %d건' % (name, len(got)), file=sys.stderr)
        except Exception as e:
            status[name] = {'ok': False, 'count': 0, 'at': now.isoformat(), 'error': str(e)[:200]}
            print('[%s] 실패: %s' % (name, e), file=sys.stderr)

    # 실패한 소스는 오늘 파일에 남아 있던 결과를 되살린다.
    # 일시적인 네트워크 장애로 그날 수집분이 통째로 사라지는 것을 막는다.
    failed = [n for n, st in status.items() if not st['ok']]
    if failed:
        for sub in ('tenders', 'news'):
            path = os.path.join(DATA, sub, '%s.json' % today)
            if not os.path.exists(path):
                continue
            with open(path, encoding='utf-8') as f:
                for old in json.load(f):
                    if old.get('collector') in failed:
                        items.append(old)
        print('실패한 소스 %s → 이전 결과에서 복원' % ', '.join(failed), file=sys.stderr)

    # id 기준 중복 제거 (소스 간 병합)
    merged = {}
    for it in items:
        if it['id'] not in merged:
            merged[it['id']] = it
    items = list(merged.values())

    try:
        refs, board_titles = fetch_board_state()
        posted = mark_posted(items, refs, board_titles)
        print('게시판 대조: 참조번호 %d개 / 등록 제목 %d개 → 표시 %d건'
              % (len(refs), len(board_titles), posted), file=sys.stderr)
    except Exception as e:
        print('게시판 대조 건너뜀: %s' % e, file=sys.stderr)

    new_count = apply_first_seen(items, seen, today)
    items.sort(key=lambda x: (-x['score'], x.get('deadline') or '9999'))

    if not items:
        print('수집 결과 0건 — 실패 처리', file=sys.stderr)
        sys.exit(1)

    tenders = [i for i in items if i['kind'] == 'tender']
    news = [i for i in items if i['kind'] == 'news']
    os.makedirs(os.path.join(DATA, 'news'), exist_ok=True)
    with open(os.path.join(DATA, 'tenders', '%s.json' % today), 'w', encoding='utf-8') as f:
        json.dump(tenders, f, ensure_ascii=False, indent=1)
    if news:
        with open(os.path.join(DATA, 'news', '%s.json' % today), 'w', encoding='utf-8') as f:
            json.dump(news, f, ensure_ascii=False, indent=1)
    with open(SEEN_PATH, 'w', encoding='utf-8') as f:
        json.dump(seen, f, ensure_ascii=False, indent=1, sort_keys=True)

    index = {'schema_version': SCHEMA_VERSION, 'last_success': now.isoformat(), 'sources': status,
             'tenders': sorted(os.listdir(os.path.join(DATA, 'tenders'))),
             'news': sorted(os.listdir(os.path.join(DATA, 'news')))
                     if os.path.isdir(os.path.join(DATA, 'news')) else []}
    with open(os.path.join(DATA, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    print('총 %d건 (발주 %d / 기사 %d, 신규 %d건)'
          % (len(items), len(tenders), len(news), new_count), file=sys.stderr)




# ══════════════════════════════════════════════════════════════════
# 기사 수집 — 보드 A(해외 영어) / 보드 B(국내) / 국내 동향(정책·법제도)
# ══════════════════════════════════════════════════════════════════

BROWSER_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
_OPENER.addheaders = [('User-Agent', BROWSER_UA)]

# 한국어 기사 채점. 영어 기사는 KEYWORD_BONUS를 그대로 쓴다.
KO_BONUS = [
    (20, ['지능형교통체계', 'c-its', '자율주행', '교통관제', '교통 관제', 'v2x']),
    (12, ['신호', '스마트교통', '스마트 교통', '교통정보', '요금징수', '하이패스',
          '단속', '과속', '교통량', '버스정보', '주차', 'brt', '수요응답',
          '레벨4', '모빌리티', '차량통신', '입법예고', '시행령', '개정']),
    (6, ['교통', '도로', '철도', '물류', '대중교통', '스마트시티', '국토교통부',
         '자동차', '고속도로', '지하철', 'ktx', '버스', '택시', '화물', '운전',
         'npu', '반도체', '카메라', '관제', '통신']),   # '단속'은 12점 등급에만 둔다
]
KO_NEGATIVE = ['부고', '인사이동', '주가', '증시', '코스피', '분양', '아파트값', '날씨', '부동산',
               '시장 규모', '시장규모', '점유율', '시장 전망', '리포트 발간', '보고서 발간']

# 영어 기사 전용 차단어. 시장조사 보도자료와 항공교통관제(ITS 아님)를 걸러낸다.
EN_NEWS_NEGATIVE = ['market size', 'market growth', 'market share', 'market report',
                    'market to reach', 'market analysis', 'cagr', 'forecast to 20',
                    'air traffic', 'air navigation']

NEWS_DAILY_CAP = 5   # 보드별 하루 노출 상한
ASSOC_BASE = 12      # ITS Korea 게시판 기본점 — 협회가 ITS 관점에서 이미 고른 목록이다


def is_english(title):
    """제목이 영어인지. 라틴 문자 비율만 봐도 충분하다."""
    t = re.sub(r'[^0-9A-Za-z가-힣]', '', title or '')
    if not t:
        return False
    latin = sum(1 for c in t if c.isascii())
    return latin / len(t) > 0.7


def score_news(title, korean=False):
    # 한국어 소스라도 제목이 영어면 영어 채점기를 태운다
    if korean and is_english(title):
        korean = False
    t = (title or '').lower()
    total, hits = keyword_bonus(t, KO_BONUS if korean else KEYWORD_BONUS)
    stop = KO_NEGATIVE if korean else (NEGATIVE + EN_NEWS_NEGATIVE)
    for w in stop:
        if w in t:
            total -= 25   # 기사에서는 차단어가 걸리면 사실상 탈락시킨다
            hits.append('-' + w)
    return max(0, min(100, total)), hits


def norm_url(u):
    """추적 파라미터 제거 + 프로토콜·www 통일. 기사 중복 판정용."""
    if not u:
        return ''
    u = re.sub(r'^https?://', '', u)
    u = re.sub(r'^www\.', '', u)
    base, _, qs = u.partition('?')
    keep = [kv for kv in qs.split('&')
            if kv and not re.match(r'(utm_|fbclid|gclid|ref=|src=)', kv)]
    return base.rstrip('/') + (('?' + '&'.join(keep)) if keep else '')


def norm_title(s):
    """매체명 꼬리와 기호를 떼어 같은 사안을 묶는다. 완벽할 필요 없다 — 최종 필터는 사람이다."""
    s = re.sub(r'\s*[-\u2013|]\s*[^-\u2013|]{2,20}$', '', s or '')
    return re.sub(r'[^0-9a-z가-힣]', '', s.lower())


def rss_items(xml):
    out = []
    for m in re.finditer(r'<item>(.*?)</item>', xml, re.S):
        b = m.group(1)

        def tag(n):
            mm = re.search(r'<%s[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</%s>' % (n, n), b, re.S)
            return html_mod.unescape(re.sub(r'\s+', ' ', mm.group(1))).strip() if mm else None
        d, iso = tag('pubDate'), None
        if d:
            try:
                iso = email.utils.parsedate_to_datetime(d).date().isoformat()
            except Exception:
                pass
        out.append({'title': tag('title'), 'link': tag('link'),
                    'date': iso, 'media': tag('source')})
    return out


def http_text(url, timeout=45, session=False):
    if session:
        return _OPENER.open(url, timeout=timeout).read().decode('utf-8', 'ignore')
    req = urllib.request.Request(url, headers={'User-Agent': BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'ignore')


def gnews(query, hl='en', gl='US'):
    u = ('https://news.google.com/rss/search?q=%s&hl=%s&gl=%s&ceid=%s:%s'
         % (urllib.parse.quote(query), hl, gl, gl, hl))
    return rss_items(http_text(u))


def strip_tags(s):
    return re.sub(r'\s+', ' ', html_mod.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


# 쿼리 세트. 'ITS' 단독 검색은 쓰지 않는다 — 영어 소유격 its가 걸려
# 실측에서 66건 중 대부분이 오탐이었다.
GNEWS_EN = ['"intelligent transport system" when:7d',
            '"traffic management system" when:7d',
            '"traffic signal" upgrade OR contract when:7d',
            '"smart traffic" when:7d',
            '"toll collection" system when:7d',
            '"traffic monitoring" when:7d',
            'V2X OR "connected vehicle" deployment when:7d',
            'autonomous shuttle OR "autonomous bus" pilot when:7d']
GNEWS_KO = ['지능형교통체계 when:7d', 'C-ITS when:7d', '자율주행 수주 OR 계약 when:7d',
            '교통 관제 시스템 구축 when:7d', 'V2X 통신 when:7d']
GNEWS_POL = ['자율주행 법안 OR 개정 when:7d', '도로교통법 개정 when:7d',
             'C-ITS 사업 when:7d', '국토교통부 모빌리티 정책 when:7d',
             '자율주행 시범운행지구 when:7d']

BOARD_TYPE = {'A': '해외기사', 'B': '국내기사', 'P': '국내동향'}


def fetch_news(days=7):
    start = (today_kst() - timedelta(days=days)).isoformat()
    out, seen_url, seen_title = [], set(), set()

    def add(items, board, korean, source, min_score):
        for x in items:
            if not x.get('title') or not x.get('link'):
                continue
            if x.get('date') and x['date'] < start:
                continue
            nu, nt = norm_url(x['link']), norm_title(x['title'])
            if nu in seen_url or (nt and nt in seen_title):
                continue
            sc, hits = score_news(x['title'], korean)
            if source == 'ITS Korea':
                sc = min(100, sc + ASSOC_BASE)
                hits = hits + ['협회게시판']
            if sc < min_score:
                continue
            # 구글뉴스 제목은 "제목 - 매체명"으로 끝난다. 매체는 따로 표시하므로 뗀다.
            title = x['title']
            media = x.get('media')
            if media:
                title = re.sub(r'\s*[-\u2013]\s*' + re.escape(media) + r'\s*$', '', title)
            title = re.sub(r'\s*[-\u2013]\s*[\w.]+\.(com|net|kr|co\.kr)\s*$', '', title)
            # 매체가 붙이는 섹션 꼬리 — "… > 뉴스", "… | 종합" 류
            title = re.sub(r'\s*[>\u203a|]\s*(뉴스|종합|속보|기사|홈)\s*$', '', title).strip()
            title = re.sub(r'\s*[-\u2013|]\s*$', '', title).strip()
            seen_url.add(nu)
            seen_title.add(nt)
            out.append({
                'schema_version': SCHEMA_VERSION,
                # id를 링크 해시로 만들면 안 된다 — 구글뉴스 RSS 링크는 요청마다 바뀌는
                # 불투명 토큰이라 같은 기사가 매번 새 id를 받고, 매일 '신규'로 다시 뜬다.
                # 정규화한 제목이 훨씬 안정적이다.
                'id': 'news-%s' % hashlib.md5((nt or nu).encode('utf-8')).hexdigest()[:12],
                'kind': 'news', 'board': board, 'source': source,
                'type': BOARD_TYPE[board],
                'country': None, 'country_ko': None,
                'title': title, 'media': media, 'org': media,
                'published': x.get('date') or today_kst().isoformat(),
                'deadline': None, 'budget': None, 'ref_no': None,
                'link': x['link'], 'cpv': [],
                'score': sc, 'score_hits': hits, 'already_posted': False,
            })

    # 쿼리 18개를 한 덩어리로 돌리면 하나만 실패해도 news 수집 전체가 죽는다.
    # 쿼리마다 격리하고 1초씩 띄운다.
    plan = ([(q, 'A', False, 'en', 'US', 12) for q in GNEWS_EN]
            + [(q, 'B', True, 'ko', 'KR', 20) for q in GNEWS_KO]
            + [(q, 'P', True, 'ko', 'KR', 20) for q in GNEWS_POL])
    for i, (q, board, korean, hl, gl, floor) in enumerate(plan):
        try:
            add(gnews(q, hl, gl), board, korean, 'Google News', floor)
        except Exception as e:
            print('  구글뉴스 쿼리 실패 [%s]: %s' % (q, e), file=sys.stderr)
        if i < len(plan) - 1:
            time.sleep(1)

    # itskorea — 사람이 이미 골라놓은 목록 (type=8 국내동향, type=9 해외영문)
    for t, board, korean in [(8, 'B', True), (9, 'A', False)]:
        try:
            h = http_text('https://itskorea.kr/boardList.do?type=%d&currentPage=1' % t)
            anchor = re.compile(r'boardDetail\.do\?type=%d&idx=(\d+)[^>]*>(.*?)</a>' % t, re.S)
            for m in anchor.finditer(h):
                idx, title = m.group(1), strip_tags(m.group(2))
                title = re.sub(r'\s*새글$', '', title)
                win = h[m.end():m.end() + 700]           # 날짜는 제목 뒤 <dd>에 있다
                dm = re.search(r'(20\d\d[-.]\d\d[-.]\d\d)', win)
                add([{'title': title,
                      'link': 'https://itskorea.kr/boardDetail.do?type=%d&idx=%s' % (t, idx),
                      'date': dm.group(1).replace('.', '-') if dm else None,
                      'media': 'ITS Korea'}],
                    board, korean, 'ITS Korea', 0)   # 협회가 이미 골라놓은 목록
        except Exception as e:
            print('  itskorea type=%d 실패: %s' % (t, e), file=sys.stderr)

    # 국토교통부 보도자료 — 세션 쿠키 없으면 307 리다이렉트 루프에 빠진다
    try:
        h = http_text('https://www.molit.go.kr/USR/NEWS/m_71/lst.jsp', session=True)
        for href, title in re.findall(r'<a[^>]+href="([^"]*dtl[^"]*)"[^>]*>(.*?)</a>', h, re.S):
            add([{'title': strip_tags(title), 'media': '국토교통부', 'date': None,
                  'link': 'https://www.molit.go.kr/USR/NEWS/m_71/' + href.replace('&amp;', '&')}],
                'P', True, '국토교통부', 6)   # 부처 원문 — 교통 관련어 하나라도 걸리면 통과
    except Exception as e:
        print('  국토부 실패: %s' % e, file=sys.stderr)

    # 법제처 입법예고 — 교통 관련 분야만
    try:
        h = http_text('https://opinion.lawmaking.go.kr/gcom/ogLmPp')
        for r in re.findall(r'<tr[^>]*>(.*?)</tr>', h, re.S):
            tds = [strip_tags(x) for x in re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)]
            if len(tds) < 3:
                continue
            if not any(k in ' '.join(tds) for k in
                       ['교통', '도로', '자동차', '철도', '자전거', '운수', '물류']):
                continue
            # 제목은 법령명이 든 칸이다. 길이만으로 고르면 날짜 칸이 잡힌다.
            cand = [t for t in tds if re.search(r'(법률안|령안|규칙안|입법예고|법 시행)', t)]
            if not cand:
                continue
            lm = re.search(r'/gcom/ogLmPp/(\d+)', r)
            add([{'title': max(cand, key=len), 'media': '법제처 입법예고', 'date': None,
                  'link': ('https://opinion.lawmaking.go.kr/gcom/ogLmPp/%s' % lm.group(1))
                          if lm else 'https://opinion.lawmaking.go.kr/gcom/ogLmPp'}],
                'P', True, '법제처', 0)
    except Exception as e:
        print('  법제처 실패: %s' % e, file=sys.stderr)

    # 근접중복 묶기 — 같은 사안을 여러 매체가 쓴다.
    # URL·제목 완전일치만으로는 못 잡는다(실측: 천안 자율주행버스 4건, 강남 심야택시 5건).
    # 상한이 하루 5건이라 한 사안이 상한을 통째로 먹는 게 실질 문제여서 여기서 묶는다.
    # 단어 토큰으로는 한국어가 안 묶인다 — '천안'과 '천안시'가 다른 토큰이라
    # 유사도가 0.33까지 떨어진다(실측). 문자 바이그램으로 본다.
    def tokens(t):
        t = re.sub(r'[^0-9a-z가-힣]', '', (t or '').lower())
        return set(t[i:i + 2] for i in range(len(t) - 1))

    out.sort(key=lambda x: -x['score'])
    kept = []
    for it in out:
        tk = tokens(it['title'])
        dup = False
        for k in kept:
            if k['board'] != it['board']:
                continue
            a, b = tk, k['_tk']
            if a and b and len(a & b) / len(a | b) >= 0.34:
                dup = True
                break
        if not dup:
            it['_tk'] = tk
            kept.append(it)
    for k in kept:
        k.pop('_tk', None)
    out = kept

    # 상한: 소스 계열별로 따로 센다.
    # ⚠ 같은 규칙이 index.html visible()에도 있다 (NEWS_CAP 블록).
    #   EXEMPT 목록·상한값·ITS Korea 계열 분리를 양쪽에서 같이 고쳐야 한다.
    #   채움 순서는 일부러 다르다 — 여기는 점수순, 화면은 '오늘 신규' 우선.
    # 한 통에 넣고 점수로 자르면 32점 뉴스가 24점 부처 원문을 밀어낸다(실측 확인).
    EXEMPT = ('국토교통부', '법제처')      # 주 3~5건, 검토 가치 높음 — 상한 없음
    capped, per = [], collections.Counter()
    for it in sorted(out, key=lambda x: (-x['score'], x['published'])):
        if it['source'] in EXEMPT:
            capped.append(it)
            continue
        k = (it['source'] == 'ITS Korea', it['board'], it['published'])
        if per[k] >= NEWS_DAILY_CAP:
            continue
        per[k] += 1
        capped.append(it)
    return capped


# 수집기 목록. fetch_news가 정의된 뒤에 와야 한다.
SOURCES = [
    ('ted', fetch_ted),
    ('worldbank', fetch_worldbank),
    ('news', fetch_news),
    # SAM.gov 보류: 창 내 215건 중 ITS 핵심어에 걸리는 건이 0건이었다.
    # 미국 ITS 발주는 주(state) DOT 소관이라 연방 조달망에 거의 오지 않는다.
    # 함수는 남겨둔다 — 나중에 주 단위 포털을 붙일 때 참고용.
    # ('samgov', fetch_samgov),
]


if __name__ == '__main__':
    main()
