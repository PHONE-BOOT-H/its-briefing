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
from datetime import datetime, date, timezone, timedelta

SCHEMA_VERSION = 3
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
SEEN_PATH = os.path.join(DATA, 'seen.json')
BOARD_CACHE_PATH = os.path.join(DATA, 'board_cache.json')
ADB_CACHE_PATH = os.path.join(DATA, 'adb_cache.json')
NEWS_ORIG_CACHE_PATH = os.path.join(DATA, 'news_orig_cache.json')
NOTES_PATH = os.path.join(DATA, 'notes.json')
FLAGS_PATH = os.path.join(DATA, 'flags.json')

# 스키마 v2 빈 값 원칙
# ────────────────────────────────────────────────────────────
# 채울 수 없는 항목은 비운다(None). 추정치·유사값으로 메꾸지 않는다.
# 사업명이 없다고 계약건명을 넣거나, 예산이 없다고 사업 총액을 대신 넣는 식은 금지.
# 값의 의미가 소스마다 다르면 별도 필드로 나눈다(budget.is_project_total).
# 화면은 빈 값을 '정보 없음 — 원문 확인'으로 보여주고, 등록문 복사도 빈칸 그대로 둔다.
# 비어 있는 것이 잘못 채워진 것보다 낫다 — 담당자가 원문을 열어보게 만드는 게 목적이다.
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
    "Cote d'Ivoire": '코트디부아르', 'Burkina Faso': '부르키나파소', 'Mali': '말리',
    'Benin': '베냉', 'Togo': '토고', 'Sierra Leone': '시에라리온', 'Djibouti': '지부티',
    'Turkiye': '튀르키예', 'Serbia': '세르비아', 'Moldova': '몰도바', 'Kosovo': '코소보',
    'Bosnia and Herzegovina': '보스니아헤르체고비나', 'Central Africa': '중앙아프리카',
    'Papua New Guinea': '파푸아뉴기니', 'Azerbaijan': '아제르바이잔', 'Timor-Leste': '동티모르',
    'Regional': '다국가', 'Lao PDR': '라오스', 'Cambodia': '캄보디아', 'Myanmar': '미얀마',
    'Thailand': '태국', 'Mongolia': '몽골', 'Fiji': '피지', 'Maldives': '몰디브',
    'Armenia': '아르메니아', 'Tajikistan': '타지키스탄', 'Turkmenistan': '투르크메니스탄',
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

# 비ITS 도메인 감점 — 섹터 태그는 교통인데 실체가 수자원·환경인 사업.
# 실측: 'Jamuna River Sustainable Management Project'(방글라데시 P172499)가
# 조달예측 기본점 40을 그대로 받아 상위권에 올라왔다.
# 지우지 않고 깎기만 한다 — 케냐 Horn of Africa Gateway(P161305)처럼
# 제목만으로는 ITS인지 알 수 없는 진짜 건이 있기 때문이다.
DOMAIN_NEG = ['river', 'waterway', 'water supply', 'flood', 'embankment', 'irrigation',
              'dredg', 'sanitation', 'sewer', 'drainage', 'watershed']


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


def score_item(title, cpv_list, base_extra=0):
    """CPV 기본점 + 제목 키워드 보너스 - 잡화 감점. 0~100.

    base_extra는 소스가 주는 기본점(World Bank·ADB 계열)이다. 밖에서 더하면
    감점이 0에서 잘려 사라진다 — 자무나 건이 40점 그대로 살아남던 경로다.
    """
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

    # 비ITS 도메인: 강한 ITS 신호(12점 이상 키워드나 CPV 기본점)가 없을 때만 깎는다.
    # 도로공사에 딸린 배수(drainage) 같은 부수 단어로 진짜 ITS 건을 깎지 않기 위해서다.
    if bonus < 12 and base == 0:
        for w in DOMAIN_NEG:
            if w in t:
                penalty += 20
                hits.append('-' + w)
                break

    # 잡화 조달: CPV가 잔뜩인데 ITS 비중이 낮으면 본체가 ITS가 아니다
    ratio = its_ratio(cpv_list)
    n_cpv = len(cpv_list or [])
    # CPV 15개 이상이면서 ITS 비중이 낮을 때만 감점.
    # 대형 ITS 사업은 통신장비·CCTV·건설 CPV가 딸려붙어 비율이 떨어지므로
    # 개수 조건 없이 비율만 보면 진짜 ITS 사업을 깎는다 (브르노 ITS 3단계 사례).
    if n_cpv >= 15 and ratio < 0.25:
        penalty += 40
        hits.append('-잡화(CPV%d개, ITS%.0f%%)' % (n_cpv, ratio * 100))

    return max(0, min(100, base_extra + base + bonus - penalty)), hits


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

# TED 발주기관 유형 코드 → 한글. 모르는 코드는 원문 그대로 둔다(빈 값 원칙: 지어내지 않는다).
TED_ORG_TYPE = {'la': '지방정부', 'ra': '광역정부', 'cga': '중앙정부', 'body-pl': '공법인',
                'pub-undert': '공기업', 'pub-undert-la': '지방 공기업',
                'pub-undert-ra': '광역 공기업', 'pub-undert-cga': '중앙 공기업',
                'body-pl-la': '지방 공법인', 'body-pl-ra': '광역 공법인',
                'body-pl-cga': '중앙 공법인', 'def-cont': '국방', 'eu-ins-bod-ag': 'EU기관',
                'org-sub': '보조금 수급기관', 'int-org': '국제기구'}


# eForms 코드값 → 한글. 게시판 양식에 그대로 들어가는 말이라 국문으로 옮겨 둔다.
# 모르는 코드는 원문 그대로 남긴다(빈 값 원칙: 지어내지 않는다).
TED_CODE = {
    'gen-pub': '일반 공공 서비스', 'la': '지방 자치 단체', 'ra': '광역 자치 단체',
    'cga': '중앙정부', 'body-pl': '공법인', 'pub-undert': '공기업',
    'none': '제한 없음', 'no-eu-funds': 'EU 기금 지원 없음', 'eu-funds': 'EU 기금 지원',
    'fa-wo-rc': '프레임워크 계약(경쟁 재개 없음)', 'fa-w-rc': '프레임워크 계약(경쟁 재개 있음)',
    'fa-mix': '프레임워크 계약(혼합)', 'none-fa': '프레임워크 아님',
    'open': '일반경쟁', 'restricted': '제한경쟁', 'neg-w-call': '공고 있는 협상',
    'comp-dial': '경쟁적 대화', 'innovation': '혁신 파트너십',
    'min-score': '최소 점수 기준', 'weight': '가중치',
    'epo-procurement-document': '입찰서류에 명시', 'epo-notice': '공고에 명시',
    'epo-sub-espd': 'ESPD로 제출',
    'quality': '품질', 'price': '가격', 'cost': '비용',
    'YEAR': '년', 'MONTH': '개월', 'WEEK': '주', 'DAY': '일',
    'supplies': '물품', 'services': '용역', 'works': '공사',
}


def ted_code(v):
    """코드 하나 또는 목록을 한글로. 못 옮기면 원문 그대로."""
    v = first_of(v)
    return TED_CODE.get(v, v) if v is not None else None


