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
from datetime import date, datetime, timezone, timedelta

SCHEMA_VERSION = 1
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
SEEN_PATH = os.path.join(DATA, 'seen.json')
KST = timezone(timedelta(hours=9))

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
          'detection', 'surveillance', 'bulletin board']),
    (6, ['bus rapid transit', 'public transport', 'mobility', 'road safety',
         'telematics', 'fare collection', 'ticketing', 'weigh-in-motion']),
]
NEGATIVE = ['cleaning', 'catering', 'insurance', 'audit', 'accounting', 'furniture',
            'stationery', 'medical', 'food', 'gender', 'legal advice',
            'security service', 'guard service', 'physical protection']


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

    # 등급별 첫 매칭은 만점, 두 번째 매칭은 절반만 — 같은 CPV 안에서 순위를 벌린다
    bonus = 0
    for weight, words in KEYWORD_BONUS:
        matched = [w for w in words if w in t]
        for i, w in enumerate(matched[:2]):
            bonus += weight if i == 0 else weight // 2
            hits.append(w)

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
    hdr = {'User-Agent': 'its-radar/1.0'}
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


def strip_html(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s or '')).strip()


def fetch_samgov(days=7):
    start = (date.today() - timedelta(days=days)).isoformat()
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
            desc = strip_html(d0.get('content'))[:600]
        _, bonus_hits = score_item(title + ' ' + desc, [])
        bonus = 0
        text = (title + ' ' + desc).lower()
        for weight, words in KEYWORD_BONUS:
            matched = [w for w in words if w in text]
            for i, w in enumerate(matched[:2]):
                bonus += weight if i == 0 else weight // 2
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
    start = (date.today() - timedelta(days=days)).isoformat()
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

    # 같은 사업(P번호)에서 여러 자료가 잡히면 가장 구체적인 것 하나만 (조달공고 > 문서)
    best = {}
    for it in out.values():
        k = it.get('project_id') or it['id']
        cur = best.get(k)
        if cur is None or (cur['stream'] == 'document' and it['stream'] == 'notice'):
            best[k] = it
    return list(best.values())


SOURCES = [
    ('ted', fetch_ted),
    ('worldbank', fetch_worldbank),
    # SAM.gov 보류: 창 내 215건 중 ITS 핵심어에 걸리는 건이 0건이었다.
    # 미국 ITS 발주는 주(state) DOT 소관이라 연방 조달망에 거의 오지 않는다.
    # 함수는 남겨둔다 — 나중에 주 단위 포털을 붙일 때 참고용.
    # ('samgov', fetch_samgov),
]


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
            items.extend(got)
            status[name] = {'ok': True, 'count': len(got), 'at': now.isoformat()}
            print('[%s] %d건' % (name, len(got)), file=sys.stderr)
        except Exception as e:
            status[name] = {'ok': False, 'count': 0, 'at': now.isoformat(), 'error': str(e)[:200]}
            print('[%s] 실패: %s' % (name, e), file=sys.stderr)

    # id 기준 중복 제거 (소스 간 병합)
    merged = {}
    for it in items:
        if it['id'] not in merged:
            merged[it['id']] = it
    items = list(merged.values())

    new_count = apply_first_seen(items, seen, today)
    items.sort(key=lambda x: (-x['score'], x.get('deadline') or '9999'))

    if not items:
        print('수집 결과 0건 — 실패 처리', file=sys.stderr)
        sys.exit(1)

    with open(os.path.join(DATA, 'tenders', '%s.json' % today), 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    with open(SEEN_PATH, 'w', encoding='utf-8') as f:
        json.dump(seen, f, ensure_ascii=False, indent=1, sort_keys=True)

    index = {'schema_version': SCHEMA_VERSION, 'last_success': now.isoformat(), 'sources': status,
             'tenders': sorted(os.listdir(os.path.join(DATA, 'tenders')))}
    with open(os.path.join(DATA, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    print('총 %d건 (신규 %d건) → data/tenders/%s.json' % (len(items), new_count, today), file=sys.stderr)


if __name__ == '__main__':
    main()