def ted_period(value, unit):
    """4 + YEAR → '4년'. 값이 없으면 None."""
    v, u = first_of(value), first_of(unit)
    if not v:
        return None
    return '%s%s' % (v, TED_CODE.get(u, u or ''))


def ted_award_method(values):
    """낙찰 기준 코드 → 한글 한 줄. 값이 없으면 None (부처 서식 그대로 비운다)."""
    v = set(values or [])
    if not v:
        return None
    if 'price' in v and ('quality' in v or 'cost' in v):
        return '종합평가(가격+기술)'
    if v == {'price'}:
        return '최저가'
    if 'quality' in v:
        return '기술평가'
    return None
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
                   'estimated-value-proc', 'estimated-value-cur-proc', 'contract-nature',
                   # v2 추가분. 셋 다 이미 응답에 오던 값인데 요청 목록에 없어서 버리고 있었다.
                   'buyer-legal-type', 'deadline-receipt-tender-time-lot',
                   'award-criterion-type-lot',
                   # 상세보기(게시판 양식)용. 전부 코드값·숫자·연락처라 번역이 필요 없다.
                   # 쿠네오 560665-2026으로 실측해 실제로 오는 것만 넣었다.
                   'buyer-email', 'organisation-tel-buyer', 'buyer-country-sub',
                   'duration-period-value-lot', 'duration-period-unit-lot',
                   'gpa-lot', 'reserved-procurement-lot', 'eu-fund-lot',
                   'framework-agreement-lot', 'framework-maximum-participants-number-lot',
                   'award-criterion-number-lot', 'award-criterion-number-threshold-lot',
                   'tender-validity-deadline-value-lot', 'tender-validity-deadline-unit-lot',
                   'procedure-type', 'authority-main-activity', 'selection-criteria-source'],
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
            'project_title': None,          # TED는 사업명·계약건명이 한 줄로 온다
            'org': pick_lang(n.get('buyer-name')),
            'org_type': TED_ORG_TYPE.get(first_of(n.get('buyer-legal-type')),
                                         first_of(n.get('buyer-legal-type'))),
            'budget': budget,
            'nature': ted_code(n.get('contract-nature')),
            'selection_method': ted_award_method(n.get('award-criterion-type-lot')),
            'source_scope': None,           # EU는 국적 제한 자체가 금지라 이 축이 없다
            'contact_name': None,           # eForms 어휘에 담당자 이름 필드가 없다
            'contact_email': first_of(n.get('buyer-email')),
            'contact_phone': first_of(n.get('organisation-tel-buyer')),
            'duration': ted_period(n.get('duration-period-value-lot'),
                                   n.get('duration-period-unit-lot')),
            'funder': None,                 # EU 조달공고에 자금원 항목이 없다
            # 게시판 '내용'의 참가자격·현지요건·낙찰방식 칸을 채우는 값들.
            # 전부 코드값·숫자라 번역이 필요 없다(실측: 쿠네오 560665-2026).
            'terms': {k: v for k, v in {
                '참여 제한': ted_code(n.get('reserved-procurement-lot')),
                'EU 기금': ted_code(n.get('eu-fund-lot')),
                'GPA 적용': ('예' if first_of(n.get('gpa-lot')) is True
                           else '아니오' if first_of(n.get('gpa-lot')) is False else None),
                '선정기준 출처': ted_code(n.get('selection-criteria-source')),
                '수행 지역(NUTS)': first_of(n.get('buyer-country-sub')),
                '프레임워크': {'none': '프레임워크 아님', 'fa-wo-rc': '프레임워크 계약(경쟁 재개 없음)',
                          'fa-w-rc': '프레임워크 계약(경쟁 재개 있음)', 'fa-mix': '프레임워크 계약(혼합)'}
                         .get(first_of(n.get('framework-agreement-lot'))),
                '최대 참여자 수': first_of(n.get('framework-maximum-participants-number-lot')),
                '입찰 유효기간': ted_period(n.get('tender-validity-deadline-value-lot'),
                                       n.get('tender-validity-deadline-unit-lot')),
                '낙찰 임계': (lambda v, t: None if not v else
                          ('%s (%s)' % (v, ted_code(t)) if first_of(t) else str(v)))(
                    first_of(n.get('award-criterion-number-lot')),
                    n.get('award-criterion-number-threshold-lot')),
                '절차 유형': ted_code(n.get('procedure-type')),
                '기관 주요활동': ted_code(n.get('authority-main-activity')),
            }.items() if v not in (None, '')} or None,
            'published': iso_date(n.get('publication-date')),
            'deadline': iso_date(n.get('deadline-receipt-request')),
            'deadline_time': (first_of(n.get('deadline-receipt-tender-time-lot')) or '')[:5] or None,
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
             'bid_description,submission_date,submission_deadline_date,contact_organization,'
             'submission_deadline_time,procurement_method_name,'
             'contact_name,contact_email,contact_phone_no,bid_reference_no')

# 세계은행은 qterm·섹터 태그가 넓어서 항공권 구매·난민사업까지 딸려온다.
# 제목에 교통 관련어가 없고 ITS 키워드 보너스도 0이면 버린다.
TRANSPORTISH = ['transport', 'road', 'traffic', 'mobility', 'rail', 'metro', 'bus',
                'highway', 'corridor', 'bridge', 'tunnel', 'logistic', 'freight']


def transportish(text):
    t = (text or '').lower()
    return any(w in t for w in TRANSPORTISH)
WB_PROJECT = 'https://search.worldbank.org/api/v3/projects?format=json&id=%s'
WB_CACHE_PATH = os.path.join(DATA, 'wb_project_cache.json')


def wb_project(pid, cache):
    """사업 단위 정보. 조달공고에는 자금원·총사업비가 없어서 여기서 가져온다.

    사업 정보는 잘 안 바뀌므로 캐시한다. 공고 여러 건이 같은 사업을 가리키는 일이 흔해
    캐시가 없으면 같은 사업을 하루에 여러 번 조회하게 된다.
    """
    if not pid:
        return {}
    if pid in cache:
        return cache[pid]
    try:
        d = http_json(WB_PROJECT % urllib.parse.quote(pid))
        rows = list((d.get('projects') or {}).values())
        n = rows[0] if rows else {}
    except Exception as e:
        print('  WB 사업조회 실패 %s: %s' % (pid, e), file=sys.stderr)
        return {}
    num = lambda k: float(n.get(k) or 0)
    # 예산처 = 어느 창구에서 돈이 나왔나. 금액 필드로 판별한다(별도 필드가 없다).
    funder = None
    if num('idacommamt') > 0:
        funder = 'IDA (세계은행 양허성 창구)'
    elif num('curr_ibrd_commitment') > 0:
        funder = 'IBRD (세계은행)'
    elif num('grantamt') > 0:
        funder = '무상원조(Grant)'
    total = num('lendprojectcost') or num('totalamt')
    cache[pid] = {
        'funder': funder,
        'borrower': n.get('borrower'),
        'impagency': n.get('impagency'),
        'total': total if total > 1000 else None,   # lendprojectcost에 400 같은 오염값이 있다
        'closing': (n.get('closingdate') or '')[:10] or None,
    }
    return cache[pid]


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
    pcache = json.load(open(WB_CACHE_PATH, encoding='utf-8')) if os.path.exists(WB_CACHE_PATH) else {}
    before = len(pcache)

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
                # 게이트는 소스 기본점을 뺀 키워드 점수로 본다. 최종 점수는 기본점을
                # score_item 안에서 더해야 감점이 0에서 잘리지 않는다.
                raw, _ = score_item(ctx, [])
                if raw == 0 and not transportish(ctx):
                    continue
                sc, hits = score_item(ctx, [], base_extra=25)
                # 자금원·총사업비는 조달공고에 없다. 사업 단위로 한 번 더 조회한다(캐시).
                prj = wb_project(n.get('project_id'), pcache)
                out['wb-%s' % n['id']] = {
                    'schema_version': SCHEMA_VERSION, 'id': 'wb-%s' % n['id'], 'kind': 'tender',
                    'source': 'World Bank', 'type': '입찰', 'stream': 'notice',
                    'country': n.get('project_ctry_name'),
                    'country_ko': COUNTRY_NAME_KO.get(n.get('project_ctry_name'), n.get('project_ctry_name')),
                    'title': title,
                    # org는 발주기관이다. 사업명(project_name)을 넣고 있었다 —
                    # title과 같은 값 계열이라 컬럼이 통째로 무의미했다.
                    'org': n.get('contact_organization'),
                    'org_type': None,       # 조달공고에 기관 유형 필드가 없다
                    'project_title': n.get('project_name'),
                    'budget': None,         # 조달공고에 금액 필드 자체가 없다(사업 API는 총차관액이라 의미가 다르다)
                    'selection_method': n.get('procurement_method_name'),
                    'source_scope': None,
                    'contact_name': n.get('contact_name'),
                    'contact_email': n.get('contact_email'),
                    'contact_phone': n.get('contact_phone_no'),
                    'duration': None,
                    'funder': prj.get('funder'),
                    'terms': {k: v for k, v in {
                        '차주': prj.get('borrower'),
                        '시행기관': prj.get('impagency'),
                        '사업 총사업비(공동재원 포함)': ('USD %s' % format(int(prj['total']), ',')) if prj.get('total') else None,
                        '사업 종료 예정': prj.get('closing'),
                    }.items() if v} or None,
                    'published': nd, 'deadline': (n.get('submission_deadline_date') or '')[:10] or None,
                    'deadline_time': (n.get('submission_deadline_time') or '')[:5] or None,
                    'ref_no': n.get('project_id') or n['id'],
                    'project_id': n.get('project_id'),
                    'link': 'https://projects.worldbank.org/en/projects-operations/procurement-detail/%s' % n['id'],
                    'cpv': [], 'score': sc, 'score_hits': hits,
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
            sc, hits = score_item(title, [], base_extra=base)
            guid = x.get('guid') or x.get('id')
            pid = x.get('projectid')
            prj = wb_project(pid, pcache)
            out['wbd-%s' % guid] = {
                'schema_version': SCHEMA_VERSION, 'id': 'wbd-%s' % guid, 'kind': 'tender',
                'source': 'World Bank', 'type': ktype, 'stream': 'document',
                'country': x.get('count'),
                'country_ko': COUNTRY_NAME_KO.get(x.get('count'), x.get('count')),
                'title': title, 'org': prj.get('impagency'), 'budget': None,
                'selection_method': None, 'source_scope': None,
                'contact_name': None, 'contact_email': None, 'contact_phone': None,
                'duration': None, 'funder': prj.get('funder'),
                'terms': {k: v for k, v in {
                    '차주': prj.get('borrower'),
                    '사업 총사업비(공동재원 포함)': ('USD %s' % format(int(prj['total']), ',')) if prj.get('total') else None,
                    '사업 종료 예정': prj.get('closing'),
                }.items() if v} or None,
                'published': dt, 'deadline': None,
                'ref_no': pid or guid, 'project_id': pid,
                'link': 'https://documents.worldbank.org/en/publication/documents-reports/documentdetail/%s' % guid,
                'cpv': [], 'score': sc, 'score_hits': hits,
                'already_posted': False,
            }

    if len(pcache) != before:
        with open(WB_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(pcache, f, ensure_ascii=False, indent=1, sort_keys=True)
        print('  WB 사업조회: 캐시 %d건, 신규 %d건' % (before, len(pcache) - before))

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
        # id 계산식이 바뀐 회차의 이월. 옛 id로 이미 본 항목이면 그 날짜를 물려받는다.
        # 없으면 전건이 '오늘 신규'가 되어 재노출 금지 설계가 하루치 통째로 무너진다.
        old = it.pop('id_legacy', None)
        if it['id'] not in seen and old and old in seen:
            seen[it['id']] = seen[old]
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

    # 사람이 원문을 읽고 채우는 칸(참가자격·공동수급·기술규격 요약 등).
    # 수집기가 매일 파일을 새로 쓰므로 별도 파일에 두고 여기서 붙인다.
    # 파일이 없으면 전건 None — 빈 값 원칙대로 비워둔다.
    notes = {}
    if os.path.exists(NOTES_PATH):
        with open(NOTES_PATH, encoding='utf-8') as f:
            notes = json.load(f)
    for it in items:
        it['notes'] = notes.get(it['id'])

    # 사람이 직접 접는 목록. 규칙으로 못 잡는 건을 여기에 적는다.
    # 실사용 사례: 프놈펜 노상주차 기고문은 협회 게시판 경유라 제목·매체 어디에도
    # 기고 표시가 없다(원문 KIRIPOST). 도메인 목록은 반복범, 이 파일은 일회성이다.
    # 형식: { "news-abc123": "기고·칼럼" }
    flags = {}
    if os.path.exists(FLAGS_PATH):
        with open(FLAGS_PATH, encoding='utf-8') as f:
            flags = json.load(f)
    for it in items:
        manual = flags.get(it['id'])
        if manual and not it.get('flag'):
            it['flag'] = manual
            it['score'] = max(0, it.get('score', 0) - OPINION_PENALTY)
            it['score_hits'] = (it.get('score_hits') or []) + ['-수동표시']
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





# ── 소스: ADB (아시아개발은행)
#
# 게시판 기등록 400건의 국가 분포에서 아시아가 47%(인도 17·인도네시아 14·필리핀 10)인데
# 아시아 MDB가 빠져 있었다. World Bank와 사업이 겹치지 않아 순기여가 그대로 더해진다.
#
# 주의: 공식 API가 아니다. adb.org가 공개 JS 번들에 박아둔 검색 토큰을 그대로 쓴다.
# 토큰이 바뀌면 401이 나고 소스 상태가 ok=false로 남는다(화면 상단에 표시된다).
ADB_TOKEN = '2a076eb3a48fd68fc78506c1a16a5d5000da76e4'
ADB_URL = ('https://searchcloud-2-ap-southeast-1.searchstax.com/29847/tenders-11959/emselect'
           '?q=*:*&fq=sm_fct_sector:%22Transport%22'
           '&fq=ds_date_posted:%5BNOW-{days}DAYS%20TO%20NOW%5D'
           '&sort=ds_date_posted%20desc&rows=200&wt=json'
           '&fl=id,tm_X3b_en_title,tm_X3b_en_country,tm_X3b_en_project_number,tm_X3b_en_type,'
           'tm_X3b_en_status,ds_date_posted,ds_date_closing,ss_url,ss_csrn_url,'
           # 아래 둘은 인덱스에 있는데 요청 목록에서 빠져 있었다. 그래서 발주기관이 0%였다.
           'tm_X3b_en_project_title,tm_X3b_en_executing_agency')

# 공고 유형 → 게시판 유형. 개인 컨설턴트도 버리지 않는다 —
# 파키스탄 ITS Specialist처럼 개인 채용이 게시판에 올라간 전례가 있다. 거르는 건 사람이 한다.
ADB_TYPE = {'Invitation for Bids': '입찰', 'Firm': '입찰', 'Individual': '입찰',
            'General Procurement Notice': '조달예측', 'Other Notice': '조달예측'}


def strip_tail(name, tail):
    """기관명 끝에 붙어온 국가명을 뗀다. 못 떼면 원문 그대로 둔다."""
    if name and tail and name.endswith(tail):
        return name[:-len(tail)].strip() or name
    return name


def parse_csrn(html, country=None):
    """ADB 공고 상세(CSRN)에서 API가 안 주는 항목만 뽑는다.

    실측 6/6 추출: 발주기관 / 예산(USD) / International·National / 선정방식.
    못 뽑은 항목은 넣지 않는다 — 빈 값 원칙. 화면이 '정보 없음'으로 보여준다.
    """
    t = re.sub(r'<[^>]+>', ' ', html).replace('&nbsp;', ' ')
    t = re.sub(r'\s+', ' ', t)
    d = {}
    m = re.search(r'Contact Person View Details\s*(.{3,120}?)\s+(?:Executing|Implementing) Agency', t)
    if m:
        # 기관명 뒤에 국가 칸이 붙어 나온다('Committee of Roads Kazakhstan').
        # 국가는 호출부에서 뗀다 — 캐시에는 원문 그대로 둔다.
        d['org'] = m.group(1).strip() or None
    m = re.search(r'Budget\s+USD\s+([\d,]+)', t)
    if m:
        d['budget'] = {'currency': 'USD', 'amount': float(m.group(1).replace(',', '')),
                       'is_project_total': False}   # 해당 계약 추정액이다(사업 총액 아님)
    m = re.search(r'\bSource\s+(International|National)\b', t)
    if m:
        d['source_scope'] = m.group(1)
    m = re.search(r'Selection Method\s+(.{3,60}?)\s+Source\b', t)
    if m:
        d['selection_method'] = m.group(1).strip()
    return d


def load_adb_cache():
    if os.path.exists(ADB_CACHE_PATH):
        with open(ADB_CACHE_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {}


def adb_detail(url, cache):
    """상세 1건. 공고는 한 번 올라오면 안 바뀌므로 캐시한다(하루 새로 조회는 몇 건뿐)."""
    if url in cache:
        return cache[url]
    try:
        cache[url] = parse_csrn(http_text(url))
    except Exception as e:
        print('  ADB 상세 실패 %s: %s' % (url[-24:], e), file=sys.stderr)
        return {}          # 실패는 캐시하지 않는다 — 다음 실행에서 다시 시도
    return cache[url]


def fetch_adb(days=7):
    d = http_json(ADB_URL.format(days=days),
                  headers={'Authorization': 'Token ' + ADB_TOKEN})
    cache = load_adb_cache()
    fresh = 0
    out = []
    for doc in (d.get('response') or {}).get('docs', []):
        title = first_of(doc.get('tm_X3b_en_title'))
        if not title:
            continue
        subtype = first_of(doc.get('tm_X3b_en_type')) or ''
        # 낙찰 완료건은 기회가 아니다. World Bank의 Contract Award 제외와 같은 기준.
        # 유형표에 없어서 지금은 '입찰'로 둔갑한다 — 조회 창을 늘리면 바로 드러난다.
        if subtype == 'Contracts Awarded':
            continue
        country = first_of(doc.get('tm_X3b_en_country'))
        pnum = first_of(doc.get('tm_X3b_en_project_number')) or ''
        raw, _ = score_item(title, [])
        # 섹터 태그가 다부문 사업에도 붙는다 — 인도 건 12개 중 관광·스포츠 전문가 채용이
        # Transport로 잡혔다. World Bank 조달공고와 같은 게이트를 건다.
        if raw == 0 and not transportish(title):
            continue
        sc, hits = score_item(title, [], base_extra=25)
        node = (doc.get('ss_url') or '').lstrip('/')
        link = doc.get('ss_csrn_url') or ('https://www.adb.org/' + node)
        # 발주기관·예산·국제/국내는 API 인덱스에 없다. 상세를 한 번 더 읽어야 나온다.
        # 상세가 없는 공고(IFB 계열, 실측 40%)는 비운다 — 지어내지 않는다.
        det = {}
        if 'csrn' in link.lower():
            if link not in cache:
                fresh += 1
            det = adb_detail(link, cache)
        out.append({
            'schema_version': SCHEMA_VERSION,
            'id': 'adb-%s' % (doc.get('id') or node),
            'kind': 'tender', 'source': 'ADB',
            'type': ADB_TYPE.get(subtype, '입찰'),
            'subtype': subtype,                     # 개인/기업 구분은 화면 배지로만
            'stream': 'notice',
            'country': country,
            'country_ko': COUNTRY_NAME_KO.get(country, country),
            'title': title,
            'org': strip_tail(det.get('org'), country)
                   or first_of(doc.get('tm_X3b_en_executing_agency')),
            'org_type': None,
            'project_title': first_of(doc.get('tm_X3b_en_project_title')),
            'budget': det.get('budget'),
            'selection_method': det.get('selection_method'),
            # International이면 외국기업 참여 가능, National이면 현지업체 한정이다.
            # 실측 표본의 61%가 National — 안 읽으면 못 들어가는 건을 절반 넘게 섞어 보여준다.
            'source_scope': det.get('source_scope'),
            'published': (doc.get('ds_date_posted') or '')[:10] or None,
            'deadline': (doc.get('ds_date_closing') or '')[:10] or None,
            'deadline_time': None,
            'ref_no': pnum, 'project_id': pnum,
            'link': link,
            'cpv': [],
            'score': sc,                            # World Bank 공고와 같은 기준선(25)
            'score_hits': hits,
            'already_posted': False,
        })
    if fresh:
        with open(ADB_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
        print('  ADB 상세: 캐시 %d건, 신규 조회 %d건' % (len(cache) - fresh, fresh))
    return out


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
          '레벨4', '모빌리티', '차량통신', '입법예고', '시행령', '개정',
          # 사업이 실제로 움직인 신호. 분류표(CAT_KO)에는 있었는데 점수표에 빠져 있었다 —
          # 그래서 '하이패스 첫 수출' 기사가 79건 중 43위였다(실측 2026-08-27).
          '수주', '수출', '발주', '예타', '예비타당성', '착공', '개통', '준공',
          '실증', '무정차', '다차로']),
    (6, ['교통', '도로', '철도', '물류', '대중교통', '스마트시티', '국토교통부',
         '자동차', '고속도로', '지하철', 'ktx', '버스', '택시', '화물', '운전',
         'npu', '반도체', '카메라', '관제', '통신']),   # '단속'은 12점 등급에만 둔다
]
# 행사 참가·수상 홍보 기사. 지우지 않고 순위만 낮춘다 — 실제 사업 뉴스를 밀어내는 게 문제다.
# 실측(2026-08-27): 국내 점수 상위 10건 중 5건이 같은 행사(AME 2026) 기사였다.
KO_EVENT = ['참가', '개막', '부스', '전시회', '시연회', '선봬', '선보여', '참관',
            '간담회', '기념식', '수상', '인증 획득', '포럼 개최', '세미나 개최',
            '설명회', '체험행사', '출범', '개관',
            # 행사 이름 자체가 제목에 박힌 기사. 1차 시도에서 이것들이 그대로 상위에 남았다
            # ('[포토] 2026 자율주행모빌리티산업전' 32점).
            '산업전', '박람회', '엑스포', '[포토]', '컨퍼런스', '시상식', '모빌리티 위크']
# 위 말이 있어도 이게 같이 있으면 안 깎는다. 행사장에서 발표된 실제 계약이 있다.
KO_BIZ = ['수주', '수출', '계약', '착공', '개통', '준공', '예타', '예비타당성',
          '발주', '협약', 'mou', '통과', '지정', '승인']
EVENT_PENALTY = 20
BODY_CAP = 12        # 본문 가산 상한. 본문은 길어서 상한이 없으면 제목을 압도한다
BODY_LEAD = 500      # 본문 앞부분만 본다. 뒤로 갈수록 관련기사·홍보문구가 섞인다
# 본문에서는 이 말들만 센다. 전체 키워드표를 본문에 그대로 돌리면 엉뚱한 기사가 오른다 —
# 의료로봇 MOU 기사가 관련기사 링크의 '수주' 때문에 +12를 받았다(실측 1차 시도).
# 제목에 안 실리기 쉬운데 실체를 정하는 말만 남긴다.
# '수주'·'발주'·'실증'은 본문에서 뺐다. 너무 흔해서 무관한 보도자료가 올라온다 —
# '제273차 대외경제장관회의'가 본문의 '수주' 한 마디로 +12를 받았다(실측 2차 시도).
# 제목에 있을 때만 센다.
BODY_WORDS = [(12, ['하이패스', '무정차', '다차로', '스마트톨링', '통행료', '요금징수',
                    '지능형교통체계', 'c-its', '교통관제', '관제센터', '교통정보센터',
                    '예비타당성', '예타', '착공', '준공', '개통', '수출'])]

KO_NEGATIVE = ['부고', '인사이동', '주가', '증시', '코스피', '분양', '아파트값', '날씨', '부동산',
               '시장 규모', '시장규모', '점유율', '시장 전망', '리포트 발간', '보고서 발간',
               # 항공교통관제는 ITS가 아니다. 영어 쪽 'air traffic' 차단어의 한국어 짝이
               # 빠져 있어서 '항공교통관제 시뮬레이터 체험' 기사가 29점으로 통과했다.
               '항공교통관제', '항공관제', '항공교통', '항공 교통', '항공편']

# 영어 기사 전용 차단어. 시장조사 보도자료와 항공교통관제(ITS 아님)를 걸러낸다.
EN_NEWS_NEGATIVE = ['market size', 'market growth', 'market share', 'market report',
                    'market to reach', 'market analysis', 'cagr', 'forecast to 20',
                    'air traffic', 'air navigation']

# ── 기사 ITS 카테고리 (카드 배지 한 칸에 들어가는 폐쇄 어휘)
#
# 해외 보드와 국내 보드는 규칙을 나눈다. 실측에서 결정 키워드 교집합이 0이었다 —
# 해외 87건 중 82건이 영어 단어로, 국내 60건 전부가 한국어 단어로 갈렸다.
# 하나로 합쳐두면 이름만 같은 분류기 두 개를 한 표에 적어놓은 셈이 된다.
#
# 순서 = 트리거어의 모호도 순. 위일수록 우연히 안 걸리는 말이다.
# 첫 매칭이 이긴다(first-match-wins). 실측상 기사 셋 중 하나가 두 카테고리에
# 동시에 걸리므로, 이 순서가 곧 분류 정책이다.
CAT_EN = [
    ('C-ITS·V2X', ['c-its', 'v2x', 'v2i', 'v2v', 'telematics', 'dsrc',
                   'connected vehicle', 'connected car', 'cooperative its']),
    # ITS 일반(총회·표준·디지털트윈·지도)을 담을 칸이 없어서 전부 '기타'로 떨어졌다.
    # 협회 게시판 감사에서 나온 구멍이다.
    ('기술·표준', ['its world congress', 'standardisation', 'standardization',
                'digital twin', 'geospatial', 'hd map', 'interoperability']),
    ('요금·통행료', ['toll', 'road pricing', 'congestion charge', 'mlff', 'free flow',
                 'fastag', 'electronic fee']),
    ('단속·집행', ['enforcement', 'anpr', 'number plate', 'licence plate', 'license plate',
                'speed camera', 'red light', 'overspeed', 'violation',
                # 눈검사에서 잡힌 것 — 'school zone cameras cut speeding'이 신호·관제로,
                # 'work-zone safety camera pilot'이 기타로 갔다.
                'speeding', 'zone camera', 'safety camera', 'traffic fine']),
    ('신호·관제', ['traffic signal', 'traffic light', 'signalling', 'signaling',
                'traffic management', 'traffic control', 'intersection',
                'traffic monitoring', 'variable message', 'passenger information',
                'intelligent transport', 'its system', 'surveillance', 'work zone',
                'school zone', 'road safety']),
    ('자율주행', ['autonomous', 'self-driving', 'selfdriving', 'driverless',
               'robotaxi', 'robo-taxi', 'automated driving', 'level 4',
               'waymo', 'smart-driving', 'smart driving']),
    ('산업·투자', ['ipo', 'acquisition', 'merger', 'stake', 'funding round',
                'investment', 'wins contract', 'awarded contract']),
    ('대중교통·물류', ['public transport', 'bus rapid', 'brt', 'metro', 'tram',
                  'light rail', 'railway', 'freight', 'logistics', 'parking',
                  'maas', 'mobility as a service', 'seaport']),
]
# 국내 보드는 산업·투자를 자율주행 위에 둔다.
# 실측: 자율주행이 국내 보드의 61~73%를 먹는데 그중 상당수가 상장·지분·수주 기사였다
# ('라이드플럭스 코스닥 상장 예심 청구'가 자율주행 배지를 달던 문제).
CAT_KO = [
    ('정책·법제도', ['입법예고', '시행령', '시행규칙', '개정안', '개정령', '법률안', '제정안',
                 '고시', '국회 통과', '법 개정', '하위법령']),
    ('C-ITS·V2X', ['c-its', 'v2x', 'v2i', '차량통신', '차량사물통신', '협력주행', '텔레매틱스']),
    ('산업·투자', ['상장', '코스닥', '코스피 이전', '기업공개', '지분', '인수', '매각',
                '투자 유치', '시리즈 a', '시리즈 b', '수주', '계약 체결', '공급 계약',
                '양해각서', 'mou']),
    ('요금·통행료', ['통행료', '요금징수', '하이패스', '스마트톨링', '다차로', '요금소', '교통카드']),
    ('단속·집행', ['단속', '과속', '무인단속', '음주운전', '과적', '불법주정차', '위반',
                'cctv', '무인교통단속']),
    ('신호·관제', ['신호', '교차로', '교통관제', '관제센터', '관제시스템', '교통정보', '교통량',
                '혼잡', '돌발상황', '정보제공', '스마트교차로', '교통안전']),
    ('자율주행', ['자율주행', '자율차', '로보택시', '무인주행', '레벨4', '자율운행', '자율협력',
               '웨이모', '오토노머스']),
    ('기술·표준', ['세계총회', '디지털트윈', '디지털 트윈', '국제표준', '표준화', '정밀도로지도']),
    ('대중교통·물류', ['대중교통', '버스', 'brt', '철도', '지하철', 'ktx', '트램', '전철',
                  '택시', '물류', '화물', '주차', '파킹', '수요응답']),
]


def classify(title, board):
    """기사 제목 → ITS 카테고리. 규칙만 쓴다(LLM 금지).

    board A(해외 영문)와 B·P(국내)는 표를 따로 쓴다. 국내 표가 먼저 걸리지 않으면
    영어 표로 한 번 더 본다 — 국내 보드에도 영문 제목이 섞여 들어온다.
    """
    t = (title or '').lower()
    # 해외 보드에도 한글 제목이 온다 — ITS Korea 해외 게시판(type=9)과 번역된 기사다.
    # 영어 표만 걸면 '브라질 전기버스 1만 대 돌파', '샌프란시스코 자율주행 현주소'가
    # 통째로 기타로 떨어진다(눈검사에서 3건 확인).
    tables = [CAT_EN, CAT_KO] if board == 'A' else [CAT_KO, CAT_EN]
    for table in tables:
        for name, words in table:
            if any(w in t for w in words):
                return name
    return '기타'


# ── 기사 감점 목록 (운영하며 늘려가는 자리)
#
# 둘 다 '제외'가 아니라 감점이다. 원 기사가 진짜일 수 있고, 기고문에도 쓸 내용이 있다.
# 점수를 깎고 표시를 붙여 화면 아래로 가라앉히면 사람이 펼쳐서 판단할 수 있다.
#
# 재가공(도용) 의심 — 남의 기사를 그대로 옮겨 싣는 사이트.
# 실사용 발견: streamlinefeed.co.ke가 홍콩프리프레스 기사를 옮겨 실어 수집에 잡혔다.
# 새로 발견하면 이 목록에 한 줄 추가하면 된다. 매체명과 링크 도메인 양쪽을 본다.
REPOST_DOMAINS = ['streamlinefeed.co.ke']
REPOST_PENALTY = 20

# 기고·칼럼·사설 — 사실 보도가 아니라 의견이다.
# 실사용 발견: 프놈펜 노상주차 기고문이 뉴스로 통과했다.
OPINION_SIGNALS = ['commentary', 'opinion', 'editorial', 'op-ed', 'viewpoint',
                   '기고', '칼럼', '사설', '기자수첩', '오피니언', '시론', '기획']
OPINION_PENALTY = 15


def news_flags(title, media, link):
    """감점 사유와 크기. (감점, 표시, hits 항목)"""
    hay = ((media or '') + ' ' + (link or '')).lower()
    if any(d in hay for d in REPOST_DOMAINS):
        return REPOST_PENALTY, '재가공 의심', '-재가공의심'
    t = (title or '').lower()
    if any(w in t for w in OPINION_SIGNALS):
        return OPINION_PENALTY, '기고·칼럼', '-기고문'
    return 0, None, None


NEWS_DAILY_CAP = 5   # 보드별 하루 노출 상한
ASSOC_BASE = 12      # ITS Korea 게시판 기본점 — 협회가 ITS 관점에서 이미 고른 목록이다


def is_english(title):
    """제목이 영어인지. 라틴 문자 비율만 봐도 충분하다."""
    t = re.sub(r'[^0-9A-Za-z가-힣]', '', title or '')
    if not t:
        return False
    latin = sum(1 for c in t if c.isascii())
    return latin / len(t) > 0.7


def score_news(title, korean=False, body=''):
    """제목으로 채점하고, 원문 본문이 있으면 제목에 없던 말만 상한 내에서 더한다.

    제목만 보면 놓친다. '튀르키예 고속도로 15년 굴리는 도로공사…해외수주 7495억'의
    실체는 '한국형 하이패스 말레이시아 첫 수출'인데 그 말이 전부 본문에만 있었다(6점).
    본문을 그냥 다 세면 반대로 본문이 제목을 압도하므로 BODY_CAP으로 묶는다.
    """
    # 한국어 소스라도 제목이 영어면 영어 채점기를 태운다
    if korean and is_english(title):
        korean = False
    t = (title or '').lower()
    rules = KO_BONUS if korean else KEYWORD_BONUS
    total, hits = keyword_bonus(t, rules)
    event = korean and any(w in t for w in KO_EVENT) and not any(w in t for w in KO_BIZ)
    # 행사 기사에는 본문 가산을 주지 않는다. 행사 소개문이라 더할 값이 없고,
    # 주면 감점을 그대로 상쇄한다(실측 1차: 산업전 개막 기사가 44→41에 그쳤다).
    if body and not event:
        # 제목에서 이미 센 말은 뺀다. 같은 말이 본문에 반복될 뿐인 기사가 두 배로 오른다.
        rest = [(w, [x for x in words if x not in t]) for w, words in BODY_WORDS]
        btotal, bhits = keyword_bonus(body[:BODY_LEAD].lower(), rest)
        if btotal > 0:
            total += min(BODY_CAP, btotal)
            hits += ['본문:' + w for w in bhits[:2]]
    stop = KO_NEGATIVE if korean else (NEGATIVE + EN_NEWS_NEGATIVE)
    for w in stop:
        if w in t:
            total -= 25   # 기사에서는 차단어가 걸리면 사실상 탈락시킨다
            hits.append('-' + w)
    if event:
        total -= EVENT_PENALTY
        hits.append('-행사홍보')
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


# ── 협회 게시글의 원문 게재일
#
# 협회 게시판은 '올린 날'만 준다. 그 기사가 언제 나온 것인지는 안 준다.
# 실측(2026-08-27, 협회 해외기사 15건): 3건(20%)이 원문 기준 3일 이상 지난 것이었고
# 그중 하나는 516일 전 기사였다(SHIFFT, 원문 2025-03-28을 8/26에 게시).
# 게시일만 보고 고르면 '이번 주 기사'가 아닌 걸 올리게 된다 — 실제로 한 번 그랬다.
# 게시글 아래에는 관련기관 링크 14개가 모든 글에 똑같이 붙는다.
# 그냥 '첫 외부 링크'를 집으면 원문이 없는 글에서 국토교통부 대문을 원문이라고 집는다(실측).
BOARD_FOOTER = ('molit.go.kr', 'its.go.kr', 'roadplus.co.kr', 'kaia.re.kr', 'koti.re.kr',
                'krihs.re.kr', 'kict.re.kr', 'kor-kst.or.kr', 'kits.or.kr', 'itsa.org',
                'its-jp.org', 'its.dot.gov', 'ertico.com', 'itsasia-pacific.com')
BOARD_SKIP = ('itskorea', 'cdnjs', 'jsdelivr', 'w3.org', 'jquery', 'google',
              'facebook', 'twitter', 'linkedin', 'youtube')
# 원문 앵커는 글 안에 '출처 : … (원문보기)' 형태로 들어간다. 이게 있는 줄을 먼저 본다.
ORIG_A = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.{0,120}?)</a>', re.S)
STALE_DAYS = 7          # 이보다 오래된 원문은 화면에서 '지연'으로 표시한다


def article_date(page):
    """기사 페이지에서 원문 게재일(YYYY-MM-DD).

    사이드바 '관련 기사'의 날짜를 집지 않도록 headline이 붙은 JSON-LD 노드를 먼저 본다.
    제목 없이 datePublished만 긁으면 실측에서 Kapsch 건이 2026-02-25(사이드바)로 나왔다.
    """
    for blk in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
                          page, re.S):
        try:
            data = json.loads(blk)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            n = stack.pop()
            if not isinstance(n, dict):
                continue
            if isinstance(n.get('@graph'), list):
                stack.extend(n['@graph'])
            if n.get('headline') and n.get('datePublished'):
                d = str(n['datePublished'])[:10]
                if re.match(r'20\d\d-\d\d-\d\d$', d):
                    return d
    m = re.search(r'property="article:published_time"[^>]*content="(20\d\d-\d\d-\d\d)', page)
    if m:
        return m.group(1)
    d = re.findall(r'"datePublished"\s*:\s*"(20\d\d-\d\d-\d\d)', page)
    return max(d) if d else None


def article_body(page, limit=900):
    """페이지에서 본문 앞부분. 채점용이라 완벽할 필요 없다.

    긴 <p>만 모은다. 통째로 태그를 벗기면 사이트 메뉴가 섞여 엉뚱한 가산점이 붙고
    (실측: itsinternational 본문이 'News Products Features Categories…'로 시작),
    짧은 <p>는 대개 내비게이션·캡션이다.
    """
    page = re.sub(r'<(script|style)[^>]*>.*?</>', ' ', page, flags=re.S | re.I)
    ps = [strip_tags(x) for x in re.findall(r'<p[^>]*>(.*?)</p>', page, re.S | re.I)]
    ps = [x for x in ps if len(x) >= 40 and '출처' not in x[:20]]
    text = ' '.join(ps)
    if len(text) < 120:                      # <p>를 안 쓰는 사이트
        text = strip_tags(page)
    return re.sub(r'\s+', ' ', text)[:limit]


def orig_pub(board_url, cache):
    """협회 게시글 -> {원문 링크, 원문 게재일}. 실패하면 빈 값을 남긴다(빈 값 원칙)."""
    # 'body'가 없으면 본문 채점을 붙이기 전에 받은 캐시다. 다시 받는다.
    if board_url in cache and 'body' in cache[board_url]:
        return cache[board_url]
    rec = {'link': None, 'date': None, 'body': ''}
    ok = False
    try:
        # 엔티티를 먼저 푼다. 게시판 HTML은 링크를 &#034; 로 감싸 놓아서,
        # 푸는 순서를 뒤집으면 URL 꼬리에 따옴표가 붙어 원문이 404가 난다(실측 3/5 실패).
        h = html_mod.unescape(http_text(board_url, timeout=25))
        spare = None
        for m in ORIG_A.finditer(h):
            u = m.group(1).rstrip('.,)')
            if any(k in u for k in BOARD_SKIP):
                continue
            label = re.sub(r'<[^>]+>', '', m.group(2))
            if '원문' in label or '출처' in label:
                rec['link'] = u
                break
            if spare is None and not any(k in u for k in BOARD_FOOTER):
                spare = u
        # 원문 표시가 없는 글도 있다. 그때만 '고정 링크가 아닌 첫 외부 링크'로 물러선다.
        rec['link'] = rec['link'] or spare
        # 게시글 본문이 원문 발췌를 담고 있다. 외부 사이트가 막혀도 이건 늘 읽힌다.
        rec['body'] = article_body(h)
        ok = True
        if rec['link']:
            page = http_text(rec['link'], timeout=25)
            rec['date'] = article_date(page)
            rec['body'] = rec['body'] or article_body(page)
    except Exception as e:
        print('  원문조회 실패 %s: %s' % (board_url[-12:], e), file=sys.stderr)
    # 실패는 캐시에 남기지 않는다. 남기면 그날의 일시적 오류가 영구 빈 값이 된다
    # (실측: 307 한 번 난 게시글 2건이 링크·본문 없이 굳었다).
    if ok:
        cache[board_url] = rec
    return rec


def enrich_assoc(items):
    """협회 글에 원문 링크·게재일·본문을 채우고, 본문까지 넣어 다시 채점한다.

    ⚠ 반드시 근접중복 묶기와 상한 계산 '앞'에서 돌아야 한다. 점수가 바뀌므로,
    뒤에서 돌리면 옛 점수로 자른 결과 위에 새 점수를 덮어쓰게 된다.
    한 건에 요청 2회(게시글 + 원문)라 첫 실행만 무겁고 그 뒤로는 캐시가 받는다.
    """
    cache = {}
    if os.path.exists(NEWS_ORIG_CACHE_PATH):
        try:
            with open(NEWS_ORIG_CACHE_PATH, encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    before, looked, today = len(cache), 0, today_kst()
    for it in items:
        if it['source'] != 'ITS Korea':
            continue
        fresh = it['link'] not in cache or 'body' not in cache[it['link']]
        if fresh and looked >= 150:
            continue   # ponytail: 실행당 신규 조회 150건. 폭주만 막는다 — 평시 신규는 하루 11건 안팎
        looked += fresh
        rec = orig_pub(it['link'], cache)
        it['orig_link'] = rec.get('link')
        it['orig_published'] = rec.get('date')
        if rec.get('date'):
            try:
                gap = (today - date.fromisoformat(rec['date'])).days
                it['stale'] = gap if gap >= STALE_DAYS else None
            except ValueError:
                pass
        # 본문을 얻었으면 다시 채점한다. add()에서 매긴 점수는 제목만 본 것이다.
        if rec.get('body'):
            korean = it['board'] in ('B', 'P')
            sc, hits = score_news(it['title'], korean, body=rec['body'])
            sc = min(100, sc + ASSOC_BASE)
            hits = hits + ['협회게시판']
            penalty, flag, hit = news_flags(it['title'], it.get('media'), it['link'])
            if penalty:
                sc = max(0, sc - penalty)
                hits = hits + [hit]
            it['score'], it['score_hits'], it['flag'] = sc, hits, flag
    if len(cache) != before:
        with open(NEWS_ORIG_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
        print('  협회 원문조회: 캐시 %d건, 신규 %d건' % (before, len(cache) - before),
              file=sys.stderr)
    return items


def fetch_news(days=7):
    start = (today_kst() - timedelta(days=days)).isoformat()
    out, seen_url, seen_title = [], set(), set()

    def add(items, board, korean, source, min_score, pinned=False):
        for x in items:
            if not x.get('title') or not x.get('link'):
                continue
            if x.get('date') and x['date'] < start:
                continue
            nu, nt = norm_url(x['link']), norm_title(x['title'])
            if nu in seen_url or (nt and nt in seen_title):
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
            # 채점은 매체 꼬리를 뗀 뒤에 한다. 원본 제목으로 매기면 매체명이 점수에 들어간다 —
            # '… - 다나와 자동차'의 '자동차'가 6점으로 계산되던 실측 12건.
            # id·분류·점수가 모두 같은 문자열을 보게 맞춰 둔다.
            sc, hits = score_news(title, korean)
            if source == 'ITS Korea':
                sc = min(100, sc + ASSOC_BASE)
                hits = hits + ['협회게시판']
            # 하한은 감점 전 점수로 본다. 감점으로 탈락시키면 '제외'가 되어버린다 —
            # 재가공·기고문은 지우는 게 아니라 접어서 사람이 펼쳐 보게 하는 것이 목적이다.
            if sc < min_score:
                continue
            penalty, flag, hit = news_flags(title, media, x['link'])
            if penalty:
                sc = max(0, sc - penalty)
                hits = hits + [hit]
            seen_url.add(nu)
            seen_title.add(nt)
            # id는 매체 꼬리를 뗀 '표시용 제목'으로 만든다.
            # 원본 RSS 제목으로 만들면 구글뉴스가 날마다 다른 꼬리를 붙일 때
            # 같은 기사가 두 개의 id를 받는다(실측 50쌍, 22.5%).
            nt_disp = norm_title(title)
            out.append({
                'schema_version': SCHEMA_VERSION,
                # id를 링크 해시로 만들면 안 된다 — 구글뉴스 RSS 링크는 요청마다 바뀌는
                # 불투명 토큰이라 같은 기사가 매번 새 id를 받고, 매일 '신규'로 다시 뜬다.
                # 정규화한 제목이 훨씬 안정적이다.
                'id': 'news-%s' % hashlib.md5((nt_disp or nu).encode('utf-8')).hexdigest()[:12],
                # 옛 계산식(원본 제목 기준) id. 장부 이월용이며 채워 쓰고 버린다.
                'id_legacy': 'news-%s' % hashlib.md5((nt or nu).encode('utf-8')).hexdigest()[:12],
                'kind': 'news', 'board': board, 'source': source,
                'type': BOARD_TYPE[board],
                'country': None, 'country_ko': None,
                'title': title, 'media': media, 'org': media,
                # 카드 배지가 매체명 대신 이걸 쓴다. 매체명은 상세 모달에만 남는다.
                'category': classify(title, board),
                'flag': flag,          # '재가공 의심' / '기고·칼럼' — 화면에서 접는다
                # 게시판 맨 위에 고정된 공지글. 매일 목록 상단에 다시 나타나므로
                # 노출 상한을 이것들이 먼저 먹으면 그날 새 글이 통째로 밀린다
                # (실측: 8/21에 우리가 담은 5건이 전부 고정 공지였고 새 글은 0건).
                # 표시해 두고 상한 계산에서 뺀다.
                'pinned': pinned or None,
                'published': x.get('date') or today_kst().isoformat(),
                'deadline': None, 'budget': None, 'ref_no': None,
                'link': x['link'], 'cpv': [],
                # 협회 글은 게시일과 원문 게재일이 다르다. 아래에서 채운다(orig_pub).
                'orig_link': None, 'orig_published': None, 'stale': None,
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
    #
    # 3페이지까지 읽는다. 1페이지만 읽으면 못 따라간다 — 실측: 게시판은 하루 11건 안팎을
    # 올리는데 1페이지의 '일반' 칸은 10개뿐이고, 앞 10행은 매일 같은 공지 고정글이다.
    # 하루 한 번 읽는 구조에서 1페이지만 보면 도달 가능 21%, 3페이지면 84%다.
    #
    # 행 단위(<li><dl>)로 파싱한다. 앵커만 훑으면 같은 글이 두 번 잡히고(제목·썸네일 링크)
    # 공지 고정글을 구분할 수 없다.
    ROW = re.compile(r'<li( class="li_fixed")?>\s*<dl>(.*?)</dl>\s*</li>', re.S)
    DATE_LI = re.compile(r'<li>(20\d\d[-.]\d\d[-.]\d\d)</li>')
    for t, board, korean in [(8, 'B', True), (9, 'A', False)]:
        link_re = re.compile(r'boardDetail\.do\?type=%d&idx=(\d+)[^>]*>(.*?)</a>' % t, re.S)
        for page in (1, 2, 3):
            try:
                h = http_text('https://itskorea.kr/boardList.do?type=%d&currentPage=%d' % (t, page))
                i, j = h.find('listSet'), h.find('pagination')
                area = h[i:j] if 0 <= i < j else h      # 목록 영역만. 사이드바 위젯을 피한다
                for m in ROW.finditer(area):
                    pinned, body = bool(m.group(1)), m.group(2)
                    lm = link_re.search(body)
                    if not lm:
                        continue
                    title = re.sub(r'\s*새글\s*$', '', strip_tags(lm.group(2))).strip()
                    dm = DATE_LI.search(body)
                    add([{'title': title,
                          'link': 'https://itskorea.kr/boardDetail.do?type=%d&idx=%s' % (t, lm.group(1)),
                          'date': dm.group(1).replace('.', '-') if dm else None,
                          'media': 'ITS Korea'}],
                        board, korean, 'ITS Korea', 0,   # 협회가 이미 골라놓은 목록
                        pinned=pinned)
            except Exception as e:
                print('  itskorea type=%d p%d 실패: %s' % (t, page, e), file=sys.stderr)

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

    enrich_assoc(out)

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
    # 상한 면제 — 사람이 이미 선별한 목록이라 기계가 다시 자를 이유가 없다.
    # ITS Korea는 협회가 고른 목록이고(하루 11건 안팎), 부처 원문은 주 3~5건이다.
    # 대신 협회 글 중 카테고리가 '기타'인 것은 화면에서 접는다(index.html foldable).
    EXEMPT = ('국토교통부', '법제처', 'ITS Korea')
    capped, per = [], collections.Counter()
    for it in sorted(out, key=lambda x: (-x['score'], x['published'])):
        # 감점 표시된 기사는 상한 경쟁에서 빼둔다. 점수가 낮아 무조건 잘리는데,
        # 잘리면 '제외'가 된다 — 접어서 보여주기로 한 것들이다. (화면에도 같은 규칙)
        if it.get('flag'):
            capped.append(it)
            continue
        if it['source'] in EXEMPT:
            capped.append(it)
            continue
        k = (it['board'], it['published'])
        if per[k] >= NEWS_DAILY_CAP:
            continue
        per[k] += 1
        capped.append(it)

    return capped


# 수집기 목록. fetch_news가 정의된 뒤에 와야 한다.
SOURCES = [
    ('ted', fetch_ted),
    ('worldbank', fetch_worldbank),
    ('adb', fetch_adb),
    ('news', fetch_news),
    # SAM.gov 보류: 창 내 215건 중 ITS 핵심어에 걸리는 건이 0건이었다.
    # 미국 ITS 발주는 주(state) DOT 소관이라 연방 조달망에 거의 오지 않는다.
    # 함수는 남겨둔다 — 나중에 주 단위 포털을 붙일 때 참고용.
    # ('samgov', fetch_samgov),
]


if __name__ == '__main__':
    main()
