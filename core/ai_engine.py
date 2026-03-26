# ai_engine.py
import os, json, voyageai, re, asyncio, csv, io, time, hashlib
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging
from dotenv import load_dotenv, find_dotenv
from google import genai
from google.genai import types

# .env 로드 (파일 위치와 무관하게 상위 디렉터리까지 자동 탐색)
load_dotenv(find_dotenv())

logger = logging.getLogger("Throo_AI_Engine")

class ThrooHybridEngine:
    # 전표 유형 사전 분류용 키워드 정의
    _AR_KEYWORDS  = ['매출', '입금', '수금', '판매', '납품', '청구', '세금계산서 발행']
    _AP_KEYWORDS  = ['매입', '지급', '송금', '구매', '발주', '구입', '결제', '출금', '세금계산서 수취']
    # 거래처 상호 탐지: 법인격 키워드 또는 업종명 포함 패턴
    # ★ 수정: [가-힣]{2,5} → [A-Za-z가-힣]{1,8} 로 확장
    #   "LG전자", "SK텔레콤", "KT&G" 등 Latin+Korean 혼합 상호 인식 지원
    _COUNTERPARTY_RE = re.compile(
        r'(?:㈜|주식회사|\(주\)|유한회사|\(유\))[가-힣A-Za-z\s]{1,10}'
        r'|[가-힣A-Za-z]{2,8}(?:주식회사|㈜|\(주\)|유한회사|\(유\))'
        r'|[A-Za-z가-힣]{1,8}(?:전자|화학|건설|물산|통신|식품|유통|제약|은행|자동차|시스템|솔루션|물류|에너지|텔레콤|홀딩스|그룹|인더스트리)'
    )

    @staticmethod
    def is_empty_value(val) -> bool:
        """LLM이 반환하는 '가짜 빈 값'을 포함하여 실질적으로 비어있는지 판별.

        Python falsy(None, "", 0, []) 외에 LLM이 빈 의미로 자주 반환하는 값들을 모두 처리:
          - 공백 문자열:   " ", "  "  →  True
          - 하이픈/대시:   "-", "—"   →  True
          - 문자열 null:   "null", "None", "N/A", "n/a", "없음", "해당없음"  →  True

        사용 예)
            is_empty_value(None)       → True
            is_empty_value("")         → True
            is_empty_value(" ")        → True
            is_empty_value("-")        → True
            is_empty_value("null")     → True
            is_empty_value("2026-04-10") → False
            is_empty_value("S010")     → False
        """
        if val is None:
            return True
        if not isinstance(val, str):
            # 숫자 0은 빈값으로 취급하지 않음 (금액 0은 유효한 값)
            return False
        _FAKE_EMPTY = {"", "-", "—", "–", "null", "none", "n/a", "없음", "해당없음", "미입력"}
        return val.strip().lower() in _FAKE_EMPTY

    @staticmethod
    def calculate_accounting_amounts(result_json: dict, raw_text: str = "") -> None:
        """
        AI의 산수 오류 및 오탐지(예: '구매1팀'의 '1')를 차단하는 최종 가드레일.

        ─ 키워드 결정 우선순위 ─
        1. raw_text에 '별도','제외','공급가','단가' 포함 → mode = SUPPLY 강제
           추출 금액을 supply_base로 보고 부가세를 더해 total_amount 확정.
           예) "330,000원 부가세 별도" → sup=330,000 → total=363,000
        2. 위 키워드 없음 → mode = TOTAL 유지
           추출 금액을 total_amount로 보고 역산(÷1.1)으로 공급가·세액 확정.
           예) "330,000원 부가세 10%" → tot=330,000 → base=300,000 / vat=30,000

        ─ 금액 연산 ─
        float 부동소수점 오차를 방지하기 위해 Decimal + ROUND_HALF_UP을 사용한다.
        result_json에 저장할 최종값은 int로 변환하여 JSON 직렬화 호환성을 유지한다.
        """
        def _to_d(value) -> Decimal:
            """None·빈값을 안전하게 Decimal('0')으로 변환"""
            return Decimal(str(value)) if value else Decimal('0')

        def _round_won(value: Decimal) -> Decimal:
            """원(KRW) 단위 ROUND_HALF_UP 반올림 (소수점 없음)"""
            return value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

        ZERO = Decimal('0')

        # ══════════════════════════════════════════════════════════════════
        # [단위 보정] 한글 통화 단위 초정밀 인식 → total_amount 원(KRW) 절대값 변환
        # ─ 설계 원칙 ─
        # 1. KRW 전용: 외화(USD·JPY 등)이면 이 블록 전체를 건너뜀
        # 2. 콤마 제거 후 탐색 — "1,500만원" 처럼 콤마가 숫자를 끊는 오탐 방지
        # 3. 복합 단위 패턴(억+천만 등)을 단일 패턴보다 먼저 시도
        # 4. 단일 단위는 _UNIT_TABLE 순서(긴 단위·큰 단위 우선)로 첫 매칭만 채택
        # 5. _unit_aware_amount: 변환 결과를 가드레일 1/3에 전달하는 트래킹 변수
        # ══════════════════════════════════════════════════════════════════
        _unit_aware_amount: Decimal = ZERO  # 외화이면 ZERO 유지 → 가드레일 정상 동작

        # local_currency: result_json에 저장된 회사 기준 통화 (t_company 조회값).
        # 없으면 'KRW' 폴백 (구 데이터 호환).
        _local_cur_now  = (result_json.get("local_currency") or "KRW").strip().upper()
        _currency_now   = (result_json.get("currency")       or _local_cur_now).strip().upper()
        if _currency_now != _local_cur_now:
            # 외화 거래: 로컬 통화 단위 표현(만원/억 등)이 없으므로 변환하면 금액 왜곡
            logger.info(
                f"[단위보정 생략] currency={_currency_now} ≠ local={_local_cur_now} "
                f"→ 원문 숫자 그대로 사용"
            )
        else:
            _text_nc = raw_text.replace(",", "")  # 콤마 제거 — 단위 탐색 전용

            # ── 복합 단위 패턴 (큰+작은 단위 조합, 긴 패턴 우선) ────────────────
            _COMPOSITE_UNIT_PATTERNS = [
                # "1억 2천만원" → 120,000,000
                (r'(\d+(?:\.\d+)?)\s*억\s*(\d+(?:\.\d+)?)\s*천만원?', 100_000_000, 10_000_000),
                # "1억 5백만원" → 150,000,000
                (r'(\d+(?:\.\d+)?)\s*억\s*(\d+(?:\.\d+)?)\s*백만원?', 100_000_000,  1_000_000),
                # "1억 5만원"   → 100,050,000
                (r'(\d+(?:\.\d+)?)\s*억\s*(\d+(?:\.\d+)?)\s*만원?',   100_000_000,     10_000),
                # "1억 5천원"   → 100,005,000
                (r'(\d+(?:\.\d+)?)\s*억\s*(\d+(?:\.\d+)?)\s*천원?',   100_000_000,      1_000),
                # "3천만 5만원" → 30,050,000
                (r'(\d+(?:\.\d+)?)\s*천만\s*(\d+(?:\.\d+)?)\s*만원?',  10_000_000,     10_000),
            ]

            # ── 단일 단위 테이블 (긴 단위·큰 단위 우선 — 순서가 매칭 우선순위) ──
            _UNIT_TABLE = [
                ('천억원', Decimal('100000000000')),
                ('천억',   Decimal('100000000000')),
                ('억원',   Decimal('100000000')),
                ('억',     Decimal('100000000')),
                ('천만원', Decimal('10000000')),
                ('천만',   Decimal('10000000')),
                ('백만원', Decimal('1000000')),
                ('백만',   Decimal('1000000')),
                ('만원',   Decimal('10000')),
                ('만',     Decimal('10000')),
                ('천원',   Decimal('1000')),
                ('천',     Decimal('1000')),
            ]

            _unit_converted = False

            # 1단계: 복합 패턴 시도 (큰 단위+작은 단위 조합)
            for _pat, _mul_a, _mul_b in _COMPOSITE_UNIT_PATTERNS:
                _um = re.search(_pat, _text_nc)
                if _um:
                    _conv = Decimal(_um.group(1)) * _mul_a + Decimal(_um.group(2)) * _mul_b
                    logger.info(
                        f"[단위보정] 복합단위 '{_um.group(0)}' "
                        f"({int(_mul_a):,}+{int(_mul_b):,}) → {int(_conv):,}원"
                    )
                    result_json["total_amount"] = int(_conv)
                    _unit_aware_amount = _conv
                    _unit_converted = True
                    break

            # 2단계: 단일 단위 시도 (큰 단위 우선, 첫 매칭에서 확정)
            if not _unit_converted:
                for _unit_suffix, _multiplier in _UNIT_TABLE:
                    _um = re.search(
                        r'(\d+(?:\.\d+)?)\s*' + re.escape(_unit_suffix), _text_nc
                    )
                    if _um:
                        _conv    = Decimal(_um.group(1)) * _multiplier
                        _cur_tot = Decimal(str(result_json.get("total_amount") or 0))

                        # ★ _unit_aware_amount는 _need_update 와 무관하게 항상 설정한다.
                        # 이유: AI가 이미 올바른 절대값(예: 3,300,000)을 total_amount에 넣었더라도
                        #       원문에는 "330만원" 이라고 표기되므로, 뒤쪽 가드레일 1에서
                        #       source_amount가 "330" 으로 추출된다.
                        #       _unit_aware_amount가 없으면 가드레일 3이 3,300,000 ≠ 330 으로
                        #       판단해 AI 정답을 강제 환원하는 오작동이 발생한다.
                        _unit_aware_amount = _conv

                        # total_amount 갱신: 억 이상은 무조건, 만·천은 5배 이상 차이일 때만
                        _need_update = (
                            _multiplier >= Decimal('100000000')
                            or _cur_tot <= ZERO
                            or (_conv / (_cur_tot if _cur_tot > ZERO else Decimal('1'))) >= Decimal('5')
                        )
                        if _need_update:
                            logger.info(
                                f"[단위보정] 단일단위 '{_um.group(0)}' "
                                f"(×{int(_multiplier):,}) → {int(_conv):,}원 (total_amount 갱신)"
                            )
                            result_json["total_amount"] = int(_conv)
                        else:
                            logger.info(
                                f"[단위보정] 단일단위 '{_um.group(0)}' 감지 — "
                                f"AI 결과({int(_cur_tot):,}원) 신뢰, total_amount 유지 / "
                                f"_unit_aware_amount={int(_conv):,} 설정 완료"
                            )
                        break  # 첫 매칭 단위에서 확정 (더 작은 단위로 내려가지 않음)

        # ══════════════════════════════════════════════════════════════════
        # [가드레일 0] 부가세 관련성 검사 — 가장 먼저 실행
        # 문장에 부가세 언급이 없으면 10% 안분 로직을 완전히 건너뛰고,
        # total_amount를 그대로 공급가로 확정한다.
        # "+" / "-" 포함: "300,000 + 부가세" 또는 "330,000 - 세액" 등 표현도 감지
        # ══════════════════════════════════════════════════════════════════
        _VAT_KEYWORDS = ["포함", "별도", "부가세", "VAT", "vat", "세금", "세액", "+", "-"]
        _is_vat_relevant = any(kw in raw_text for kw in _VAT_KEYWORDS)

        if not _is_vat_relevant:
            # ── 1. 기준 금액 결정 ────────────────────────────────────────────
            _tot0 = _to_d(result_json.get("total_amount"))
            # AI가 금액을 추출하지 못했을 때 원문에서 직접 탐지
            if _tot0 <= ZERO:
                _text0 = raw_text.replace(",", "")
                _pm0   = re.search(r'(\d[\d,]*)\s*원', raw_text)
                _lnums = [Decimal(n) for n in re.findall(r'\d+', _text0) if len(n) >= 3]
                if _pm0:
                    _tot0 = Decimal(_pm0.group(1).replace(",", ""))
                elif _lnums:
                    _tot0 = max(_lnums)

            # ── 2. AI 생성 오염 라인 청소 ────────────────────────────────────
            # 부가세 언급 없음 → TAX 타입 라인은 근거 없는 Hallucination이므로 완전 삭제.
            # 나머지 라인(EXP, AP 등)의 개별 amount를 0으로 초기화하여
            # 이후 안분 로직(가드레일 우선권)에서 _tot0 기준으로 재계산되도록 유도.
            _orig_lines     = result_json.get("lines", [])
            _filtered_lines = [l for l in _orig_lines if l.get("type", "").upper() != "TAX"]
            _removed_cnt    = len(_orig_lines) - len(_filtered_lines)
            for l in _filtered_lines:
                l["amount"] = 0

            # ── 3. 금액 확정 (일괄 업데이트) ─────────────────────────────────
            result_json["lines"] = _filtered_lines
            result_json.update({
                "total_amount": int(_tot0),
                "supply_base":  int(_tot0),  # 합계 = 공급가 (부가세 없음)
                "supply_vat":   0,
                "vat_rate":     0,
            })
            logger.info(
                f"[가드레일 0] 부가세 미언급 → TAX 라인 {_removed_cnt}개 제거, "
                f"{int(_tot0):,}원 전액 비용화 완료 "
                f"(supply_base={int(_tot0):,}, vat=0)"
            )
            return  # 이하 10% 안분 로직을 실행하지 않음

        mode     = result_json.get("amt_mode", "TOTAL")
        tot      = _to_d(result_json.get("total_amount"))
        sup      = _to_d(result_json.get("supply_base"))
        # ★ vat_rate 0% 버그 방지: `or 10` 패턴은 0을 falsy로 처리해 10%로 덮어씀.
        # None(미추출) → 기본값 10% / 0(명시적 0%) → 0% 그대로 유지
        _vat_val = result_json.get("vat_rate")
        vat_rate = (Decimal(str(_vat_val)) if _vat_val is not None else Decimal('10')) / Decimal('100')
        text_clean = raw_text.replace(",", "")

        # [가드레일 1] 원문에서 '진짜' 기준 금액 탐지
        # '원' 앞 숫자를 최우선, 없으면 3자리 이상 숫자 중 최댓값 사용
        # → '구매1팀', '2026년' 등 문맥상 금액이 아닌 짧은 숫자를 자동 배제
        # ★ 단위 변환이 발생한 경우(_unit_aware_amount > 0): 원문의 단위 표현("330만원" 등)은
        #   `\d+\s*원` 패턴에 매칭되지 않아 source_amount가 소수로 추출될 수 있다.
        #   이 경우 _unit_aware_amount를 source_amount로 채택하여 가드레일 3 오발화를 방지한다.
        price_match   = re.search(r'(\d[\d,]*)\s*원', raw_text)
        all_long_nums = [Decimal(n) for n in re.findall(r'\d+', text_clean) if len(n) >= 3]

        source_amount = ZERO
        if price_match:
            source_amount = Decimal(price_match.group(1).replace(",", ""))
        elif all_long_nums:
            source_amount = max(all_long_nums)

        # 단위 변환이 발생했다면 변환된 금액을 정권 source_amount로 교체
        # (예: "330만원" → source_amount=330 → 교체 후 3,300,000)
        if _unit_aware_amount > ZERO:
            logger.info(
                f"[가드레일 1] 단위 변환 감지 → source_amount {source_amount:,.0f} "
                f"→ {int(_unit_aware_amount):,} 으로 교체"
            )
            source_amount = _unit_aware_amount

        # [가드레일 2] 명시적 제외/별도/공급가 키워드 → mode 강제 결정
        # AI의 amt_mode 판단보다 원문 키워드를 절대 우선한다.
        exclusive_keywords   = ["별도", "제외", "공급가", "단가"]
        is_explicit_exclusive = any(kw in raw_text for kw in exclusive_keywords)

        if is_explicit_exclusive:
            # 키워드 있음 → 원문 금액은 공급가(SUPPLY)
            mode = "SUPPLY"
            # source_amount를 supply_base 기준값으로 확정
            # (AI가 tot에 넣었어도, 이 경우 공급가가 맞음)
            sup = source_amount if source_amount > ZERO else (sup if sup > ZERO else tot)
            logger.info(f"[가드레일] 별도/제외 키워드 감지 → SUPPLY 모드 강제 (sup={sup:,.0f})")
        else:
            # 키워드 없음 → 원문 금액은 합계(TOTAL)
            mode = "TOTAL"
            # [가드레일 3] AI 산수(Hallucination) 감지 및 강제 보정
            # ─ 개선된 판단 기준 ─
            # ① source_amount가 유효해야 함 (비교 대상 존재)
            # ② tot와 source_amount가 실질적으로 다를 때만 차단
            #    (단위 변환 포함 후에도 일치하지 않는 경우 = 진짜 Hallucination)
            # ③ 오차 허용 범위: 반올림 등으로 1원 이하 차이는 동일로 간주
            # ④ 단위 변환된 source_amount와 tot가 일치하면 차단하지 않음
            #    (예: "330만원" → source=3,300,000 / tot=3,300,000 → 통과)
            _amounts_match = source_amount > ZERO and abs(tot - source_amount) <= Decimal('1')
            if tot > ZERO and source_amount > ZERO and not _amounts_match:
                original_tot = tot
                tot = source_amount
                for line in result_json.get("lines", []):
                    line["amount"] = 0
                logger.info(
                    f"[가드레일 3] AI 산수 차단: tot={int(original_tot):,} ≠ source={int(source_amount):,} "
                    f"→ {int(tot):,}으로 강제 환원 및 라인 재계산 실행"
                )

        # [최종 금액 확정 및 안분]
        # 모든 연산은 Decimal로 수행하고, result_json 저장 시 int 변환하여 직렬화 호환성 확보
        if mode == "SUPPLY" and sup > ZERO:
            # 공급가 기준: total = supply_base + ROUND_HALF_UP(supply_base × vat_rate)
            calculated_vat = _round_won(sup * vat_rate)
            total_amount   = sup + calculated_vat
            result_json["total_amount"] = int(total_amount)
            result_json["supply_base"]  = int(sup)
            result_json["supply_vat"]   = int(calculated_vat)
            logger.info(
                f"[금액확정] SUPPLY: sup={sup:,.0f} + vat={calculated_vat:,.0f} "
                f"→ total={total_amount:,.0f}"
            )
        else:
            # 합계 기준: supply_base = ROUND_HALF_UP(total ÷ (1 + vat_rate)), vat = total - base
            if vat_rate > ZERO:
                base = _round_won(tot / (Decimal('1') + vat_rate))
                vat  = tot - base
            else:
                # vat_rate = 0(영세율·면세): supply_base = total_amount, vat = 0
                base = tot
                vat  = ZERO
            result_json["total_amount"] = int(tot)
            result_json["supply_base"]  = int(base)
            result_json["supply_vat"]   = int(vat)
            logger.info(
                f"[금액확정] TOTAL: tot={tot:,.0f} → base={base:,.0f} / vat={vat:,.0f}"
            )

        # ── vat_rate = 0 후처리: TAX 라인 강제 제거 ────────────────────────────
        # 영세율(0%)·면세 거래에는 부가세 라인 자체가 존재해서는 안 된다.
        # 벡터 DB 패턴에서 TAX 라인을 가져왔더라도 완전 제거하여 차대 합계 불일치 방지.
        if vat_rate == ZERO:
            _lines_before = result_json.get("lines", [])
            _lines_after  = [l for l in _lines_before if l.get("type", "").upper() != "TAX"]
            _removed      = len(_lines_before) - len(_lines_after)
            if _removed:
                result_json["lines"] = _lines_after
                logger.info(
                    f"[vat_rate=0] TAX 라인 {_removed}개 제거 완료 "
                    f"(영세율/면세 전표 — 부가세 라인 불필요)"
                )

    @staticmethod
    def _match_status(score) -> str:
        """pg_trgm similarity 점수 → 매칭 상태 변환
        EXACT : score >= 0.85  → 자동 확정, 필드 정상 표시
        FUZZY : score >= 0.30  → 자동 입력되나 UI 황색 하이라이트로 사용자 확인 요청
        NONE  : score <  0.30  → 빈 값 처리, UI 빨간 테두리로 직접 검색 유도
        임계치 근거: pgAdmin 실측 기반 ('구매1팀이'↔'구매1팀'=0.57, 전처리 후 점수 향상)
        """
        s = float(score) if score is not None else 0.0
        if s >= 0.85:   return "EXACT"
        elif s >= 0.30: return "FUZZY"
        else:           return "NONE"

    @staticmethod
    def _clean_search_term(term: str) -> str:
        """검색어 전처리: 후행 조사·일반명사 제거로 similarity 점수 향상
        AI가 추출한 원문 그대로인 경우에도 핵심 고유명사만 남겨 정확도를 높인다.
        예: '긴다리의자관련' → '긴다리의자', '구매1팀이' → '구매1팀'
        공백 포함 시 첫 토큰(핵심어)만 사용: '긴다리의자관련 소모품' → '긴다리의자'
        """
        if not term or not term.strip():
            return term

        # 공백 분리 후 첫 토큰만 사용 (후속 일반명사 제거)
        token = term.strip().split()[0]

        # 후행 패턴 제거 목록 (긴 패턴 우선 — 짧은 단일 조사보다 먼저 매칭)
        _STRIP_TAIL = [
            "관련", "관한", "소모품", "품목", "부품", "재료", "용품", "비용",
            "이에서", "에서", "에게", "으로서", "으로써", "이라면", "이라도",
            "부터", "까지", "처럼", "같이", "만큼",
            "이라", "이나", "이란", "이랑", "이고", "이며", "이든",
            "이서", "이가", "으로", "에도", "에만", "에는", "로서", "로써",
            "를", "을", "은", "는", "이", "가", "도", "와", "과", "의",
            "로", "에", "고", "서", "나", "지",
        ]
        for suffix in _STRIP_TAIL:
            if token.endswith(suffix) and len(token) > len(suffix) + 1:
                token = token[: -len(suffix)]
                break  # 한 번만 제거 (연쇄 방지)

        return token if len(token) >= 2 else term.strip()

    @staticmethod
    def _map_common_terms(term: str) -> str:
        """사용자 입력 용어 → 표준 계정명(glname1) 매핑 (하드코딩된 동의어 사전)
        AI가 '식대'와 '복리후생비'를 연결하지 못할 경우를 대비해,
        자주 쓰이는 용어를 표준 계정명으로 변환하여 Intent GL Override 로직에 제공한다.
        """
        # (키워드, 표준계정명) 튜플 리스트 — 우선순위 고려
        mapping = [
            ("식대", "복리후생비"), ("점심", "복리후생비"), ("저녁", "복리후생비"), ("회식", "복리후생비"), 
            ("야근", "복리후생비"), ("간식", "복리후생비"), ("음료", "복리후생비"), ("커피", "복리후생비"),
            ("택시", "여비교통비"), ("버스", "여비교통비"), ("기차", "여비교통비"), ("KTX", "여비교통비"), 
            ("SRT", "여비교통비"), ("출장", "여비교통비"), ("주유", "차량유지비"), ("통행", "차량유지비"),
            ("발송", "운반비"), ("택배", "운반비"), ("퀵", "운반비"), ("화물", "운반비"),
            ("접대", "접대비"), ("선물", "접대비"),
            ("복사", "도서인쇄비"), ("명함", "도서인쇄비"), ("인쇄", "도서인쇄비"),
            ("전기", "전력비"), ("수도", "수도광열비"), ("가스", "수도광열비"), ("난방", "수도광열비"),
            ("임차", "지급임차료"), ("월세", "지급임차료"),
            ("수수료", "지급수수료"), ("자문", "지급수수료"), ("기장", "지급수수료"),
            ("수리", "수선비"), ("보수", "수선비"), ("고장", "수선비"),
            ("소모품", "소모품비"), ("비품", "소모품비"), ("문구", "소모품비"),
            ("교육", "교육훈련비"), ("강의", "교육훈련비"), ("연수", "교육훈련비"),
            ("대출이자", "이자비용"), ("차입금이자", "이자비용"), ("이자지급", "이자비용"),
            ("이자납부", "이자비용"), ("예금이자", "이자수익"), ("이자입금", "이자수익"),
        ]
        
        for keyword, account_name in mapping:
            if keyword in term:
                return account_name
        return term  # 매핑 없으면 원래 용어 반환

    # ── GL 대분류 콤팩트 요약 (system_instruction 경량화용) ──────────────
    # 전체 137개 계정 대신 대분류별 대표 코드만 사용 → 토큰 절감, 속도 향상
    # 구체적인 계정은 pattern_guide(RAG 결과)를 통해 동적으로 주입됨
    _GL_CATEGORY_SUMMARY = """[GL 계정 대분류 참조 — 코드|계정명]
■ 자산    : 100000|현금  110000|외상매출금  111000|미수금  120000|선급금  135100|비품
■ 부채    : 210000|외상매입금  211000|미지급금  213000|매입부가세  214000|매출부가세  215000|선수금
■ 자본    : 300000|이월이익잉여금
■ 매출    : 400000|제품매출  410000|상품매출  420000|기타매출  720000|잡수익
■ 매출원가: 440000|제품매출원가  441000|상품매출원가  450000|재료비-제품
■ 인건비  : 500000|임원급여  500200|직원급여  501000|퇴직급여
■ 복리후생: 510100|복리후생-야근식대  510110|복리후생-행사  510160|복리후생-교육  510240|복리후생-간식대지원
■ 여비교통: 510300|여비교통-출장비  510310|여비교통-시내교통
■ 통신비  : 510400|통신비-전화팩스  510410|통신비-우편발송  510420|통신비-전산회선료
■ 수도광열: 520000|수도광열-수도전기  521000|수도광열-냉난방비
■ 기타비용: 522060|세금과공과  523000|소모품비  524000|업무추진비(접대비)  533000|운반비  542000|지급수수료  542200|임차료  560000|보험료  579000|잡비
■ 세금과공과(522060) 추가: 이제 과태료나 인지대 등을 정확히 분류합니다.
■ 복리후생-경조금(510200) 추가: 화환이나 축의금 지출  
■ 지급수수료-기타(542400) 추가: 일반적인 수수료 지출
■ 광고선전비(545000) 추가: 홍보 관련 지출 대응
■ 영업외  : 752000|이자비용  710000|이자수익  753000|잡손실"""

    def __init__(self):
        self.vo     = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))
        self.gemini = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

        # ── Gemini system_instruction (경량화: 대분류 요약만 주입) ───────────
        # 전체 GL Master 대신 _GL_CATEGORY_SUMMARY(대분류 요약)만 사용해 토큰 절감.
        # 구체적인 계정 코드는 RAG 패턴 매칭 결과(pattern_guide)로 동적 주입됨.
        self.system_instruction = f"""너는 전문 회계 AI 비서다. 아래 GL 대분류 참조표와 사용자 프롬프트의 [참조 패턴]을 반드시 활용하여 분개를 생성하라.

[회계 판단 규칙]
- 우체국 등기·우표·전화세·우편 발송 등 서류·정보 전달 비용 → 통신비-우편발송(510410)
- 퀵서비스·택배·화물 운송 등 물건 물리적 이동 비용 → 운반비(533000)
- 식대·야근식대·간식비 → 복리후생-야근식대(510100)
- 과태료·벌과금·허가수수료·인지세·주민세·재산세·면허세 등 국가·지자체에 납부하는 세금 및 공과금
  → 반드시 세금과공과(522060)를 우선 사용하라. 잡비(579000)로 처리하지 마라.
- 대출이자·차입금이자 등 이자를 '지급'하거나 '납부'하는 경우 → 이자비용(752000)
- 예금이자·적금이자 등 이자가 '입금'되거나 '들어온' 경우 → 이자수익(710000)
- 차변과 대변의 합계는 반드시 일치해야 하며, 모든 금액은 숫자 형태로 출력한다.
- 알 수 없는 계정코드는 절대 임의로 만들지 말고 glmaster를 ""(빈 문자열)로 출력하라.

[날짜 추출 규칙 — 중요]
- 자연어에 "입금일", "만기일", "지급 예정일" 등이 명시된 경우:
  해당 라인의 `due_date` 필드에 YYYY-MM-DD 형식으로 반드시 출력하라. 생략하지 마라.
- 자연어에 "세금계산서 발행일", "계산서 발행일", "세금계산서 일자", "세금계산서 발행" 등의 날짜가 명시된 경우:
  최상단 루트 JSON의 `tax_invoice_date` 필드에 YYYY-MM-DD 형식으로 반드시 출력하라. 절대 생략하지 마라.
  (예: "26.3.31일" → "2026-03-31", "26.04.10" → "2026-04-10")
- tax_invoice_date는 '발행', '계산서', '수취', '수신', '발급', '영수증', '전자' 등
  증빙 발행과 관련된 명시적 키워드가 날짜와 함께 언급된 경우에만 추출하라.
  단순히 "26.3.18일 삼성전자에..." 처럼 거래 발생일·시점을 나타내는 날짜는
  절대로 tax_invoice_date에 넣지 말고 반드시 빈 문자열("")로 반환하라.
- '부가세 포함', '부가세 별도', 'VAT', '세금', '세액' 등 금액 계산용 키워드는
  증빙 발행과 무관하다. 이 단어들만 있고 증빙 키워드가 없다면
  tax_invoice_date를 절대 채우지 말고 반드시 빈 문자열("")로 반환하라.
- 연도가 두 자리(예: "26.3.31", "26.04.10")인 경우 2000년대로 처리한다.
  예: "26.3.31" → "2026-03-31",  "26.4.10" → "2026-04-10"

[세금코드(taxcd) 추출 규칙]
- 세금코드(taxcd)는 AI가 임의로 추론하지 않는다. 반드시 빈 문자열("")로 반환하라. 사용자가 팝업에서 직접 선택하도록 유도해야 한다.
- taxcd 필드는 반드시 루트 JSON에 포함시켜라. 생략 금지.

[사용자 명시 계정 최우선 원칙 — 절대 준수]
- 사용자 입력 문장에 명확한 회계 계정 명칭(예: 비품, 소모품비, 복리후생비, 임차료 등)이 포함되어 있다면:
  당신의 사전 지식·통계적 추론·물품명 연상과 무관하게, 사용자가 명시한 단어를 100% 우선하여
  해당 계정을 차/대변의 메인 계정으로 결정하라.
- 예: "긴다리의자 비품 구매" → '의자'라는 물품명에 집착하여 소모품으로 추론하지 말고,
  사용자가 명시한 '비품'을 계정으로 사용할 것.
- [시스템 힌트] 섹션이 프롬프트에 포함된 경우, 그 지시를 다른 모든 규칙보다 최우선으로 따를 것.

<AMOUNT_EXTRACTION_RULES>
1. 문장에 나타난 숫자 그대로를 total_amount로 추출하라. (예: "10,000원치" → 10000)
2. 당신이 임의로 10%를 더해 11,000으로 만드는 행위는 '데이터 조작'으로 간주된다.
3. 명시적인 '공급가' 혹은 '부가세 별도' 단어가 없을 때는 절대로 amt_mode를 "SUPPLY"로 설정하지 마라.
4. 문장에 '포함', '별도', '부가세', 'VAT', '세금', '세액' 등 부가세 관련 표현이 전혀 없다면 vat_rate를 반드시 0으로 출력하라.
5. 위 조건(규칙 4)에 해당하는 경우 supply_base와 total_amount를 동일한 숫자로 채워라. 임의로 역산(÷1.1)하지 마라.
</AMOUNT_EXTRACTION_RULES>

[역분개(Reversal) 판단 규칙]
- "매출감소", "매출취소", "매출반품", "매출에누리", "외상매출금 감소" 등의 표현은 역분개(마이너스 매출)임.
- 역분개 시에도 일단 정방향(매출발생) 분개 구조로 출력하라. 차대변 Swap은 백엔드 파이썬이 수행한다.
- 역분개 문맥에서도 doctyp은 'CI'(매출 관련)를 그대로 사용하라.

[결제 수단 판별 규칙 — 중요]
- "법카 한도 초과돼서 현금으로 지급", "법카 안 돼서 개인카드로 결제" 등의 표현은
  법인카드가 언급되었더라도 실제 결제는 현금/개인카드임. 이 경우 미지급금(AP) 또는 현금(EXP) 분개로 처리하라.
- 실제 법인카드 결제가 확정된 경우만 법인카드 전표로 생성하라.
- 사원 개인카드 사용의 경우: 해당 사원을 거래처로 하여 미지급금(AP) 처리.
- "내 현금으로 결제", "사비로 냈다", "내가 먼저 냈다" 등 사원 개인 자금으로 대납한 경우:
  대변은 현금(100000)이 아니라 미지급금(211000, AP, gltype='S')으로 처리하고 doctyp을 SI로 출력하라.
  해당 사원명이 있으면 거래처(bizptcd)에 사원 코드를 매핑하라.

{self._GL_CATEGORY_SUMMARY}""".strip()
        logger.info("system_instruction 초기화 완료 (경량 GL 대분류 요약 사용)")

        # ── 결과 캐시 (동일 입력 반복 시 API 재호출 없이 즉시 반환) ──────────
        # 키: md5(comcd + raw_text),  값: result_json 전체
        # 최대 _CACHE_MAX 건 FIFO 방식으로 유지
        self._result_cache: dict = {}
        self._cache_order: list  = []
        self._CACHE_MAX: int     = 50

        # ── 프롬프트 템플릿 로딩 ──────────────────────────────────────────────
        try:
            prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "journal_generation.txt")
            with open(prompt_path, "r", encoding="utf-8") as f:
                self.system_prompt_template = f.read()
        except Exception as e:
            logger.error(f"Failed to load prompt template: {e}")
            self.system_prompt_template = ""

    # 거래처 미탐지여도 단독으로 전표 유형을 확정할 수 있는 강한 동사 키워드
    # "납품하여", "매출하여" 등 행위 완료형 → CI / "구매하여", "결제하여" 등 → SI
    _STRONG_AR_KW = ['매출하', '판매하', '납품하', '청구하', '수금하', '입금받', '세금계산서 발행']
    _STRONG_AP_KW = ['매입하', '구매하', '발주하', '지급하', '결제하', '세금계산서 수취', '세금계산서 수신']

    # ── 현금 수령 · 소액 현금 지출 패턴 분기용 키워드 ─────────────────────────
    # _is_cash_receipt_only / _is_small_cash_expense 헬퍼에서 사용
    _AR_RECEIPT_KW  = ['입금', '수금', '수납', '들어왔', '받았']   # 현금/통장 수령 동사
    _CASH_KW        = ['현금', '통장', '계좌', '은행']              # 현금/은행 명사
    _SMALL_CASH_KW  = ['퀵', '퀵비', '택배', '택배비', '등기', '우편', '우표']  # 소액 운반 비용

    # ── 선수금(계약금) 처리 감지 키워드 ─────────────────────────────────────
    # CI(매출) 전표에서 이 키워드가 감지되면 대변 계정을 215000(선수금)으로 고정하고
    # mulky='A' 를 세팅한다. 세금계산서 발행 대상 아님(SX 코드 힌트).
    _ADVANCE_PAYMENT_KW = [
        '계약금', '선입금', '선수금', '미리 입금', '미리입금',
        '선불', '계약 입금', '선금', '착수금', '보증금 입금',
    ]

    # ── 선급금(계약금 지급) 처리 감지 키워드 ──────────────────────────────────
    # SI(매입) 전표에서 이 키워드 + AP방향 키워드가 감지되면 차변 계정을 120000(선급금)으로 고정하고
    # mulky='A' 를 세팅한다. 세금계산서 수취 대상 아님(PX00 코드 힌트).
    _ADVANCE_PURCHASE_KW = [
        '선급금', '계약금', '착수금', '선불', '선금',
        '선급 지급', '계약금 지급', '착수금 지급', '선불 지급',
        '선지급', '계약금 송금', '착수금 송금', '선급금 송금',
        '미리 지급', '미리지급',
    ]

    # 즉시 지불 성격 비용: 이자·수수료·공과금 + 지급 조합 → SI가 아닌 GL 우선
    # 예: "이자 지급", "수수료 납부", "공과금 결제" — 외상(AP 오픈아이템) 거래가 아님
    _GL_OVERRIDE_KW  = ['이자', '수수료', '공과금', '세금과공과', '과태료', '벌과금',
                        '인지세', '주민세', '재산세', '면허세']
    _INSTANT_PAY_KW  = ['지급', '납부', '결제', '송금', '이체', '출금']

    def _detect_doctype_hint(self, raw_text: str) -> str:
        """
        Python 규칙 기반 전표 유형 1차 추론 (AI 호출 전).

        판정 우선순위:
        0. 즉시 지불 비용(이자·수수료·공과금 + 지급) → GL 강제 (SI 오판 방지)
        1. 거래처 상호 탐지 + AR/AP 방향 키워드 조합 → CI / SI
        2. 거래처 미탐지여도 강한 행위 키워드(_STRONG_AR/AP_KW)만으로 판정 → CI / SI
           예: "LG전자에 납품하여 매출" — LG전자가 정규식에서 인식 안 될 때도 CI 보장
        3. 위 모두 해당 없음 → GL (일반전표)
        """
        # ── 0순위: 즉시 지불 비용 키워드 조합 → GL 우선 ────────────────────────
        # "이자 지급", "수수료 납부" 등은 외상 매입(AP) 거래가 아니므로 GL 유지
        _has_gl_override = any(kw in raw_text for kw in self._GL_OVERRIDE_KW)
        _has_instant_pay = any(kw in raw_text for kw in self._INSTANT_PAY_KW)
        if _has_gl_override and _has_instant_pay:
            logger.info(
                f"[Doctype GL 우선] 즉시지불 비용 감지 ('{raw_text[:50]}') → GL 반환"
            )
            return 'GL'

        has_counterparty = bool(self._COUNTERPARTY_RE.search(raw_text))
        has_ar = any(kw in raw_text for kw in self._AR_KEYWORDS)
        has_ap = any(kw in raw_text for kw in self._AP_KEYWORDS)

        # 1순위: 거래처 + 방향 키워드 조합
        if has_counterparty:
            if has_ar and not has_ap:
                logger.debug(f"Doctype CI: 거래처+AR 키워드 매칭")
                return 'CI'
            if has_ap and not has_ar:
                logger.debug(f"Doctype SI: 거래처+AP 키워드 매칭")
                return 'SI'

        # 2순위: 강한 행위 키워드 단독 판정 (거래처 정규식 미탐지 보완)
        has_strong_ar = any(kw in raw_text for kw in self._STRONG_AR_KW)
        has_strong_ap = any(kw in raw_text for kw in self._STRONG_AP_KW)

        if has_strong_ar and not has_strong_ap:
            logger.info(f"Doctype CI (강한 AR 키워드 단독 판정): '{raw_text[:40]}'")
            return 'CI'
        if has_strong_ap and not has_strong_ar:
            logger.info(f"Doctype SI (강한 AP 키워드 단독 판정): '{raw_text[:40]}'")
            return 'SI'

        # 3순위: 거래처는 탐지됐으나 AR/AP 방향이 모두 있거나 없는 경우
        # → AR 키워드가 더 명확하면 CI, AP 키워드가 더 명확하면 SI
        if has_counterparty and has_ar and has_ap:
            logger.info(f"Doctype: AR+AP 동시 탐지, AR 우선 CI 반환")
            return 'CI'  # AR 우선

        return 'GL'

    def _is_cash_receipt_only(self, raw_text: str) -> bool:
        """거래처 미명시 현금/통장 입금 확인 패턴 → AR 수금 폴백 트리거.
        '입금/수금' 동사 AND '현금/통장/계좌' 명사가 동시에 존재해야 True.
        예: '통장에 입금 확인' → True / 'LG전자 매출 입금' → False (거래처 있음)
        """
        return (any(kw in raw_text for kw in self._AR_RECEIPT_KW)
                and any(kw in raw_text for kw in self._CASH_KW))

    def _is_small_cash_expense(self, raw_text: str) -> bool:
        """소액 현금 지출 패턴 감지 (퀵비/택배비 + 현금 지급).
        Vector DB 우선 적용 원칙에 따라, Vector DB 미매칭 시에만 직접 분개 생성.
        예: '퀵비 현금으로 결제' → True / '택배비 카드 결제' → False (현금 없음)
        """
        return (any(kw in raw_text for kw in self._SMALL_CASH_KW)
                and any(kw in raw_text for kw in self._CASH_KW))

    async def get_embedding(self, text_input: str, input_type: str = "query") -> list:
        """
        Voyage AI voyage-3 임베딩 생성.
        - 벡터 DB 검색(쿼리)  → input_type="query"   (기본값)
        - 패턴 저장(문서 삽입) → input_type="document"
        - asyncio.to_thread로 감싸 이벤트 루프 블로킹 방지
        """
        result = await asyncio.to_thread(
            self.vo.embed, [text_input], model="voyage-3", input_type=input_type
        )
        return result.embeddings[0]

    # 구버전 호환 alias — mainai.py 등 외부 호출부가 아직 구 이름을 쓸 경우를 위해 유지
    async def generate_final_journal(self, db: Session, comcd: str, raw_text: str):
        return await self.analyze_and_generate_journal(db, comcd, raw_text)

    async def analyze_and_generate_journal(self, db: Session, comcd: str, raw_text: str):
        _t_start = time.time()

        # ── STEP1: 캐시 히트 체크 (동일 입력 → 즉시 반환) ───────────────────
        _cache_key = hashlib.md5(f"{comcd}::{raw_text.strip()}".encode()).hexdigest()
        if _cache_key in self._result_cache:
            logger.info(f"[CACHE HIT] '{raw_text[:30]}...' → {time.time()-_t_start:.3f}s")
            return self._result_cache[_cache_key]

        # ── 회사 기준 통화(Local Currency) 조회 ────────────────────────────────
        # 글로벌 법인(USD 본사, EUR 유럽법인 등) 지원을 위해 'KRW' 하드코딩을 제거하고
        # t_company.curren 값을 기준 통화로 사용한다.
        # 조회 실패 시 안전 폴백으로 'KRW' 사용.
        try:
            _lc_row = db.execute(
                text("SELECT curren FROM t_company WHERE comcd = :c LIMIT 1"),
                {"c": comcd}
            ).fetchone()
            local_currency = (_lc_row[0] or "KRW").strip().upper() if _lc_row else "KRW"
        except Exception as _lc_e:
            local_currency = "KRW"
            logger.warning(f"[로컬통화 조회 실패] {_lc_e} → 폴백 KRW 사용")
        logger.info(f"[로컬통화] comcd={comcd} → local_currency={local_currency}")

        # 0. Python 규칙 기반 전표 유형 사전 추론
        doctype_hint = self._detect_doctype_hint(raw_text)
        logger.info(f"Doctype hint: {doctype_hint} for: '{raw_text[:40]}'")

        # ── 특수 패턴 플래그 (AR수금폴백 / 소액현금지출 / 선수금) ──────────────
        # Vector DB 미매칭 시 패턴 가이드를 분기하고, 후처리 단계에서 라인을 직접 생성한다.
        flag_cash_receipt    = self._is_cash_receipt_only(raw_text)
        flag_small_cash_exp  = self._is_small_cash_expense(raw_text)
        # 선수금 플래그: CI 힌트(또는 미결정) + 계약금/선수금 키워드 조합일 때 활성화
        flag_advance_payment = (
            doctype_hint in ("CI", None)
            and any(kw in raw_text for kw in self._ADVANCE_PAYMENT_KW)
        )
        # 선급금 플래그: SI/GL 힌트 + 계약금/선급금 키워드 + AP방향 키워드 조합일 때 활성화
        # AP_KEYWORDS(지급/송금 등) 가드로 "계약금 입금"(AR) 오판 방지.
        # flag_advance_payment과 상호 배제: 동일 키워드가 양쪽 감지 시 선수금(CI) 우선.
        flag_advance_purchase = (
            doctype_hint in ("SI", "GL", None)
            and any(kw in raw_text for kw in self._ADVANCE_PURCHASE_KW)
            and any(kw in raw_text for kw in self._AP_KEYWORDS)
            and not flag_advance_payment
        )
        # ── 법인카드 플래그: 단순 키워드 존재가 아닌 실제 결제 수단 판별 ──────────
        # 부정어/대체어가 있으면 "법카 언급됐지만 실제 결제는 다른 수단" → False
        _CORP_CARD_KW    = ['법카', '법인카드']
        _CORP_CARD_NEG   = ['한도 초과', '한도초과', '한도 부족', '초과돼', '실패', '안 됐',
                            '안됐', '못 써', '못써', '대신', '대신에', '개인카드', '개인 카드']
        _corp_mentioned  = any(kw in raw_text for kw in _CORP_CARD_KW)
        _corp_negated    = any(kw in raw_text for kw in _CORP_CARD_NEG)
        flag_corp_card   = _corp_mentioned and not _corp_negated

        # ── 사비 대납(Out-of-Pocket) 플래그 ─────────────────────────────────────
        # "현금" 단어가 있어도 사비 문맥이면 회사 현금 지출이 아닌 AP(미지급금 211000) 처리
        # 키워드 일치 OR 정규식 패턴 일치 중 하나라도 True → flag_out_of_pocket = True
        _OOP_KW  = [
            '내 현금', '내현금', '사비', '내 돈', '내돈',
            '개인 돈', '개인돈', '사비로', '사비 결제',
            '내가 결제', '내가 냈', '내가 지급', '내가 지불',
            '내 개인카드', '개카', '내 카드로', '내카드로',
        ]
        # 정규식 보조 패턴: "내 XX원 지급", "내가 XX만원" 등 인칭+행위 조합
        # ── 강화 포인트 ────────────────────────────────────────────────────────
        # ① (?<!\w) : 선행 한글/영문자가 없을 때만 인칭대명사 매칭
        #             (예: '제거', '제품' → 앞에 \w 없어도 뒤를 보고 차단)
        # ② (?=[\s가이는은]) : 인칭대명사 다음에 반드시 공백 또는 조사(가·이·는·은)만 허용
        #    → '내일'(내+일), '내려'(내+려), '저번'(저+번), '제거'(제+거) 오매칭 차단
        #    → '내가', '내 현금', '저는', '제가' 등 정상 인칭 표현은 그대로 통과
        _OOP_RE  = re.compile(
            r'(?<!\w)(?:내|제|본인|저)(?=[\s가이는은])(?:\s*(?:가|이|는|은))?\s*(?:현금|돈|카드|개인카드|사비)'
            r'|사비\s*(?:로|결제|지급|지불|냄|냈|냈다)',
            re.IGNORECASE
        )
        flag_out_of_pocket = (
            any(kw in raw_text for kw in _OOP_KW)
            or bool(_OOP_RE.search(raw_text))
        )
        # 하위 호환: 기존 코드에서 사용하던 이름 유지
        flag_personal_exp = flag_out_of_pocket

        # ── 역분개(Reversal) 플래그 ──────────────────────────────────────────
        # "매출감소", "매출취소", "매입반품" 등 역분개 문맥 감지.
        # 감지 시 AI가 생성한 lines의 차대변을 파이썬에서 Swap(D↔C)한다.
        # 전표유형(CI/SI)은 변경하지 않음 — 매출감소도 여전히 CI 거래.
        #
        # 판정 3계층:
        # ① 행위 키워드 + 대상 키워드 동시 존재 (기존 방식)
        # ② 복합어 정규식: '매출감소', '매입취소' 등 subject+action이 붙어있는 단어 감지
        # ③ 독립 역분개 용어: '오입금', '오지급', '네트' — subject 없어도 단독으로 역분개 확정
        _REVERSAL_ACTION_KW  = [
            '감소', '취소', '반품', '환입', '에누리', '마이너스', '차감', '환불',
            '환출', '반송', '반출', '감액', '상계', '반전', '철회', '반환',   # 확장
        ]
        _REVERSAL_SUBJECT_KW = ['매출', '매입', '외상', '채권', '채무', '세금계산서']

        # ② 복합어 정규식: subject와 action이 최대 5자 이내로 인접한 경우 포착
        _REVERSAL_COMPOUND_RE = re.compile(
            r'(?:매출|매입|외상|채권|채무|세금계산서)\s{0,2}'
            r'(?:감소|취소|반품|환입|에누리|차감|환불|환출|반송|반출|감액|상계|반전|철회|반환)',
            re.IGNORECASE
        )
        # ③ 단독 역분개 확정어 (주어/대상 없이도 역분개로 판정)
        _REVERSAL_STANDALONE_KW = ['오입금', '오지급', '네트']

        _has_reversal_action     = any(kw in raw_text for kw in _REVERSAL_ACTION_KW)
        _has_reversal_subject    = any(kw in raw_text for kw in _REVERSAL_SUBJECT_KW)
        _has_reversal_compound   = bool(_REVERSAL_COMPOUND_RE.search(raw_text))
        _has_reversal_standalone = any(kw in raw_text for kw in _REVERSAL_STANDALONE_KW)

        is_opposite_entry = (
            (_has_reversal_action and _has_reversal_subject)   # ① 키워드 조합
            or _has_reversal_compound                          # ② 복합어 정규식
            or _has_reversal_standalone                        # ③ 단독 반대분개(Opposite Entry) 용어
        )

        if flag_cash_receipt:     logger.info(f"[플래그] AR 수금 폴백 활성화: '{raw_text[:30]}'")
        if flag_small_cash_exp:   logger.info(f"[플래그] 소액 현금 지출 활성화: '{raw_text[:30]}'")
        if flag_advance_payment:  logger.info(f"[플래그] 선수금(AR) 감지 → 215000/mulky=A 예정: '{raw_text[:50]}'")
        if flag_advance_purchase: logger.info(f"[플래그] 선급금(AP) 감지 → 120000/mulky=A 예정: '{raw_text[:50]}'")
        if flag_out_of_pocket:    logger.info(f"[플래그] 사비 대납(OOP) 감지 → AP 미지급금 강제 Override 예정: '{raw_text[:50]}'")
        if is_opposite_entry:     logger.info(f"[플래그] 반대분개(Opposite Entry) 감지 → D/C Swap 예정: '{raw_text[:60]}'")
        if _corp_mentioned and _corp_negated:
            logger.info(f"[플래그] 법인카드 언급 있으나 부정/대체 문맥 → flag_corp_card=False: '{raw_text[:40]}'")
        elif flag_corp_card:
            logger.info(f"[플래그] 법인카드 확정 감지: '{raw_text[:30]}'")

        # 1. Vector DB 검색 (RAG) — 쿼리용 임베딩, docty 컬럼 포함 조회
        _t1 = time.time()
        vec = await self.get_embedding(raw_text, input_type="query")
        logger.info(f"[PROF] Voyage embed: {time.time()-_t1:.2f}s")
        query = text("""
            SELECT id, journal_json, docty, (embedding <=> :v) as dist FROM t_v_std_pattern
            UNION ALL
            SELECT id, final_json AS journal_json, NULL::text AS docty, (embedding <=> :v) AS dist
            FROM t_v_user_learn WHERE comcd = :c
            ORDER BY dist ASC LIMIT 1
        """)
        _t2 = time.time()
        cand = db.execute(query, {"v": str(vec), "c": comcd}).fetchone()
        logger.info(f"[PROF] Vector DB search: {time.time()-_t2:.2f}s")

        pattern_id          = None
        pattern_guide       = ""
        parsed_pattern      = None   # 비교 기준용: AI 응답 후 계정 Override 감지에 사용
        gl_fallback_matched = False
        effective_docty     = doctype_hint  # 기본값: Python 키워드 힌트
        needs_review        = False          # STEP3: 완전 매칭 실패 시 True → UI "수동 확인 필요"

        if cand and (1 - cand.dist) > 0.55:
            # ── 1순위: Vector DB 패턴 매칭 성공 → DB 패턴의 docty 사용 ──
            pattern_id = cand.id
            raw_data   = cand.journal_json
            parsed_pattern = json.loads(raw_data) if isinstance(raw_data, str) else raw_data

            # t_v_user_learn.final_json은 {"lines": [...], "doctyp": "...", "modify_reason": "..."}
            # 형태로 저장됨. docty·modify_reason은 내부 메타이므로 AI 프롬프트에는 lines만 전달.
            # t_v_std_pattern.journal_json은 기존 리스트 형태 유지.
            if isinstance(parsed_pattern, dict):
                db_docty      = (parsed_pattern.get("doctyp") or cand.docty or 'GL').strip().upper()
                lines_for_ai  = parsed_pattern.get("lines", [])
            else:
                db_docty      = (cand.docty or 'GL').strip().upper()
                lines_for_ai  = parsed_pattern

            pattern_guide  = f"### [필수 참조 패턴]\n{json.dumps(lines_for_ai, ensure_ascii=False)}"

            # ── 출금 동사 우선 원칙 (DB 패턴 오염 차단) ─────────────────────────────
            # '지급/송금/이체/결제' 등 명시적 출금 동사가 있고 입금 동사가 없으면
            # 과거 학습 데이터의 잘못된 CI 패턴을 무시하고 SI로 강제 확정한다.
            # '받/입금/수금' 보호 키워드로 "지급받다" 등 입금 문맥 오판을 방지한다.
            _INFLOW_GUARD     = ['받', '입금', '수금', '수납', '들어왔', '수취']
            _has_outflow_only = (
                any(kw in raw_text for kw in self._INSTANT_PAY_KW)
                and not any(kw in raw_text for kw in _INFLOW_GUARD)
            )
            if _has_outflow_only and db_docty != 'SI':
                effective_docty = 'SI'
                logger.info(
                    f"[doctype 동사우선] 출금동사 감지·입금동사 없음, "
                    f"DB='{db_docty}' 무시 → SI 강제 확정 (raw='{raw_text[:40]}')"
                )
            else:
                # Python 키워드 힌트(CI/SI)는 DB 패턴 유형보다 우선한다.
                # doctype_hint == 'GL'(방향 불명)일 때만 DB 패턴 유형을 따른다.
                effective_docty = doctype_hint if doctype_hint != 'GL' else db_docty
                if effective_docty != db_docty:
                    logger.info(
                        f"[doctype 힌트우선] hint='{doctype_hint}' > DB='{db_docty}' → {effective_docty}"
                    )

            logger.info(f"Vector DB match: id={pattern_id} dist={cand.dist:.4f} effective_docty={effective_docty}")
        else:
            # Vector DB 매칭 실패 → 키워드 힌트 기반으로 분기
            effective_docty = doctype_hint
            if doctype_hint == 'GL':
                # ── 소액 현금 지출 (퀵비/택배비+현금, Vector DB 미매칭 시 최우선) ──────
                if flag_small_cash_exp:
                    pattern_guide = (
                        "### [소액 현금 지출 패턴 — 반드시 아래 2줄 구조 사용]\n"
                        "[\n"
                        "  {\"debcre\": \"D\", \"glmaster\": \"\",       \"type\": \"EXP\", \"text\": \"비용\"},\n"
                        "  {\"debcre\": \"C\", \"glmaster\": \"100000\", \"type\": \"EXP\", \"text\": \"현금지급\"}\n"
                        "]\n"
                        "차변 EXP glmaster: 문맥에 맞는 비용계정으로 채워라 (퀵/택배→533000, 식대→510100).\n"
                        "대변: 반드시 현금(100000) 고정. 벤더(거래처) 배정 생략."
                    )
                    gl_fallback_matched = True
                    logger.info(f"[소액현금지출] 직접 분개 패턴 적용: '{raw_text[:40]}'")

                # ── 현금 입금 확인 패턴 (거래처 미명시 수금 폴백) ─────────────────────
                elif flag_cash_receipt:
                    pattern_guide = (
                        "### [현금 입금 확인 패턴 — 반드시 아래 2줄 구조 사용]\n"
                        "[\n"
                        "  {\"debcre\": \"D\", \"glmaster\": \"100000\", \"type\": \"EXP\", \"text\": \"입금\"},\n"
                        "  {\"debcre\": \"C\", \"glmaster\": \"\",        \"type\": \"EXP\", \"text\": \"입금정리\"}\n"
                        "]\n"
                        "차변: 현금(100000) 고정.\n"
                        "대변 계정 과목: '현금1' 또는 '가수금' 계정 코드를 알면 채워라. "
                        "모르면 \"\"로 두면 백엔드가 자동으로 채운다."
                    )
                    gl_fallback_matched = True
                    logger.info(f"[현금입금확인] 폴백 패턴 적용: '{raw_text[:40]}'")

                if not flag_small_cash_exp and not flag_cash_receipt:
                    # GL: 계정과목 마스터에서 키워드 검색 (2-tier fallback)
                    kw_set = set()
                    for word in re.findall(r'[가-힣]{2,}', raw_text):
                        for ln in range(min(len(word), 5), 1, -1):
                            kw_set.add(word[:ln])
                    keywords = list(kw_set)[:12]

                    seen_gl = set()
                    gl_hints = []

                    # 회사 사용 계정 t_cglmst 전용 검색 (t_nglmst Fallback 제거 — 무결성 정책)
                    # 오직 해당 회사(comcd)의 t_cglmst에 등록된 계정만 사용 허용.
                    for kw in keywords:
                        rows = db.execute(
                            text("SELECT glmaster, glname1, gltype FROM t_cglmst WHERE comcd=:c AND glname1 ILIKE :k LIMIT 3"),
                            {"c": comcd, "k": f"%{kw}%"}
                        ).fetchall()
                        for row in rows:
                            if row[0] not in seen_gl:
                                seen_gl.add(row[0])
                                gl_hints.append({"src": "cgl", "glmaster": row[0], "glname1": row[1], "gltype": row[2]})
                        if len(gl_hints) >= 8:
                            break

                    if gl_hints:
                        pattern_guide = (
                            f"### [계정 과목 후보 — 아래 중 적절한 계정 사용]\n"
                            f"{json.dumps(gl_hints, ensure_ascii=False)}"
                        )
                        gl_fallback_matched = True
                        logger.info(f"GL fallback(t_cglmst) matched {len(gl_hints)} accounts")
                    else:
                        # ── STEP3: t_cglmst 매칭 완전 실패 → 사용자 조치 필요 안내 ──
                        # t_nglmst(표준계정)에서 대체 매핑하지 않음 — 회계 무결성 정책.
                        # 사용자가 마스터 관리에서 계정을 추가한 후 재시도해야 함.
                        pattern_guide = (
                            "### [계정 과목 설정 필요]\n"
                            "'계정 과목 설정'에 등록되지 않은 항목입니다. "
                            "[마스터 관리] 메뉴에서 해당 계정을 추가한 후 다시 시도해 주세요.\n"
                            "계정 코드는 절대로 임의로 생성하거나 추론하지 마라. "
                            "모든 계정 코드(glmaster)는 \"\"(빈 문자열)로 두어라."
                        )
                        gl_fallback_matched = False
                        needs_review = True
                        logger.warning(
                            f"[STEP3 Miss] t_cglmst 키워드 검색 실패 — needs_review=True 설정, "
                            f"raw_text='{raw_text[:40]}'"
                        )
            else:
                # CI/SI: 프롬프트 내 고정 템플릿으로 AI가 직접 처리 (별도 DB 검색 불필요)
                logger.info(f"No vector match, hint={doctype_hint}, delegating to AI standard template")

        # 결정된 전표 유형을 AI 프롬프트에 강제 지시 (하이브리드 결과 — AI가 임의 변경 금지)
        hint_section = (
            f"\n### [전표유형 최종 결정] doctyp은 반드시 '{effective_docty}'로만 출력할 것"
            f" (DB패턴 + 키워드 분석 하이브리드 결과)"
        )

        # ── 사용자 명시 계정명 탐지 → XML CRITICAL_OVERRIDE 주입 ──────────────
        # 1) t_cglmst에서 (glmaster, glname1, gltype) 전체 목록 1회 조회
        # 2) raw_text에 glname1이 포함되어 있으면 explicit_gl_match에 저장
        # 3) 프롬프트에 XML 절대 규칙 주입 (LLM 1차 통제)
        # 4) LLM 응답 후 파이썬 강제 보정으로 2차 방어 (아래 PHASE 0-X에서 처리)
        _explicit_gl_hint  = ""
        explicit_gl_match  = None   # {glmaster, glname1, gltype} — 후처리 강제 보정용
        try:
            _gl_master_rows = db.execute(
                text("""
                    SELECT glmaster, glname1, gltype
                    FROM   t_cglmst
                    WHERE  glname1 IS NOT NULL
                    ORDER  BY length(glname1) DESC   -- 긴 이름 우선 매칭
                """)
            ).fetchall()

            # 길이 내림차순으로 이미 정렬되어 있으므로 그대로 순회
            for _row in _gl_master_rows:
                _gcode, _gname, _gtype = _row[0], (_row[1] or "").strip(), _row[2]
                if len(_gname) < 2:
                    continue  # 1글자 오탐 방지
                if _gname in raw_text:
                    explicit_gl_match = {
                        "glmaster": _gcode,
                        "glname1":  _gname,
                        "gltype":   _gtype,
                    }
                    break

            if explicit_gl_match:
                _em_code = explicit_gl_match["glmaster"]
                _em_name = explicit_gl_match["glname1"]

                # AP 문맥(비용/매입/구매): 차변 메인 계정 지시
                _is_ap_ctx = effective_docty in ("SI", "GL") or any(
                    kw in raw_text for kw in ['구매', '매입', '지급', '발주', '구입', '비용', '결제']
                )
                # AR 문맥(매출/수금): 대변 메인 계정 지시
                _is_ar_ctx = effective_docty == "CI" or any(
                    kw in raw_text for kw in ['매출', '판매', '납품', '청구', '수금']
                )

                if _is_ap_ctx and not _is_ar_ctx:
                    _side_hint = (
                        f"차변(비용/자산) 메인 계정으로 반드시 계정코드 '{_em_code}'({_em_name})을 사용하십시오. "
                        f"소모품·비품·기타 모든 임의 추론을 엄격히 금지합니다."
                    )
                elif _is_ar_ctx and not _is_ap_ctx:
                    _side_hint = (
                        f"대변(수익/매출) 메인 계정으로 반드시 계정코드 '{_em_code}'({_em_name})을 사용하십시오. "
                        f"임의 추론을 엄격히 금지합니다."
                    )
                else:
                    _side_hint = (
                        f"분개의 메인 계정(비용·자산·수익 라인)으로 반드시 계정코드 '{_em_code}'({_em_name})을 사용하십시오. "
                        f"임의 추론을 엄격히 금지합니다."
                    )

                # XML 태그 + 계정코드 명시: 단순 텍스트 힌트보다 LLM 준수율이 높음
                _explicit_gl_hint = (
                    f"\n<CRITICAL_OVERRIDE>"
                    f"사용자가 텍스트 내에 '{_em_name}' 계정을 명시했습니다. "
                    f"{_side_hint}"
                    f"</CRITICAL_OVERRIDE>"
                )
                logger.info(
                    f"[명시계정 Override] '{_em_name}'({_em_code}) 탐지 → XML 프롬프트 주입 + 후처리 강제 보정 예약 "
                    f"(AP ctx={_is_ap_ctx}, AR ctx={_is_ar_ctx})"
                )
        except Exception as _e:
            logger.warning(f"[명시계정 탐지] DB 조회 실패, 힌트 생략: {_e}")

        # 2. AI 지시문 구성
        if not self.system_prompt_template:
            raise Exception("System prompt template not loaded.")

        today_str = date.today().isoformat()   # 예: 2026-03-09
        prompt = (
            self.system_prompt_template
            .replace("{{TODAY_DATE}}", today_str)
            .replace("{{RAW_TEXT}}", raw_text)
            .replace("{{PATTERN_GUIDE}}", f"{hint_section}{_explicit_gl_hint}\n{pattern_guide}")
        )

        # ── Gemini 2.5 Flash 비동기 호출 (최대 2회 재시도) ──────────────────
        # gemini.aio.models.generate_content: 네이티브 async → 이벤트 루프 블로킹 없음
        max_retries = 2
        resp_text = ""
        _t_api = time.time()
        for attempt in range(max_retries):
            try:
                response = await self.gemini.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=self.system_instruction,
                        temperature=0.0,
                        response_mime_type="application/json",
                        max_output_tokens=2048,          # 분개 JSON은 최대 ~800 토큰 → 여유값 2048
                        thinking_config=types.ThinkingConfig(
                            thinking_budget=0,           # ★ Thinking 모드 OFF → 10~15s 절감
                        ),
                    ),
                )
                resp_text = response.text or ""
                logger.info(f"[PROF] Gemini API call: {time.time()-_t_api:.2f}s")
                # finish_reason 로깅: MAX_TOKENS 등 비정상 종료 감지
                try:
                    finish = response.candidates[0].finish_reason
                    logger.info(f"Gemini finish_reason: {finish}")
                    if str(finish) in ("FinishReason.MAX_TOKENS", "MAX_TOKENS", "2"):
                        logger.warning("Gemini 응답이 토큰 한도로 잘렸습니다 — resp_text 복구 시도")
                except Exception:
                    pass
                break
            except Exception as e:
                logger.warning(f"Gemini API attempt {attempt + 1}/{max_retries} 실패: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)   # 재시도 대기: 2*(n+1)s → 1s 고정
                    continue
                raise Exception(f"Gemini API 호출 실패 ({max_retries}회 재시도): {str(e)}")

        try:
            _t_parse = time.time()
            # 1순위: 순수 JSON 파싱 (response_mime_type=application/json 정상 동작 시)
            try:
                result_json = json.loads(resp_text)
            except json.JSONDecodeError:
                logger.warning(f"Gemini 응답 JSON 파싱 실패 — 복구 시도 (길이={len(resp_text)}자)")

                # 마크다운 코드 펜스 제거 (```json ... ``` 형태 대응)
                cleaned = re.sub(r'^```(?:json)?\s*', '', resp_text.strip(), flags=re.IGNORECASE)
                cleaned = re.sub(r'\s*```$', '', cleaned.strip())

                # 중괄호 기준 JSON 블록 추출
                start_idx = cleaned.find('{')
                if start_idx != -1:
                    brace_count = 0
                    end_idx = start_idx
                    for i, char in enumerate(cleaned[start_idx:]):
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                        if brace_count == 0:
                            end_idx = start_idx + i + 1
                            break
                    cleaned = cleaned[start_idx:end_idx]

                # 잘린 JSON 자동 복구: 마지막 완전한 필드까지만 유지
                # trailing comma + 미완성 문자열/키 제거 후 닫는 괄호 보충
                cleaned = re.sub(r',\s*([\]}])', r'\1', cleaned)   # trailing comma
                # 잘린 문자열 값이 남아있으면 제거 (따옴표 홀수 → 마지막 불완전 키-값 삭제)
                if cleaned.count('"') % 2 != 0:
                    cleaned = re.sub(r',?\s*"[^"]*$', '', cleaned)
                # 열린 괄호/중괄호 자동 닫기
                open_braces  = cleaned.count('{') - cleaned.count('}')
                open_brackets = cleaned.count('[') - cleaned.count(']')
                cleaned += ']' * open_brackets + '}' * open_braces

                result_json = json.loads(cleaned, strict=False)
                logger.info("Gemini 잘린 JSON 복구 성공")
            result_json['pattern_id']   = pattern_id
            result_json['doctyp']       = effective_docty
            result_json['source']       = "DB" if pattern_id else ("FB" if gl_fallback_matched else "AI")
            result_json['needs_review'] = needs_review   # STEP3: UI "수동 확인 필요" 플래그

            # ── 필드명 정규화 (AI 출력 키 → 백엔드 표준 키) ─────────────────
            # AI 프롬프트에서 "tax_date"로 추출했을 때도 "tax_invoice_date"로 통일.
            # is_empty_value로 가짜 빈 값(" ", "-", "null")도 걸러냄.
            _tid_cur = result_json.get("tax_invoice_date")
            _tdt_cur = result_json.get("tax_date")
            if self.is_empty_value(_tid_cur) and not self.is_empty_value(_tdt_cur):
                result_json["tax_invoice_date"] = _tdt_cur.strip()
                result_json.pop("tax_date", None)
            elif not self.is_empty_value(_tid_cur):
                result_json["tax_invoice_date"] = _tid_cur.strip()
                result_json.pop("tax_date", None)
            else:
                # 둘 다 없거나 가짜 빈 값 → 빈 문자열로 클렌징
                result_json["tax_invoice_date"] = ""
                result_json.pop("tax_date", None)
            logger.info(
                f"[tax_invoice_date 정규화] → '{result_json['tax_invoice_date']}'"
                f" (원본 tax_invoice_date='{_tid_cur}', tax_date='{_tdt_cur}')"
            )

            # ── 세금계산서 발행일 정규식 보완 (AI 미추출 시 원문에서 직접 탐지) ──────
            # 키워드: "세금계산서/계산서/세금" + "발행일/일자/날짜" 뒤의 날짜 패턴 추출
            # 예: "세금계산서 발행일 26.3.31" → "2026-03-31"
            # Fallback: 증빙 확정 키워드가 있을 때만 전표일을 복사, 없으면 빈값 유지
            if not result_json.get("tax_invoice_date"):
                _TAX_DATE_RE = re.compile(
                    r'(?:세금계산서|계산서|세금)\s*(?:발행일|일자|날짜)[^\d]*'
                    r'(\d{2,4})[.\-/년](\d{1,2})[.\-/월](\d{1,2})',
                    re.IGNORECASE
                )
                _td_m = _TAX_DATE_RE.search(raw_text)
                if _td_m:
                    _y, _m, _d = _td_m.groups()
                    if len(_y) == 2:
                        _y = '20' + _y
                    result_json["tax_invoice_date"] = f"{_y}-{int(_m):02d}-{int(_d):02d}"
                    logger.info(
                        f"[tax_invoice_date 정규식 보완] 원문에서 발행일 추출: "
                        f"'{result_json['tax_invoice_date']}'"
                    )
                else:
                    # 명시적 발행일 미감지 → 증빙 확정 키워드 있을 때만 전표일 복사
                    # 증빙 언급이 없는 일반 경비는 빈값 유지 (사용자가 직접 입력)
                    # '전자' 단독어 제외 — '전자제품', '전자기기' 등 비증빙 단어 오탐 방지
                    # '전자계산서' / '전자세금계산서' 는 명시적 증빙 행위이므로 유지
                    _invoice_trigger_words = [
                        '발행', '계산서', '세금계산서', '발급', '수취', '수신',
                        '영수증', '전자계산서', '전자세금계산서', '끊다', '끊어',
                    ]
                    _has_tax_mention = any(kw in raw_text for kw in _invoice_trigger_words)
                    if _has_tax_mention:
                        _fallback_dt = result_json.get("date", "")
                        result_json["tax_invoice_date"] = _fallback_dt
                        logger.info(
                            f"[tax_invoice_date fallback] 증빙 키워드 감지 → "
                            f"trx_date='{_fallback_dt}' 복사"
                        )
                    else:
                        result_json["tax_invoice_date"] = ""
                        logger.info(
                            "[tax_invoice_date fallback] 증빙 언급 없음 → 빈값 유지 (사용자 직접 입력)"
                        )

            # ── 라인 내 due_date 정규화 → duedt 로 복사 (PHASE D 처리를 위해) ─
            # AI가 line["due_date"]로 반환하는 경우가 있으므로 line["duedt"]에 흡수
            for _l in result_json.get("lines", []):
                if not _l.get("duedt") and _l.get("due_date"):
                    _l["duedt"] = _l["due_date"]

            # ── 라인별 출처 태그 초기화 (프론트엔드 배지 결정용) ──────────────────────
            # "DB"       : Vector DB 패턴 매칭 (t_v_std_pattern / t_v_user_learn)
            # "FALLBACK" : t_cglmst 텍스트 검색(GL fallback) 또는 현금/규칙 기반 패턴 가이드
            # "AI"       : Vector DB 미매칭, t_cglmst 미탐지 → Gemini 순수 추론
            # AR수금폴백(시스템 규칙), Intent Override(intent_override=True)는
            # 이후 단계에서 각자 설정/플래그로 처리되므로 여기서는 기본값만 주입.
            # 1. 벡터 패턴 완전 일치 → "DB"  (UI: 패턴참조)
            # 2. 패턴 없이 마스터 DB 계정 매칭 → "FB"  (UI: 계정참조)
            # 3. 순수 AI 추론 → "AI"  (UI: AI추론)
            if pattern_id:
                _line_src = "DB"
            elif gl_fallback_matched:
                _line_src = "FB"
            else:
                _line_src = "AI"
            for _l in result_json.get("lines", []):
                _l.setdefault("source", _line_src)  # 이미 설정된 source(예: 규칙 반영) 유지

            # ── 패턴 대비 계정 Override 감지 로그 ───────────────────────────────
            # AI가 참조 패턴의 glmaster를 그대로 썼는지, 문맥에 맞게 교체했는지 기록
            if parsed_pattern and pattern_id:
                orig_lines = (
                    parsed_pattern.get("lines", [])
                    if isinstance(parsed_pattern, dict)
                    else parsed_pattern
                )
                ai_lines = result_json.get("lines", [])
                overrides = [
                    f"line[{i}] {o.get('glmaster','')} → {a.get('glmaster','')}"
                    for i, (o, a) in enumerate(zip(orig_lines, ai_lines))
                    if isinstance(o, dict) and isinstance(a, dict)
                    and o.get("glmaster", "") and a.get("glmaster", "")
                    and o.get("glmaster") != a.get("glmaster")
                ]
                if overrides:
                    logger.info(
                        f"[Account Override] 패턴(id={pattern_id}) 대비 계정 교체 "
                        f"{len(overrides)}건: {', '.join(overrides)}"
                    )
                else:
                    logger.info(
                        f"[Account Match] 패턴(id={pattern_id}) 계정 그대로 사용"
                    )
            
            # ── 외화 통화 감지 (calculate_accounting_amounts 호출 전 필수) ────────
            # 우선순위: LLM이 이미 채운 currency 필드 → 원문 패턴 감지 → local_currency
            # local_currency는 함수 진입 시 t_company에서 조회한 회사 기준 통화.
            # local_currency 기준으로 외화 여부를 판단하므로 KRW 하드코딩 없음.
            _FX_MARKERS = [
                ('USD', [r'USD', r'\$', r'달러']),
                ('JPY', [r'JPY', r'[¥円]', r'엔화', r'\d\s*엔']),
                ('EUR', [r'EUR', r'€']),
                ('CNY', [r'CNY', r'RMB', r'위안']),
                ('GBP', [r'GBP', r'£']),
            ]
            _pre_cur = (result_json.get("currency") or local_currency).strip().upper()
            if _pre_cur == local_currency:
                for _cur_code, _pats in _FX_MARKERS:
                    if any(re.search(p, raw_text, re.IGNORECASE) for p in _pats):
                        _pre_cur = _cur_code
                        break
            result_json["currency"]       = _pre_cur
            result_json["local_currency"] = local_currency   # 프론트 기본값 표시용
            if _pre_cur != local_currency:
                logger.info(f"[외화감지] currency={_pre_cur} (local={local_currency}) — 단위 보정 건너뜀")

            # ══════════════════════════════════════════════════════════════════
            # ── 외화 환율 조회 (t_nexrate) — 금액 계산 전 확정 ────────────────
            # 환율은 모든 금액 계산의 기준이므로 calculate_accounting_amounts
            # 호출 전에 먼저 확정한다. PHASE C/D 등 이후 로직도 정확한 exrate 활용.
            # 조건: extype='S'(매매기준율), srccur=감지통화, tarcur=local_currency
            # 당일 데이터가 없으면 기준일 이전 가장 가까운 데이터(As-of Query) 사용.
            # ══════════════════════════════════════════════════════════════════
            _final_cur = result_json.get("currency",       local_currency)
            _local_cur = result_json.get("local_currency", local_currency)
            if _final_cur and _final_cur != _local_cur:
                _ref_dt = result_json.get("date") or ""
                if not _ref_dt:
                    import datetime as _dt_mod
                    _ref_dt = _dt_mod.date.today().isoformat()
                try:
                    _er_row = db.execute(text("""
                        SELECT exrat FROM t_nexrate
                        WHERE  extype = 'S'
                          AND  srccur = :srccur
                          AND  tarcur = :local_cur
                          AND  date   <= :target_date
                        ORDER  BY date DESC
                        LIMIT  1
                    """), {
                        "srccur":      _final_cur,
                        "local_cur":   _local_cur,
                        "target_date": _ref_dt,
                    }).fetchone()
                    if _er_row:
                        result_json["exrate"] = float(_er_row[0])
                        logger.info(
                            f"[환율조회] {_final_cur}/{_local_cur} = {result_json['exrate']:,.4f} "
                            f"(기준일≤{_ref_dt}, t_nexrate)"
                        )
                    else:
                        result_json["exrate"] = 1.0
                        result_json.setdefault("validation_warnings", []).append(
                            f"{_final_cur}/{_local_cur} 환율 데이터가 없습니다. "
                            f"환율을 직접 입력해 주세요."
                        )
                        result_json["needs_review"] = True
                        logger.warning(
                            f"[환율조회] {_final_cur}/{_local_cur} 데이터 없음 (기준일≤{_ref_dt}) "
                            f"→ exrate=1, 직접 입력 필요"
                        )
                except Exception as _er_exc:
                    result_json["exrate"] = 1.0
                    logger.error(f"[환율조회 오류] {_er_exc}")
            else:
                result_json.setdefault("exrate", 1.0)
                result_json["currency"] = _local_cur

            # 💡 파이썬 백엔드 수학적 금액 강제 계산 로직 (AMOUNT_EXTRACTION_RULES 반영)
            self.calculate_accounting_amounts(result_json, raw_text)
            tot = result_json["total_amount"]
            base = result_json["supply_base"]
            vat = result_json["supply_vat"]
            vr = float(result_json.get("vat_rate", 0))

            # ── total_amount 유효성 검증 ──────────────────────────────────────
            if tot <= 0:
                logger.error(f"total_amount가 0 또는 음수: {tot} | raw_text='{raw_text[:60]}'")
                raise Exception(f"AI가 금액을 추출하지 못했습니다 (total_amount={tot}). 문장을 다시 입력하세요.")

            logger.info(f"금액 안분: tot={tot:,.0f} / vr={vr}% → base={base:,.0f}, vat={vat:,.0f} (mode={result_json.get('amt_mode', 'TOTAL')})")

            # 만기일 변수 확보
            due_date = result_json.get("due_date", "")
            
            # 3. 마스터 DB 정밀 매핑 (pg_trgm similarity 검색, 전처리 후 검색어 사용)
            # ── 거래처 (t_cbizpt) ─────────────────────────────────────────────
            # ── AI JSON 키 정규화: AI가 임의 키명으로 거래처를 반환할 경우 bizname으로 통일 ──
            # AI가 vendor_name / partner_name / bizname1 등의 키를 사용할 수 있으므로
            # 먼저 알려진 대체 키들을 bizname으로 흡수한다. 이미 bizname이 있으면 유지.
            for _alias in ("vendor_name", "partner_name", "company_name", "bizname1"):
                _alias_val = result_json.pop(_alias, None)
                if _alias_val and not result_json.get("bizname"):
                    result_json["bizname"] = _alias_val
                    logger.info(f"[Bizname 키 정규화] '{_alias}' → 'bizname': '{_alias_val}'")
                    break

            bn_raw = result_json.get("bizname", "")
            bn     = self._clean_search_term(bn_raw)
            result_json["bizptcd"] = ""
            result_json["biz_match_score"]  = 0.0
            result_json["biz_match_status"] = "NONE"
            biz_suppgl, biz_custgl = "", ""

            # ★ 보강: AI가 bizname을 추출 못 했거나 빈 경우, raw_text에서 직접 상호 탐지 시도
            if not bn:
                cp_match = self._COUNTERPARTY_RE.search(raw_text)
                if cp_match:
                    bn = self._clean_search_term(cp_match.group(0))
                    logger.info(f"Bizpt 자동 탐지: AI미추출 → regex '{bn}'")

            if bn:
                if bn != bn_raw:
                    logger.info(f"Bizpt 전처리: '{bn_raw}' → '{bn}'")
                row = db.execute(
                    text("""
                        SELECT bizptcd, bizname1, suppgl, custgl,
                               similarity(bizname1, :n) AS score
                        FROM t_cbizpt
                        WHERE comcd = :c AND similarity(bizname1, :n) >= 0.25
                        ORDER BY similarity(bizname1, :n) DESC
                        LIMIT 1
                    """),
                    {"c": comcd, "n": bn}
                ).fetchone()
                if row:
                    score  = float(row.score)
                    status = self._match_status(score)
                    result_json["biz_match_score"]  = round(score, 3)
                    result_json["biz_match_status"] = status
                    if status != "NONE":
                        result_json["bizptcd"]  = row.bizptcd
                        result_json["bizname"]  = row.bizname1
                        biz_suppgl = row.suppgl or ""
                        biz_custgl = row.custgl or ""
                    logger.info(f"Bizpt similarity: '{bn}' → '{row.bizname1}' score={score:.3f} [{status}]")
                else:
                    logger.info(f"Bizpt: no similarity match for '{bn}' (score < 0.25)")

            # ── LIKE 부분 일치 Fallback ────────────────────────────────────────
            # similarity 검색에서 0.25 미만으로 실패한 경우(예: '삼성전자' vs '삼성전자(주)')
            # ILIKE '%검색어%' 로 재탐색하여 안전하게 매핑.
            if bn and not result_json.get("bizptcd"):
                like_row = db.execute(
                    text("""
                        SELECT bizptcd, bizname1, suppgl, custgl
                        FROM t_cbizpt
                        WHERE comcd = :c
                          AND bizname1 ILIKE :n
                        ORDER BY bizptcd ASC
                        LIMIT 1
                    """),
                    {"c": comcd, "n": f"%{bn}%"}
                ).fetchone()
                if like_row:
                    result_json["bizptcd"]          = like_row.bizptcd
                    result_json["bizname"]          = like_row.bizname1
                    result_json["biz_match_status"] = "FUZZY"
                    biz_suppgl = like_row.suppgl or ""
                    biz_custgl = like_row.custgl or ""
                    logger.info(
                        f"Bizpt LIKE fallback: '%{bn}%' → "
                        f"'{like_row.bizname1}' ({like_row.bizptcd})"
                    )
                else:
                    logger.info(f"Bizpt LIKE fallback: '%{bn}%' 도 일치 없음")

            # ── 최종 안전망: 법인격 제거 후 LIKE 강화 검색 ──────────────────────────
            # similarity + 1차 LIKE 이후에도 bizptcd가 여전히 없을 때 실행.
            # '주식회사', '(주)' 등 법인 접미사를 모두 제거한 핵심 키워드로 재탐색.
            # 결과 존재 시 result_json의 bizptcd / bizname을 DB 값으로 강제 덮어쓰기.
            # try-except 로 감싸 이 단계의 오류가 전표 파싱 전체를 중단시키지 않게 보장.
            if bn_raw and not result_json.get("bizptcd"):
                try:
                    _corp_sfx = [
                        "주식회사", "(주)", "유한회사", "(유)",
                        "합자회사", "(합)", "합명회사", "협동조합",
                    ]
                    _bn_core = bn_raw                   # AI 추출 원본(정제 전) 사용
                    for _sfx in _corp_sfx:
                        _bn_core = _bn_core.replace(_sfx, "")
                    _bn_core = _bn_core.strip()

                    if _bn_core:
                        _safe_row = db.execute(
                            text("""
                                SELECT bizptcd, bizname1, suppgl, custgl
                                FROM t_cbizpt
                                WHERE comcd = :c
                                  AND bizname1 ILIKE :n
                                ORDER BY bizptcd ASC
                                LIMIT 1
                            """),
                            {"c": comcd, "n": f"%{_bn_core}%"}
                        ).fetchone()
                        if _safe_row:
                            # 강제 덮어쓰기: DB의 정확한 마스터 값으로 최종 갱신
                            result_json["bizptcd"]          = _safe_row.bizptcd
                            result_json["bizname"]          = _safe_row.bizname1
                            result_json["biz_match_status"] = "FUZZY"
                            biz_suppgl = _safe_row.suppgl or ""
                            biz_custgl = _safe_row.custgl or ""
                            logger.info(
                                f"[Bizpt 법인격 제거 검색] '{bn_raw}' → "
                                f"core='{_bn_core}' → DB: "
                                f"'{_safe_row.bizname1}' ({_safe_row.bizptcd})"
                            )
                        else:
                            logger.info(
                                f"[Bizpt 법인격 제거 검색] '%{_bn_core}%' 일치 없음 "
                                f"— 수동 입력 필요"
                            )
                    else:
                        logger.info(
                            f"[Bizpt 법인격 제거 검색] 법인격 제거 후 핵심어 없음 "
                            f"(원본: '{bn_raw}')"
                        )
                except Exception as _biz_ex:
                    logger.warning(
                        f"[Bizpt 법인격 제거 검색 오류] {_biz_ex} — 거래처 매핑 생략"
                    )

            # ── AP 가상 벤더 검색 (SI 거래 + 거래처 미확인) ───────────────────────────
            # 지출 거래에서 거래처명이 명시되지 않은 경우, t_cbizpt에서 '가상거래처'를 검색해 매핑.
            # 가상거래처 미존재 시 bizptcd_required=True → UI에서 사용자 수동 입력 유도.
            if effective_docty == 'SI' and not result_json.get("bizptcd"):
                vv_row = db.execute(
                    text("""
                        SELECT bizptcd, bizname1, suppgl
                        FROM t_cbizpt
                        WHERE comcd = :c
                          AND (bizname1 ILIKE '%가상%' OR bizname1 ILIKE '%임시%')
                        ORDER BY bizptcd ASC
                        LIMIT 1
                    """),
                    {"c": comcd}
                ).fetchone()
                if vv_row:
                    result_json["bizptcd"]          = vv_row.bizptcd
                    result_json["bizname"]          = vv_row.bizname1
                    result_json["biz_match_status"] = "FUZZY"
                    biz_suppgl = vv_row.suppgl or ""
                    logger.info(f"[가상벤더] '{vv_row.bizname1}' ({vv_row.bizptcd}) 자동 배정")
                else:
                    result_json["bizptcd_required"] = True
                    result_json["needs_review"]     = True
                    logger.warning("[가상벤더] t_cbizpt에 가상/임시거래처 미발견 — 사용자 수동 입력 필요 (bizptcd_required=True)")

            # ── 거래처 field_control 생성 ──────────────────────────────────────
            # 매핑 결과(biz_match_status)와 법인카드 플래그에 따라 UI 제어 신호를 프론트에 전달.
            # · EXACT  → OK (별도 표시 없음)
            # · FUZZY  → WARNING_YELLOW: 유사 거래처 확인 요청
            # · NONE   → REQUIRED_RED: 거래처 미지정 (법인카드일 때도 동일)
            _biz_status = result_json.get("biz_match_status", "NONE")
            if _biz_status == "FUZZY":
                result_json["field_control_partner"] = {
                    "status": "WARNING_YELLOW",
                    "msg":    f"'{result_json.get('bizname','')}' 거래처가 검색되었습니다. 정확한 거래처인지 확인해 주세요.",
                }
            elif _biz_status == "NONE" or not result_json.get("bizptcd"):
                _fc_msg = (
                    "법인카드 가맹점(거래처)을 직접 선택해주세요."
                    if flag_corp_card else
                    "등록된 거래처 정보를 찾을 수 없습니다. 새로운 거래처를 검색하거나 선택해 주세요."
                )
                result_json["field_control_partner"] = {
                    "status": "REQUIRED_RED",
                    "msg":    _fc_msg,
                }
            else:
                result_json["field_control_partner"] = {"status": "OK"}

            # ★ 거래처 마스터 계정 선적용: GL 검증 루프 전에 Override해야
            #   아래 GL 검증 로직이 올바른 코드로 glname/gltype을 자동 확보함
            for line in result_json.get("lines", []):
                l_type = line.get("type", "")
                if l_type == "AR" and biz_custgl:
                    line["glmaster"]      = biz_custgl
                    line["biz_gl_locked"] = True
                    logger.info(f"AR line custgl override: {biz_custgl}")
                elif l_type == "AP" and biz_suppgl:
                    line["glmaster"]      = biz_suppgl
                    line["biz_gl_locked"] = True
                    logger.info(f"AP line suppgl override: {biz_suppgl}")

            # ★ Intent GL Override
            # 대상: AI가 별도 추출한 expense_keyword (비용·수익 성격 단어) 하나만 사용
            # item_name(고유명사)과 분리함으로써 "긴다리의자" vs "식대" 충돌 방지
            intent_gl_map = {}   # { glmaster_code: {"glname1": ..., "gltype": ...} }

            exp_kw = result_json.get("expense_keyword", "").strip()
            # _clean_search_term 미적용: 비용 키워드는 조사가 없는 순수 단어이므로 전처리 불필요
            exp_kw_mapped = self._map_common_terms(exp_kw)   # 동의어 → 표준 계정명

            if exp_kw_mapped:
                if exp_kw_mapped != exp_kw:
                    logger.info(f"Intent 동의어 변환: '{exp_kw}' → '{exp_kw_mapped}'")
                row = db.execute(
                    text("""
                        SELECT glmaster, glname1, gltype,
                               similarity(glname1, :k) AS score
                        FROM t_cglmst
                        WHERE comcd = :c AND similarity(glname1, :k) >= 0.3
                        ORDER BY similarity(glname1, :k) DESC
                        LIMIT 1
                    """),
                    {"c": comcd, "k": exp_kw_mapped}
                ).fetchone()
                if row:
                    intent_gl_map[row.glmaster] = {
                        "glname1": row.glname1,
                        "gltype":  row.gltype,
                        "score":   round(float(row.score), 3),
                    }
                    logger.info(
                        f"Intent GL: '{exp_kw}' → (매핑)'{exp_kw_mapped}' "
                        f"→ DB '{row.glname1}' ({row.glmaster}) score={row.score:.3f}"
                    )
                else:
                    logger.info(f"Intent GL: '{exp_kw_mapped}' → no match (score < 0.3)")
            else:
                logger.info("Intent GL: expense_keyword 없음 — Override 건너뜀")

            # ── 관리항목 (t_mbkey) ────────────────────────────────────────────
            im_raw = result_json.get("item_name", "")
            im     = self._clean_search_term(im_raw)
            result_json["manaky"], result_json["mananm"] = "", ""
            result_json["ana_match_score"]  = 0.0
            result_json["ana_match_status"] = "NONE"
            if im:
                if im != im_raw:
                    logger.info(f"AnaKey 전처리: '{im_raw}' → '{im}'")
                row = db.execute(
                    text("""
                        SELECT manaky, mananm,
                               similarity(mananm, :n) AS score
                        FROM t_mbkey
                        WHERE comcd = :c AND similarity(mananm, :n) >= 0.30
                        ORDER BY similarity(mananm, :n) DESC
                        LIMIT 1
                    """),
                    {"c": comcd, "n": im}
                ).fetchone()
                if row:
                    score  = float(row.score)
                    status = self._match_status(score)
                    result_json["ana_match_score"]  = round(score, 3)
                    result_json["ana_match_status"] = status
                    if status != "NONE":
                        result_json["manaky"] = row.manaky
                        result_json["mananm"] = row.mananm
                    logger.info(f"AnaKey similarity: '{im}' → '{row.mananm}' score={score:.3f} [{status}]")
                else:
                    logger.info(f"AnaKey: no similarity match for '{im}' (score < 0.30)")

            # ── 손익부서 (t_cprocos) ──────────────────────────────────────────
            pn_raw = result_json.get("profit_center_name", "")
            pn     = self._clean_search_term(pn_raw)
            result_json["pctrcd"], result_json["pctrnm"] = "", ""
            result_json["pctr_match_score"]  = 0.0
            result_json["pctr_match_status"] = "NONE"
            if pn:
                if pn != pn_raw:
                    logger.info(f"Pctr 전처리: '{pn_raw}' → '{pn}'")
                row = db.execute(
                    text("""
                        SELECT pctrcd, prcrnm,
                               similarity(prcrnm, :n) AS score
                        FROM t_cprocos
                        WHERE comcd = :c AND similarity(prcrnm, :n) >= 0.30
                        ORDER BY similarity(prcrnm, :n) DESC
                        LIMIT 1
                    """),
                    {"c": comcd, "n": pn}
                ).fetchone()
                if row:
                    score  = float(row.score)
                    status = self._match_status(score)
                    result_json["pctr_match_score"]  = round(score, 3)
                    result_json["pctr_match_status"] = status
                    if status != "NONE":
                        result_json["pctrcd"] = row.pctrcd
                        result_json["pctrnm"] = row.prcrnm
                    logger.info(f"Pctr similarity: '{pn}' → '{row.prcrnm}' score={score:.3f} [{status}]")
                else:
                    logger.info(f"Pctr: no similarity match for '{pn}' (score < 0.30)")

            # ── 전체 match_score (3개 필드 중 최고점) ─────────────────────────
            result_json["match_score"] = round(max(
                result_json["biz_match_score"],
                result_json["pctr_match_score"],
                result_json["ana_match_score"],
            ), 3)

            result_json["taxcd"] = ""
            result_json["taxnm"] = ""
            txcd = result_json["taxcd"]

            # ── txgubun 조회: t_ntxkey JOIN t_ctxkey → 세금 처리 유형 판별 ──────────
            # '1': 일반과세(10%, 세액공제 가능)
            # 'D': 매입불공제(10%이나 세액공제 불가 → EXP 라인에 세액 합산)
            # '2','3': 영세율·면세(0%, TAX 라인 없음)
            txgubun = ""
            if txcd:
                _tg = db.execute(text("""
                    SELECT n.txgubun
                    FROM   t_ctxkey c
                    JOIN   t_ntxkey n ON c.taxcd = n.taxcd
                    WHERE  c.comcd = :c AND c.taxcd = :t
                    LIMIT  1
                """), {"c": comcd, "t": txcd}).fetchone()
                if _tg:
                    txgubun = (_tg[0] or "").strip()
            result_json["txgubun"] = txgubun
            logger.info(f"[세금유형] taxcd={txcd or '없음'} → txgubun='{txgubun}'")

            # ── null-gltype 가계정 사전 조회 (AR수금폴백·gltype 검증 공통 사용) ────────
            # gltype IS NULL/'' : 거래처(bizptcd) 없이 사용 가능한 GL 계정 (오픈아이템 불필요)
            # 검색 우선: '가수금'(미확인 수령 표준 가계정) > '현금1'(회사 지정 오픈아이템 계정)
            _clr_row = db.execute(text("""
                SELECT glmaster, glname1
                FROM t_cglmst
                WHERE comcd = :c
                  AND (gltype IS NULL OR gltype = '')
                  AND (glname1 ILIKE '%가수금%' OR glname1 ILIKE '%현금1%')
                ORDER BY glmaster ASC LIMIT 1
            """), {"c": comcd}).fetchone()
            _clr_code = _clr_row.glmaster if _clr_row else ""
            _clr_name = _clr_row.glname1  if _clr_row else "현금1(미확인)"
            if _clr_row:
                logger.info(f"[가계정] null-gltype 조회 성공: {_clr_code}({_clr_name})")
            else:
                logger.warning("[가계정] t_cglmst에 '가수금'/'현금1' null-gltype 계정 미발견 → 후속 폴백에서 needs_review 처리")

            # ── AR 수금 폴백: 거래처 미확인 현금/통장 입금 → 자산계정 / 가계정 직접 분개 생성 ──
            # 조건: ① 입금/수금+현금/통장 키워드  ② 거래처 미매칭
            # ★ pattern_id 조건 제거: Vector DB 이력(t_v_user_learn)에 이전 잘못된 분개(예: 미지급금)가
            #   저장된 경우 반복 오류가 발생하므로, 항상 규칙 기반 자산/가계정 분개로 덮어씀.
            if flag_cash_receipt and not result_json.get("bizptcd"):
                # ── ① 차변 계정 결정: '통장/계좌' → 보통예금, 그 외 → 현금(100000) ──────
                _has_bank = any(kw in raw_text for kw in ['통장', '계좌', '은행'])
                if _has_bank:
                    _debit_row = db.execute(text("""
                        SELECT glmaster, glname1, gltype FROM t_cglmst
                        WHERE comcd=:c
                          AND (glname1 ILIKE '%보통예금%' OR glname1 ILIKE '%통장%')
                        ORDER BY glmaster ASC LIMIT 1
                    """), {"c": comcd}).fetchone()
                    if not _debit_row:   # 보통예금 미등록 시 현금 폴백
                        _debit_row = db.execute(
                            text("SELECT glmaster, glname1, gltype FROM t_cglmst WHERE comcd=:c AND glmaster='100000' LIMIT 1"),
                            {"c": comcd}
                        ).fetchone()
                else:
                    _debit_row = db.execute(
                        text("SELECT glmaster, glname1, gltype FROM t_cglmst WHERE comcd=:c AND glmaster='100000' LIMIT 1"),
                        {"c": comcd}
                    ).fetchone()

                _debit_code  = _debit_row.glmaster if _debit_row else "100000"
                _debit_name  = _debit_row.glname1  if _debit_row else "현금"
                _debit_gtype = _debit_row.gltype   if _debit_row else ""

                # ── ② 대변 가계정: 차변코드와 절대 겹치지 않도록 glmaster != 차변코드 강제 ──
                # _clr_code(사전 조회)가 차변 코드와 동일하거나 없는 경우 재조회
                _cred_code, _cred_name = _clr_code, _clr_name
                if _cred_code == _debit_code or not _cred_code:
                    _clr_row2 = db.execute(text("""
                        SELECT glmaster, glname1 FROM t_cglmst
                        WHERE comcd = :c
                          AND (gltype IS NULL OR gltype = '')
                          AND (glname1 ILIKE '%가수금%' OR glname1 ILIKE '%현금1%')
                          AND glmaster != :excl
                        ORDER BY glmaster ASC LIMIT 1
                    """), {"c": comcd, "excl": _debit_code}).fetchone()
                    _cred_code = _clr_row2.glmaster if _clr_row2 else ""
                    _cred_name = _clr_row2.glname1  if _clr_row2 else "현금1(미확인)"
                    if _clr_row2:
                        logger.info(f"[AR수금폴백] 코드 충돌 재조회: {_cred_code}({_cred_name})")
                    else:
                        logger.warning("[AR수금폴백] 차변 제외 후에도 가계정 미발견")

                # ── ③ 분개 라인 조립 (AR수금 규칙 기반 강제 생성 → source='규칙 반영') ──────────
                result_json["lines"] = [
                    {
                        "debcre": "D", "glmaster": _debit_code, "glname": _debit_name,
                        "gltype": _debit_gtype, "bizamt": tot, "biztax": 0,
                        "type": "EXP", "duedt": "", "text": "입금 확인",
                        "source": "규칙 반영",
                    },
                    {
                        "debcre": "C", "glmaster": _cred_code, "glname": _cred_name,
                        "gltype": "",  "bizamt": tot, "biztax": 0,
                        "type": "EXP", "duedt": "", "text": "입금 확인",
                        "source": "규칙 반영",
                        "needs_review": (not _cred_code),
                    },
                ]
                result_json["doctyp"] = 'GL'
                result_json["source"] = "RULE"
                effective_docty       = 'GL'
                _kind = "통장(보통예금)" if _has_bank else "현금"
                if not _cred_code:
                    result_json["needs_review"] = True
                    logger.warning(
                        f"[AR수금폴백] 가계정 미발견 → 대변 needs_review | "
                        f"D:{_debit_code}({_debit_name})"
                    )
                else:
                    logger.info(
                        f"[AR수금폴백] {_kind} 입금 | "
                        f"D:{_debit_code}({_debit_name}) / C:{_cred_code}({_cred_name})"
                    )

            # 💡 라인별 금액 주입
            # [우선] AI가 라인에 amount > 0 제공 → 해당 라인 개별금액 사용 (복합분개 지원)
            # [폴백] amount == 0 또는 미제공 → type 기반 안분 (AR/AP=tot / EXP/REV=base / TAX=vat)
            # ※ duedt는 GL 검증 후 gltype 기반으로 루프 말미에 결정 (후처리, 아래 ★ 참조)
            used_intent_gl = set()  # Intent Override 중복 방지: 동일 계정을 두 라인에 동시 적용 금지
            for line in result_json.get("lines", []):
                l_type = line.get("type", "").upper()

                # ── 금액 할당 (AI 개별금액 최우선, 미제공/0 시 tot/base Fallback) ──────
                ai_line_amt = float(line.get("amount", 0) or 0)
                if ai_line_amt > 0:
                    # ▶ AI가 개별 금액을 추출한 라인 → tot/base 강제 주입 건너뛰고 적용
                    line["bizamt"] = ai_line_amt
                    line["biztax"] = ai_line_amt if l_type == "TAX" else 0
                    logger.info(f"[금액] AI 개별금액 적용: type={l_type} amt={ai_line_amt:,.0f}")
                elif l_type in ("AR", "AP"):
                    # ▶ 채권·채무 라인: 부가세 포함 총액
                    line["bizamt"], line["biztax"] = tot, 0
                elif l_type in ("REV", "EXP", "COG"):
                    # ▶ 수익·비용·매출원가 라인: 공급가액(부가세 제외)
                    line["bizamt"], line["biztax"] = base, 0
                elif l_type == "TAX":
                    # ▶ 세금 라인: 부가세액
                    line["bizamt"], line["biztax"] = vat, vat
                else:
                    # ▶ type 미인식 라인 — debcre 기반 안전 추론
                    debcre  = line.get("debcre", "")
                    gltype_ = line.get("gltype", "")
                    if debcre == "C" and gltype_ in ("C", "S"):
                        line["bizamt"], line["biztax"] = tot, 0
                        logger.warning(f"type 미인식 → AP fallback: glmaster={line.get('glmaster','')}")
                    elif debcre == "D" and gltype_ == "X":
                        line["bizamt"], line["biztax"] = vat if vr > 0 else tot, vat if vr > 0 else 0
                        logger.warning(f"type 미인식 → TAX fallback: glmaster={line.get('glmaster','')}")
                    else:
                        line["bizamt"], line["biztax"] = base, 0
                        logger.warning(f"type 미인식 → EXP fallback: glmaster={line.get('glmaster','')}, debcre={debcre}")

                # GL 계정명 무결성 검증 + STEP4 고정 폴백 맵핑
                glcd = line.get("glmaster", "")
                line["glname"], line["gltype"] = "", ""
                gl_verified = False
                if glcd:
                    row = db.execute(
                        text("SELECT glname1, gltype FROM t_cglmst WHERE comcd=:c AND glmaster=:g LIMIT 1"),
                        {"c": comcd, "g": glcd}
                    ).fetchone()
                    if row:
                        line["glname"], line["gltype"] = row[0], row[1]
                        gl_verified = True

                if not gl_verified:
                    # ── STEP4: 고정 계정 폴백 맵핑 ──────────────────────────
                    # type + debcre 조합으로 가장 적합한 기본 계정을 자동 부여해
                    # UI에 빈칸("계정 검색 필요")이 남지 않도록 보정.
                    # needs_review=True로 표시해 사용자 확인을 유도한다.
                    _FIXED_FALLBACK = {
                        # (type, debcre) → (glmaster, glname)
                        # ★ AP 라인은 이 테이블에서 제외: 소액현금지출=현금, 일반AP=미지급금 소프트 폴백
                        ("TAX",  "D"): ("213000", "매입부가세"),
                        ("TAX",  "C"): ("214000", "매출부가세"),
                        ("AR",   "D"): ("110000", "외상매출금"),
                        ("AR",   "C"): ("110000", "외상매출금"),
                        ("REV",  "C"): ("720000", "잡수익"),
                        ("REV",  "D"): ("720000", "잡수익"),
                        ("EXP",  "D"): ("579000", "잡비"),
                        ("EXP",  "C"): ("579000", "잡비"),
                    }
                    fb_key = (l_type, line.get("debcre", ""))
                    fb = _FIXED_FALLBACK.get(fb_key)
                    # ── AP 라인 상황별 폴백 (외상매입금(210000) 무조건 주입 중단) ─────────
                    if fb is None:
                        if l_type == "AP":
                            # 소액현금지출 대변 → 현금(100000) / 그 외 AP → 미지급금(211000)
                            fb = ("100000", "현금") \
                                if (flag_small_cash_exp and line.get("debcre") == "C") \
                                else ("211000", "미지급금")
                        else:
                            # type 미정 → debcre로만 판단
                            # 대변: null-gltype 가계정(_clr_code) 우선 / 없으면 미지급금(211000)
                            # AP 성격의 미지급금을 수금 전표 대변에 무조건 넣는 오류 방지
                            fb = (_clr_code or "211000", _clr_name if _clr_code else "미지급금") \
                                if line.get("debcre") == "C" else ("100000", "현금1")
                    fb_code, fb_name = fb
                    # ── STEP4 안전장치: 폴백 코드가 t_cglmst에 실존하는지 반드시 확인 ──
                    # t_cglmst에 없는 코드는 절대 주입하지 않는다 (회계 무결성 정책).
                    # 미존재 시 glmaster를 빈 문자열로 두고 사용자 직접 선택 유도.
                    fb_row = db.execute(
                        text("SELECT glname1, gltype FROM t_cglmst WHERE comcd=:c AND glmaster=:g LIMIT 1"),
                        {"c": comcd, "g": fb_code}
                    ).fetchone()
                    if fb_row:
                        line["glmaster"]     = fb_code
                        line["glname"]       = fb_row[0]
                        line["gltype"]       = fb_row[1]
                        logger.warning(
                            f"[STEP4 폴백] type={l_type} debcre={line.get('debcre','')} "
                            f"→ {fb_code}({fb_row[0]}) t_cglmst 확인 [needs_review][규칙 반영]"
                        )
                    else:
                        # t_cglmst에 폴백 코드 미존재 → 빈 값으로 두고 사용자 수동 선택
                        line["glmaster"]     = ""
                        line["glname"]       = ""
                        line["gltype"]       = ""
                        logger.warning(
                            f"[STEP4 폴백 차단] type={l_type} debcre={line.get('debcre','')} "
                            f"→ '{fb_code}'({fb_name})가 t_cglmst(comcd={comcd})에 없음 "
                            f"→ glmaster='' 처리, 사용자 직접 선택 필요"
                        )
                    line["needs_review"] = True
                    line["source"]       = "규칙 반영"
                    result_json["needs_review"] = True

                # 만기일(duedt): gltype이 C/S가 아닌 라인만 초기화.
                # 단, 이미 AI가 채워준 값(예: due_date 필드로 전달된 값)이 있으면 유지.
                if line.get("gltype") not in ('C', 'S'):
                    line["duedt"]            = ""
                    line["due_date_enabled"] = False

                # ★ Intent Override: 사용자가 직접 언급한 계정명이 있을 때 AI 추론 계정보다 우선 적용
                # AR/AP(거래처 마스터), TAX 라인 및 이미 거래처 잠금된 라인은 제외.
                # 대변(C)이면서 현금 계정(glmaster='100000' 또는 glname에 '현금' 포함)인 라인도 제외:
                # 소액현금지출·운반비 등에서 대변 현금을 의도치 않게 덮어쓰는 것을 방지.
                # ★ pattern_id 성역 보호: 벡터 DB 패턴이 매칭되었다면 문장 전체 맥락으로
                #   '정석 분개'가 이미 확정된 것이므로, 단순 단어 탐지(Intent Override)가
                #   확정된 계정(예: 상품매출 410000)을 덮어쓰지 못하도록 완전 차단한다.
                _is_cash_credit = (
                    line.get("debcre") == "C"
                    and (
                        line.get("glmaster") == "100000"
                        or "현금" in (line.get("glname") or "")
                    )
                )
                if (intent_gl_map
                        and not pattern_id
                        and not line.get("biz_gl_locked")
                        and not _is_cash_credit
                        and l_type not in ["AR", "AP", "TAX"]):
                    for gl_code, gl_info in intent_gl_map.items():
                        if gl_code in used_intent_gl:
                            continue  # 이미 다른 라인에 적용된 계정은 스킵
                        if line.get("glmaster", "") != gl_code:
                            line["glmaster"]        = gl_code
                            line["glname"]          = gl_info["glname1"]
                            line["gltype"]          = gl_info["gltype"]
                            line["intent_override"] = True
                            used_intent_gl.add(gl_code)
                            logger.info(f"Intent override → {gl_code} ({gl_info['glname1']}) on {l_type} line")
                            break

                # ★ 만기일(duedt) — GL 검증 완료 후 계정코드 우선, gltype 순으로 결정
                # 215000(선수금)은 gltype="C"이지만 전기일 폴백 로직이 필요하므로 먼저 처리.
                _fgt = line.get("gltype", "")
                _is_adv_gl = (line.get("glmaster", "") == "215000")
                if _is_adv_gl:
                    # 선수금(215000): 1순위=AI 추출, 2순위=헤더 due_date, 3순위=전기일자
                    # gltype이 "C"로 바뀌어도 이 분기가 먼저 실행되어 전기일 폴백을 보장한다.
                    _adv_due = line.get("due_date", "") or due_date or result_json.get("date", "")
                    line["duedt"]            = _adv_due if not self.is_empty_value(_adv_due) else result_json.get("date", "")
                    line["due_date_enabled"] = True
                    logger.info(f"[선수금] duedt 자동 설정: '{line['duedt']}' (AI='{line.get('due_date','')}' / 전기일='{result_json.get('date','')}')")
                elif _fgt in ("C", "S"):
                    line["duedt"]            = line.get("due_date", "") or due_date
                    line["due_date_enabled"] = True
                else:
                    line["duedt"]            = ""
                    line["due_date_enabled"] = False

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE 0-ADV: 선수금(계약금) 대변 계정 강제 고정 ──────────────────
            #
            # 조건: CI 전표 + 선수금 키워드 감지(flag_advance_payment)
            # 동작: ① 대변(C) 라인 중 EXP/REV/AR 타입 → glmaster=215000(선수금)으로 덮어씀
            #       ② biz_gl_locked=True 세팅 → Intent Override·PHASE 0-X 대상 제외
            #       ③ result_json["mulky"]="A" 세팅 → mainai.py 저장 시 4개 테이블 반영
            #       ④ effective_docty를 CI로 확정
            # ══════════════════════════════════════════════════════════════════
            if flag_advance_payment and effective_docty in ("CI", "GL", None, ""):
                # t_cglmst에서 215000 존재 여부 확인
                _adv_row = db.execute(
                    text("SELECT glmaster, glname1 FROM t_cglmst WHERE comcd=:c AND glmaster='215000' LIMIT 1"),
                    {"c": comcd},
                ).fetchone()

                if _adv_row:
                    _adv_applied = False
                    for _al in result_json.get("lines", []):
                        _al_type  = _al.get("type", "").upper()
                        _al_dc    = _al.get("debcre", "")
                        # 대변(C) 이면서 세금·AR 오픈아이템이 아닌 라인을 215000으로 교체
                        if _al_dc == "C" and _al_type not in ("TAX",) and not _adv_applied:
                            _al["glmaster"]      = "215000"
                            _al["glname"]        = _adv_row.glname1 or "선수금"
                            # gltype="C": 고객 오픈아이템 → mainai.py에서 C1/t_cbody_o로 라우팅
                            _al["gltype"]        = "C"
                            _al["biz_gl_locked"] = True        # 후속 Override·PHASE 0-X 차단
                            _al["source"]        = "규칙 반영"
                            _al["type"]          = "EXP"       # 안분 로직상 base 금액 사용
                            _adv_applied = True
                            logger.info(f"[선수금] 대변 라인 → 215000({_adv_row.glname1}), gltype=C 강제 고정")

                    if _adv_applied:
                        result_json["mulky"]           = "A"
                        result_json["advance_payment"] = True
                        result_json["doctyp"]          = "CI"
                        effective_docty                = "CI"
                        # SX(매출-세금무관): DB 존재 여부와 무관하게 UI 표시용으로 항상 세팅.
                        # taxnm은 DB에서 조회, 없으면 기본 레이블 사용.
                        _sx_meta = db.execute(
                            text("SELECT taxnm FROM t_ctxkey WHERE comcd=:c AND taxcd='SX' LIMIT 1"),
                            {"c": comcd},
                        ).fetchone()
                        result_json["taxcd"] = "SX"
                        result_json["taxnm"] = _sx_meta.taxnm if _sx_meta else "세금무관"
                        result_json["taxnm"] = result_json["taxnm"] or "세금무관"
                        logger.info(
                            f"[선수금] mulky='A', taxcd='SX', taxnm='{result_json['taxnm']}'"
                        )
                    else:
                        logger.warning("[선수금] 대변 라인을 찾지 못해 Override 건너뜀")
                else:
                    logger.warning("[선수금] t_cglmst에 215000 미등록 → 선수금 Override 건너뜀")

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE 0-ADV-SI: 선급금(계약금 지급) 차변 계정 강제 고정 ──────────
            #
            # 조건: SI/GL 전표 + 선급금 키워드 + AP방향 키워드(flag_advance_purchase)
            # 동작: ① 차변(D) 라인 중 TAX 제외 첫 번째 → glmaster=120000, gltype='S' 고정
            #         biz_gl_locked=True로 후속 Intent Override·PHASE 0-X 차단
            #       ② 대변(C) 라인 → biz_suppgl(거래처 기본 AP GL) 동적 매핑
            #          biz_suppgl 없으면 210000(외상매입금) 폴백
            #       ③ mulky='A', doctyp='SI', advance_purchase=True 확정
            #       ④ taxcd='PX00'(매입-세금무관) 초기 세팅 (DB 없으면 기본 레이블 사용)
            # ── 주의: effective_docty는 벡터 DB 오염 가능 → flag(Python 키워드 분석) 우선 ──
            # ══════════════════════════════════════════════════════════════════
            if flag_advance_purchase:
                _adv_pur_row = db.execute(
                    text("SELECT glmaster, glname1 FROM t_cglmst WHERE comcd=:c AND glmaster='120000' LIMIT 1"),
                    {"c": comcd},
                ).fetchone()

                if _adv_pur_row:
                    _adv_pur_applied = False
                    for _pl in result_json.get("lines", []):
                        _pl_type = _pl.get("type", "").upper()
                        _pl_dc   = _pl.get("debcre", "")
                        # 차변(D) 이면서 TAX가 아닌 첫 번째 라인을 120000으로 교체
                        if _pl_dc == "D" and _pl_type not in ("TAX",) and not _adv_pur_applied:
                            _pl["glmaster"]      = "120000"
                            _pl["glname"]        = _adv_pur_row.glname1 or "선급금"
                            # gltype='S': AP 보조원장(t_sbody_o) 라우팅 키
                            _pl["gltype"]        = "S"
                            _pl["biz_gl_locked"] = True   # 후속 Override·PHASE 0-X 차단
                            _pl["source"]        = "규칙 반영"
                            _pl["type"]          = "EXP"  # 안분 로직상 base 금액 사용
                            _adv_pur_applied = True
                            logger.info(
                                f"[선급금] 차변 라인 → 120000({_adv_pur_row.glname1}), gltype=S 강제 고정"
                            )

                    if _adv_pur_applied:
                        # 대변(C) 라인: biz_suppgl(거래처 기본 AP GL) 동적 매핑
                        if biz_suppgl:
                            _supp_gl = biz_suppgl
                            _supp_nm_row = db.execute(
                                text("SELECT glname1 FROM t_cglmst WHERE comcd=:c AND glmaster=:g LIMIT 1"),
                                {"c": comcd, "g": _supp_gl},
                            ).fetchone()
                            _supp_nm   = _supp_nm_row.glname1 if _supp_nm_row else "외상매입금"
                            _supp_src  = "biz_suppgl"
                        else:
                            # biz_suppgl 미등록 → 210000(외상매입금) 폴백
                            _supp_fb = db.execute(
                                text("SELECT glmaster, glname1 FROM t_cglmst WHERE comcd=:c AND glmaster='210000' LIMIT 1"),
                                {"c": comcd},
                            ).fetchone()
                            _supp_gl  = _supp_fb.glmaster if _supp_fb else ""
                            _supp_nm  = _supp_fb.glname1  if _supp_fb else "외상매입금"
                            _supp_src = "폴백(210000)"

                        if _supp_gl:
                            for _pl in result_json.get("lines", []):
                                if _pl.get("debcre") == "C" and _pl.get("type", "").upper() not in ("TAX",):
                                    _pl["glmaster"]      = _supp_gl
                                    _pl["glname"]        = _supp_nm
                                    _pl["gltype"]        = "S"
                                    _pl["biz_gl_locked"] = True
                                    _pl["source"]        = "규칙 반영"
                                    logger.info(
                                        f"[선급금] 대변 라인 → {_supp_gl}({_supp_nm}), {_supp_src} 동적 매핑"
                                    )
                                    break
                        else:
                            logger.warning("[선급금] biz_suppgl·210000 모두 미등록 → 대변 계정 빈값 처리")

                        result_json["mulky"]            = "A"
                        result_json["advance_purchase"] = True
                        result_json["doctyp"]           = "SI"
                        effective_docty                 = "SI"

                        # PX00(매입-세금무관) 세팅: DB 존재 여부와 무관하게 UI 표시용으로 항상 세팅
                        _px_meta = db.execute(
                            text("SELECT taxnm FROM t_ctxkey WHERE comcd=:c AND taxcd='PX00' LIMIT 1"),
                            {"c": comcd},
                        ).fetchone()
                        result_json["taxcd"] = "PX00"
                        result_json["taxnm"] = (_px_meta.taxnm if _px_meta else None) or "세금무관"
                        logger.info(
                            f"[선급금] mulky='A', taxcd='PX00', taxnm='{result_json['taxnm']}'"
                        )
                    else:
                        logger.warning("[선급금] 차변 라인을 찾지 못해 Override 건너뜀")
                else:
                    logger.warning("[선급금] t_cglmst에 120000 미등록 → 선급금 Override 건너뜀")

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE 0-X: 명시 계정 파이썬 강제 보정 (LLM 2차 방어선) ──────────
            #
            # LLM이 XML CRITICAL_OVERRIDE를 무시하고 다른 계정을 반환했을 경우,
            # 파이썬 레벨에서 조건 없이 강제 덮어쓴다.
            #
            # 대상 라인 선정 기준:
            #   AP(매입/비용) 문맥 → 차변(D) 라인 중 비용/자산(EXP/ASSET) 타입 우선,
            #                         없으면 TAX·AP·AR 제외한 첫 번째 D 라인
            #   AR(매출) 문맥     → 대변(C) 라인 중 REV 타입 우선,
            #                         없으면 AR·TAX 제외한 첫 번째 C 라인
            #
            # is_opposite_entry가 True면 D/C가 이미 Swap된 상태이므로 방향을 반전하여 적용.
            # ══════════════════════════════════════════════════════════════════
            if explicit_gl_match:
                # ── pattern_id 우선 보호 ─────────────────────────────────────────
                # 벡터 DB(t_v_user_learn) 패턴이 매칭된 경우 문장 전체 맥락으로
                # 이미 올바른 계정(예: 상품매출 410000)이 확정된 것이므로,
                # 단순히 문장 속 단어("상품")에 반응하는 명시 계정 보정을 차단한다.
                # 예) "상품 판매" → explicit_gl_match=상품(130010) 이지만
                #     pattern_id로 상품매출(410000)이 확정된 경우 130010으로 덮어쓰면 안 됨.
                if pattern_id:
                    logger.info(
                        f"[PHASE 0-X] pattern_id={pattern_id} 벡터 DB 패턴 우선 → "
                        f"명시 계정 보정 건너뜀 "
                        f"(explicit_gl_match={explicit_gl_match.get('glmaster','')} "
                        f"'{explicit_gl_match.get('glname1','')}' 차단)"
                    )
                else:
                    _xcode  = explicit_gl_match["glmaster"]
                    _xname  = explicit_gl_match["glname1"]
                    _xtype  = explicit_gl_match.get("gltype", "")
                    _xlines = result_json.get("lines", [])
                    # PHASE 0-R 이전이므로 result_json["is_opposite_entry"]은 아직 미설정.
                    # 사전 감지된 is_opposite_entry 변수를 직접 참조.
                    _is_opposite_entry_now = is_opposite_entry

                    # 현재 문맥 재판별 (post-processing 시점)
                    _post_ap = effective_docty in ("SI", "GL") or any(
                        kw in raw_text for kw in ['구매', '매입', '지급', '발주', '구입', '비용', '결제']
                    )
                    _post_ar = effective_docty == "CI" or any(
                        kw in raw_text for kw in ['매출', '판매', '납품', '청구', '수금']
                    )

                    # 반대분개(Opposite Entry)면 D/C 이미 Swap됨 → 대상 방향도 반전
                    _target_side = "D"  # AP: 차변 라인을 교정
                    if _post_ar and not _post_ap:
                        _target_side = "C"  # AR: 대변 라인을 교정
                    if _is_opposite_entry_now:
                        _target_side = "C" if _target_side == "D" else "D"

                    # 교정 제외 type 목록 (AP/AR 오픈아이템, 부가세 라인은 건드리지 않음)
                    _SKIP_TYPES = {"AP", "AR", "TAX"}

                    _override_target = None
                    # 1순위: EXP, REV, COG 타입 라인 (biz_gl_locked 성역 제외)
                    for _l in _xlines:
                        if _l.get("debcre") != _target_side:
                            continue
                        if _l.get("biz_gl_locked"):           # ← 선수금/선급금/거래처 마스터 계정 보호
                            continue
                        if _l.get("type", "").upper() in {"EXP", "REV", "COG"}:
                            _override_target = _l
                            break
                    # 2순위: SKIP_TYPES가 아닌 첫 번째 라인 (biz_gl_locked 성역 제외)
                    if not _override_target:
                        for _l in _xlines:
                            if _l.get("debcre") != _target_side:
                                continue
                            if _l.get("biz_gl_locked"):       # ← 선수금/선급금/거래처 마스터 계정 보호
                                continue
                            if _l.get("type", "").upper() not in _SKIP_TYPES:
                                _override_target = _l
                                break

                    if _override_target:
                        # ── 최종 안전망: biz_gl_locked 라인은 어떤 경로로도 변경 불가 ──
                        if _override_target.get("biz_gl_locked"):
                            logger.info(
                                f"[PHASE 0-X] biz_gl_locked=True → 계정 보정 차단 "
                                f"(protected glmaster={_override_target.get('glmaster','')})"
                            )
                        else:
                            _old_code = _override_target.get("glmaster", "")
                            _old_name = _override_target.get("glname", "")
                            if _old_code != _xcode:
                                _override_target["glmaster"] = _xcode
                                _override_target["glname"]   = _xname
                                if _xtype:
                                    _override_target["gltype"] = _xtype
                                _override_target["source"]   = "규칙 반영"
                                logger.info(
                                    f"[PHASE 0-X] 명시 계정 강제 보정: "
                                    f"{_old_code}({_old_name}) → {_xcode}({_xname}) "
                                    f"[debcre={_target_side}, type={_override_target.get('type','')}]"
                                )
                            else:
                                logger.info(
                                    f"[PHASE 0-X] LLM이 이미 올바른 계정({_xcode}) 사용 → 보정 불필요"
                                )
                    else:
                        logger.warning(
                            f"[PHASE 0-X] 교정 대상 라인을 찾지 못함 "
                            f"(target_side={_target_side}, lines={[l.get('debcre') for l in _xlines]})"
                        )

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE 0: 사비 대납(Out-of-Pocket) 강제 Override ───────────────
            #
            # AI가 어떤 계정을 대변에 배치했든 무조건 미지급금(211000)으로 덮어쓴다.
            # 이 로직은 프롬프트에 의존하지 않고 Python 객체를 직접 조작한다.
            #
            # 처리 순서:
            # 1) doctyp → 'SI' 강제
            # 2) 기존 대변(C) 라인 전체 제거
            # 3) 대변 라인을 미지급금(211000) 단일 라인으로 재구성
            #    (bizamt = 기존 대변 합계 또는 차변 합계 중 큰 값)
            # 4) 거래처 없으면 bizptcd_required 플래그 세팅
            # ══════════════════════════════════════════════════════════════════
            if flag_out_of_pocket:
                result_json["doctyp"] = "SI"

                all_lines   = result_json.get("lines", [])
                debit_lines = [l for l in all_lines if l.get("debcre") == "D"]
                credit_lines= [l for l in all_lines if l.get("debcre") == "C"]

                # 미지급금 라인에 넣을 금액: 기존 대변 합계 → 없으면 차변 합계
                _c_amt = sum(float(l.get("bizamt") or 0) for l in credit_lines)
                _d_amt = sum(float(l.get("bizamt") or 0) for l in debit_lines)
                _oop_amt = round(_c_amt if _c_amt > 0 else _d_amt, 0)

                # 새 미지급금 대변 라인 생성
                _mj_line = {
                    "debcre":       "C",
                    "glmaster":     "211000",
                    "glname":       "미지급금",
                    "gltype":       "S",
                    "type":         "AP",
                    "bizamt":       _oop_amt,
                    "biztax":       0,
                    "text":         "사비 대납 미지급금",
                    "source":       "규칙 반영",
                    "biz_gl_locked": True,   # 사비 대납 강제 생성 라인 → 후속 보정 차단
                    "pctrcd":       result_json.get("pctrcd", ""),
                    "anakey":       result_json.get("manaky", ""),
                    "duedt":        "",
                }

                # 기존 라인에서 대변 라인을 모두 제거하고 새 미지급금 라인으로 교체
                result_json["lines"] = debit_lines + [_mj_line]

                logger.info(
                    f"[사비대납 Override] 대변 라인 {len(credit_lines)}개 제거 → "
                    f"미지급금(211000) 단일 대변 라인 생성 (amt={_oop_amt:,.0f}) | "
                    f"원본 C 계정: {[l.get('glmaster','?') for l in credit_lines]}"
                )

                # 거래처(사원 벤더) 미매핑이면 REQUIRED_RED 처리 예약
                if not result_json.get("bizptcd"):
                    result_json["bizptcd_required"] = True
                    result_json["needs_review"]     = True

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE 0-R: 역분개(Reversal) D/C Swap ─────────────────────────
            #
            # "매출감소/취소/반품" 등의 역분개 문맥이 감지된 경우:
            # AI가 정방향(매출발생)으로 분개한 결과물의 차대변을 일괄 뒤집는다.
            # 예) 정방향: D:외상매출금 / C:상품매출 / C:매출부가세예수금
            #    역분개:  C:외상매출금 / D:상품매출 / D:매출부가세예수금
            #
            # 처리 규칙:
            # - doctyp은 그대로 유지 (CI는 CI, SI는 SI)
            # - 금액 부호는 양수 유지 (마이너스 분개 방식 불사용)
            # - 역분개 키워드가 감지된 경우 사비 대납(OOP) 여부와 무관하게 반드시 실행.
            #   Vector DB 이력이 정방향 패턴을 참조했더라도 현재 문맥이 역분개이면 Swap 우선.
            # - result_json["is_opposite_entry"] = True 로 프론트엔드 배지 표시.
            #
            # ── 이중 스왑(Double Swap) 방어 ──────────────────────────────────
            # AI가 이미 영리하게 역분개를 수행한 경우 파이썬 Swap을 생략한다.
            # 판단 기준 (앵커 라인으로 방향 확인):
            #  · CI(매출) : AR 라인(type='AR' 또는 gltype='C')이 이미 C(대변)이면 AI가 처리 완료
            #  · SI(매입) : AP 라인(type='AP' 또는 gltype='S')이 이미 D(차변)이면 AI가 처리 완료
            #  · 앵커 라인 없음(GL 등) : 무조건 Swap 실행
            # ══════════════════════════════════════════════════════════════════
            if is_opposite_entry:
                _doctyp = result_json.get("doctyp", "")
                _lines  = result_json.get("lines", [])

                # 앵커 라인으로 AI 역분개 선행 여부 확인
                _already_reversed = False
                if _doctyp == "CI":
                    # 정방향 CI: AR 라인이 D(차변) → 역분개 시 C(대변)으로 바뀌어야 함
                    for _l in _lines:
                        if _l.get("type") == "AR" or _l.get("gltype") == "C":
                            _already_reversed = (_l.get("debcre") == "C")
                            logger.info(
                                f"[역분개 방어] CI 앵커(AR/gltype=C) 라인 debcre='{_l.get('debcre')}' "
                                f"→ already_reversed={_already_reversed}"
                            )
                            break
                elif _doctyp == "SI":
                    # 정방향 SI: AP 라인이 C(대변) → 역분개 시 D(차변)으로 바뀌어야 함
                    for _l in _lines:
                        if _l.get("type") == "AP" or _l.get("gltype") == "S":
                            _already_reversed = (_l.get("debcre") == "D")
                            logger.info(
                                f"[역분개 방어] SI 앵커(AP/gltype=S) 라인 debcre='{_l.get('debcre')}' "
                                f"→ already_reversed={_already_reversed}"
                            )
                            break

                if _already_reversed:
                    # AI가 이미 역분개 수행 → 파이썬 Swap 생략, 플래그만 세팅
                    result_json["is_opposite_entry"] = True
                    logger.info(
                        f"[반대분개(Opposite Entry) Swap 생략] AI 선행 처리 감지 — 이중 스왑 방어 작동 "
                        f"(doctyp={_doctyp})"
                    )
                else:
                    # 파이썬이 강제 Swap 실행
                    _swap_count = 0
                    for line in _lines:
                        if line.get("debcre") == "D":
                            line["debcre"] = "C"
                            _swap_count += 1
                        elif line.get("debcre") == "C":
                            line["debcre"] = "D"
                            _swap_count += 1
                    result_json["is_opposite_entry"] = True   # 프론트 배지 표시용 플래그
                    logger.info(
                        f"[반대분개(Opposite Entry) Swap 실행] {_swap_count}개 라인 D/C 교체 완료 | "
                        f"doctyp={_doctyp} (유지)"
                    )

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE A: gltype C/S 기반 doctyp 강제 전환 ──────────────────────
            # AP 오픈아이템(gltype='S') 라인 → doctyp 'SI'
            # AR 오픈아이템(gltype='C') 라인 → doctyp 'CI'
            # gltype='A'(자산)는 향후 확장용 — 현재 전환 없음 (TODO)
            # ══════════════════════════════════════════════════════════════════
            _has_c_line = any(l.get("gltype") == "C" for l in result_json.get("lines", []))
            _has_s_line = any(l.get("gltype") == "S" for l in result_json.get("lines", []))
            _cur_doctyp = result_json.get("doctyp", effective_docty)

            # 즉시 지불 문맥(이자·수수료·공과금 + 지급동사)이면 gltype=S여도 GL 유지
            # 이자 지급·공과금 결제는 외상 매입(AP 오픈아이템)이 아니므로 SI 전환 불필요
            _gl_override_ctx = (
                any(kw in raw_text for kw in self._GL_OVERRIDE_KW)
                and any(kw in raw_text for kw in self._INSTANT_PAY_KW)
            )
            if _has_s_line and _cur_doctyp == "GL":
                if _gl_override_ctx:
                    logger.info(
                        f"[doctyp 강제전환 억제] gltype=S 존재하나 즉시지불 문맥 → GL 유지"
                    )
                else:
                    result_json["doctyp"] = "SI"
                    logger.info(f"[doctyp 강제전환] GL → SI (gltype=S 오픈아이템 라인 포함)")
            elif _has_c_line and _cur_doctyp == "GL":
                result_json["doctyp"] = "CI"
                logger.info(f"[doctyp 강제전환] GL → CI (gltype=C 오픈아이템 라인 포함)")
            # gltype='A' 자산 계정 → 향후 확장 예정:
            # TODO: 고정자산 취득/처분 전표일 경우 별도 doctyp 분기 로직 추가

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE B: bizptcd 없고 C/S 계정이면 경고 플래그 세팅 ──────────
            # ══════════════════════════════════════════════════════════════════
            if not result_json.get("bizptcd"):
                for line in result_json.get("lines", []):
                    orig_gltype = line.get("gltype")
                    if orig_gltype in ('C', 'S'):
                        old_code = line.get("glmaster", "")
                        line["needs_review"] = True
                        result_json["needs_review"]     = True
                        result_json["bizptcd_required"] = True
                        result_json["bizptcd"] = ""
                        result_json["bizname"] = ""
                        logger.warning(
                            f"[gltype 경고] bizptcd 없음, gltype='{orig_gltype}' 오픈아이템 계정({old_code}) — "
                            "거래처 선택 필요 (계정 치환 없음)"
                        )

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE C: header_field_control 생성 ─────────────────────────
            # 결제 수단·전표 유형에 따라 헤더 필드 제어 객체를 정밀 구성.
            # 우선순위: ① 법인카드  ② 사비 대납  ③ SI/CI 공통  ④ GL/기타
            #
            # ★ 실행 순서 보장:
            #   PHASE A(GL→CI/SI 강제 전환)와 PHASE 0-ADV(선수금 CI 세팅)는
            #   반드시 이 블록 이전에 완료된다.
            #   따라서 아래 _final_doctyp은 모든 자동 전환이 반영된 최종 전표 유형이다.
            #   GL이 CI로 전환된 경우에도 tax_invoice_date WARNING_YELLOW가 올바르게 적용된다.
            # ══════════════════════════════════════════════════════════════════
            _hfc: dict = {}
            _final_doctyp = result_json.get("doctyp", "GL")
            logger.info(f"[PHASE C] 진입 — 최종 doctyp='{_final_doctyp}' (PHASE A/0-ADV 전환 반영 완료)")

            # ── ① 법인카드 확정 ────────────────────────────────────────────
            if flag_corp_card:
                _hfc["tax_invoice_date"] = {
                    "status": "DISABLED",
                    "msg":    "법인카드는 세금계산서 발행일이 없습니다.",
                }
                _hfc["taxcode"] = {
                    "status": "USER_SELECT_REQUIRED",
                    "msg":    "법인카드 공제/불공제 여부를 선택해주세요.",
                }
                _hfc["bizptcd"] = {
                    "status": "REQUIRED_RED",
                    "msg":    "법인카드 가맹점(거래처)을 직접 선택해주세요.",
                }
                result_json["bizptcd"] = ""
                result_json["bizname"] = ""

            # ── ② 사비 대납 (내 현금/사비) ───────────────────────────────
            elif flag_personal_exp:
                # 사원 개인 자금 대납 → 세금계산서 비대상, 사원 거래처 직접 선택
                _hfc["tax_invoice_date"] = {
                    "status": "DISABLED",
                    "msg":    "사비 대납 건은 세금계산서 발행일이 없습니다.",
                }
                _hfc["taxcode"] = {
                    "status": "USER_SELECT_REQUIRED",
                    "msg":    "사비 대납 세금코드(매입세금무관 등)를 선택해주세요.",
                }
                if not result_json.get("bizptcd"):
                    _hfc["bizptcd"] = {
                        "status": "REQUIRED_RED",
                        "msg":    "사비 대납 사원을 거래처로 선택해주세요.",
                    }
                else:
                    _biz_st = result_json.get("biz_match_status", "EXACT")
                    _hfc["bizptcd"] = {
                        "status": "WARNING_YELLOW" if _biz_st == "FUZZY" else "EDITABLE",
                        "msg": f"'{result_json.get('bizname','')}' 거래처로 자동 검색되었습니다. 맞는지 확인해 주세요." if _biz_st == "FUZZY" else "",
                    }

            # ── ③-A CI (매출전표) ─────────────────────────────────────────
            elif _final_doctyp == "CI":
                _is_advance = result_json.get("advance_payment", False)

                if _is_advance:
                    # 선수금(계약금): 아직 재화/용역 미제공 → 세금계산서 발행 시점 아님
                    _hfc["tax_invoice_date"] = {
                        "status": "DISABLED",
                        "msg":    "선수금(계약금) 수령 시점은 세금계산서 발행 대상이 아닙니다.",
                    }
                    result_json["tax_invoice_date"] = ""
                    # SX(매출-세금무관): DB 유무와 무관하게 항상 EDITABLE로 표시.
                    # taxcd/taxnm은 PHASE 0-ADV에서 이미 세팅됨. 여기서는 field control만 반환.
                    result_json.setdefault("taxcd", "SX")
                    result_json.setdefault("taxnm", "세금무관")
                    _hfc["taxcode"] = {
                        "status": "EDITABLE",
                        "msg":    "선수금은 세금무관(SX) 코드가 자동 설정되었습니다. 필요시 변경하세요.",
                    }
                    logger.info("[PHASE C][선수금] tax_invoice_date DISABLED, taxcode=SX EDITABLE 확정")
                else:
                    # 일반 매출전표: 세금계산서 발행일 — 비어있으면 Yellow Warning
                    _tid_val = result_json.get("tax_invoice_date", "")
                    if self.is_empty_value(_tid_val):
                        _hfc["tax_invoice_date"] = {
                            "status": "WARNING_YELLOW",
                            "msg":    "세금계산서 발행일이 누락되었습니다. 건별 발행이면 날짜를 입력하시고, 합계 발행 예정이면 비워두셔도 됩니다.",
                        }
                    else:
                        _hfc["tax_invoice_date"] = {"status": "EDITABLE", "msg": ""}
                    # 세금코드: AI/매핑 결과에 유효한 값이 있으면 EDITABLE로 유지.
                    # LLM의 가짜 빈 값(" ", "-", "null" 등) 포함, 실질적으로 비어있으면 선택 필수.
                    _existing_taxcd = result_json.get("taxcd", "")
                    if not self.is_empty_value(_existing_taxcd):
                        _hfc["taxcode"] = {"status": "EDITABLE", "msg": ""}
                    else:
                        if _existing_taxcd != "":
                            result_json["taxcd"] = ""
                        _hfc["taxcode"] = {
                            "status": "USER_SELECT_REQUIRED",
                            "msg":    "매출 전표는 세금코드를 반드시 선택해주세요.",
                        }
                # 거래처: 매칭 상태에 따라 분기
                _biz_status = result_json.get("biz_match_status", "NONE")
                if _biz_status == "NONE" or not result_json.get("bizptcd"):
                    _hfc["bizptcd"] = {
                        "status": "REQUIRED_RED",
                        "msg":    "등록된 거래처 정보를 찾을 수 없습니다. 새로운 거래처를 검색하거나 선택해 주세요.",
                    }
                elif _biz_status == "FUZZY":
                    _hfc["bizptcd"] = {
                        "status": "WARNING_YELLOW",
                        "msg":    f"'{result_json.get('bizname','')}' 거래처가 검색되었습니다. 정확한 거래처인지 확인해 주세요.",
                    }
                else:
                    _hfc["bizptcd"] = {"status": "EDITABLE", "msg": ""}

            # ── ③-B SI (매입전표) ─────────────────────────────────────────
            elif _final_doctyp == "SI":
                _is_adv_pur = result_json.get("advance_purchase", False)

                # 매입전표: 세금계산서 발행일은 입력 대상이 아님 → 항상 DISABLED
                _hfc["tax_invoice_date"] = {
                    "status": "DISABLED",
                    "msg":    "매입전표(SI)는 세금계산서 발행일 입력 대상이 아닙니다.",
                }
                result_json["tax_invoice_date"] = ""

                if _is_adv_pur:
                    # 선급금(계약금 지급): 재화/용역 미수령 시점 → 세금계산서 수취 대상 아님
                    result_json.setdefault("taxcd", "PX00")
                    result_json.setdefault("taxnm", "세금무관")
                    _hfc["taxcode"] = {
                        "status": "EDITABLE",
                        "msg":    "선급금은 세금무관(PX00) 코드가 자동 설정되었습니다. 필요시 변경하세요.",
                    }
                    logger.info("[PHASE C][선급금] tax_invoice_date DISABLED, taxcode=PX00 EDITABLE 확정")
                else:
                    # 일반 매입전표: 세금코드 필수 선택
                    _existing_taxcd = result_json.get("taxcd", "")
                    if not self.is_empty_value(_existing_taxcd):
                        _hfc["taxcode"] = {"status": "EDITABLE", "msg": ""}
                    else:
                        if _existing_taxcd != "":
                            result_json["taxcd"] = ""
                        _hfc["taxcode"] = {
                            "status": "USER_SELECT_REQUIRED",
                            "msg":    "매입 전표는 세금코드를 반드시 선택해주세요.",
                        }

                # 거래처
                _biz_status = result_json.get("biz_match_status", "NONE")
                if _biz_status == "NONE" or not result_json.get("bizptcd"):
                    _hfc["bizptcd"] = {
                        "status": "REQUIRED_RED",
                        "msg":    "등록된 거래처 정보를 찾을 수 없습니다. 새로운 거래처를 검색하거나 선택해 주세요.",
                    }
                elif _biz_status == "FUZZY":
                    _hfc["bizptcd"] = {
                        "status": "WARNING_YELLOW",
                        "msg":    f"'{result_json.get('bizname','')}' 거래처가 검색되었습니다. 정확한 거래처인지 확인해 주세요.",
                    }
                else:
                    _hfc["bizptcd"] = {"status": "EDITABLE", "msg": ""}

            # ── ④ GL 및 기타 ────────────────────────────────────────────────
            else:
                _hfc["tax_invoice_date"] = {"status": "EDITABLE", "msg": ""}
                _hfc["taxcode"]          = {"status": "EDITABLE", "msg": ""}
                _biz_status = result_json.get("biz_match_status", "NONE")
                if _biz_status == "NONE" or not result_json.get("bizptcd"):
                    _hfc["bizptcd"] = {
                        "status": "REQUIRED_RED",
                        "msg":    "등록된 거래처 정보를 찾을 수 없습니다. 새로운 거래처를 검색하거나 선택해 주세요.",
                    }
                elif _biz_status == "FUZZY":
                    _hfc["bizptcd"] = {
                        "status": "WARNING_YELLOW",
                        "msg":    f"'{result_json.get('bizname','')}' 거래처가 검색되었습니다. 정확한 거래처인지 확인해 주세요.",
                    }
                else:
                    _hfc["bizptcd"] = {"status": "EDITABLE", "msg": ""}

            # ── mulky(멀티키) 제어: 선수금/선급금('A') 전표는 수정 불가 ─────────
            if result_json.get("mulky") == "A":
                _hfc["mulky"] = {
                    "status": "DISABLED",
                    "msg":    "선수금/선급금 전표는 멀티키를 변경할 수 없습니다.",
                }
            else:
                _hfc["mulky"] = {"status": "EDITABLE", "msg": ""}

            result_json["header_field_control"] = _hfc

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE D: 라인별 line_field_control 생성 (due_date 제어 통합) ──
            # 이전에 분산되어 있던 duedt/due_date_enabled/field_control 블록을
            # 여기 하나로 통합. gltype C/S만 REQUIRED_RED, 나머지 DISABLED.
            # ══════════════════════════════════════════════════════════════════
            for line in result_json.get("lines", []):
                _gt  = line.get("gltype", "")
                _lfc: dict = {}

                # ── due_date 제어 ─────────────────────────────────────────────
                # 우선순위: 라인의 duedt > 라인의 due_date > 루트 due_date > 빈값
                # is_empty_value()로 LLM의 가짜 빈 값(" ", "-", "null" 등)까지 걸러낸다.
                # 실질적으로 비어있다고 판정된 경우 실제 필드값도 "" 로 클렌징한다.
                _raw_duedt = (
                    line.get("duedt")
                    or line.get("due_date")
                    or result_json.get("due_date", "")
                )
                _has_valid_duedt = not self.is_empty_value(_raw_duedt)

                # ★ 215000(선수금)·120000(선급금) 체크를 gltype C/S보다 먼저 실행.
                # gltype이 이미 C/S로 바뀌었어도 계정별 전용 WARNING_YELLOW가 올바르게 적용된다.
                _is_adv_line     = (line.get("glmaster", "") == "215000")
                _is_adv_pur_line = (line.get("glmaster", "") == "120000")

                if _is_adv_line:
                    # ── 선수금(215000) 전용: 계약 이행 예정일 ────────────────────
                    # 라인 루프(★)에서 이미 duedt를 채웠으므로 클렌징 후 WARNING_YELLOW 반환.
                    line["due_date_enabled"] = True
                    _adv_duedt = line.get("duedt", "")
                    if self.is_empty_value(_adv_duedt):
                        _adv_duedt = result_json.get("date", "")
                        line["duedt"] = _adv_duedt
                    _lfc["due_date"] = {
                        "status": "WARNING_YELLOW",
                        "msg":    "계약 이행 예정일(만기일)을 확인해 주세요. 별도 언급이 없어 전기일자로 자동 설정되었습니다.",
                    }
                elif _is_adv_pur_line:
                    # ── 선급금(120000) 전용: 대금 지급 예정일 ────────────────────
                    line["due_date_enabled"] = True
                    _adv_pur_duedt = line.get("duedt", "") or result_json.get("date", "")
                    if self.is_empty_value(_adv_pur_duedt):
                        _adv_pur_duedt = result_json.get("date", "")
                    line["duedt"] = _adv_pur_duedt
                    _lfc["due_date"] = {
                        "status": "WARNING_YELLOW",
                        "msg":    "대금 지급 예정일(만기일)을 확인해 주세요. 별도 언급이 없어 전기일자로 자동 설정되었습니다.",
                    }
                elif _gt in ("C", "S"):
                    line["due_date_enabled"] = True
                    if _has_valid_duedt:
                        line["duedt"] = _raw_duedt.strip()
                        _lfc["due_date"] = {
                            "status": "EDITABLE",
                            "msg":    "채권/채무 관리가 필요한 오픈아이템 계정입니다. 만기 일자를 확인해 주세요.",
                        }
                    else:
                        line["duedt"] = ""
                        line.pop("due_date", None)
                        _lfc["due_date"] = {
                            "status": "REQUIRED_RED",
                            "msg":    "채권/채무 관리가 필요한 '오픈아이템' 계정입니다. 정확한 만기 일자를 확인해 주세요.",
                        }
                elif _gt == "A":
                    line["duedt"]            = ""
                    line["due_date_enabled"] = False
                    _lfc["due_date"] = {"status": "DISABLED", "msg": ""}
                else:
                    line["duedt"]            = ""
                    line["due_date_enabled"] = False
                    _lfc["due_date"] = {"status": "DISABLED", "msg": ""}

                # ── account_code 제어 ─────────────────────────────────────────
                # ① AR/AP 오픈아이템(C/S): 계정코드 임의 변경 금지 (서브레저 무결성 보호)
                # ② 선수금(biz_gl_locked): 215000으로 강제 고정된 라인은 편집 금지
                # ③ 일반 계정: 자유 편집 가능
                if _gt in ("C", "S"):
                    _lfc["account_code"] = {
                        "status": "DISABLED",
                        "msg":    "채권/채무 오픈아이템 계정은 서브레저 무결성 보호를 위해 변경할 수 없습니다.",
                    }
                elif line.get("biz_gl_locked"):
                    _lfc["account_code"] = {
                        "status": "DISABLED",
                        "msg":    "선수금/선급금(계약금) 계정은 재화/용역 인도 전까지 변경할 수 없습니다.",
                    }
                else:
                    _lfc["account_code"] = {"status": "EDITABLE", "msg": ""}

                line["line_field_control"] = _lfc

            # ══════════════════════════════════════════════════════════════════
            # ── PHASE E: 불공제('D') 분개 라인 재구성 ──────────────────────────
            #
            # txgubun='D'(매입불공제): 세액은 공제 불가 → 비용으로 전액 처리.
            # - t_ctax에는 공급가·세액을 분리 기록 (supply_base / supply_vat 이용)
            # - 분개 라인에서는 TAX 라인을 제거하고 EXP 차변 라인에 세액을 합산.
            # - 결과: D(비용 = 공급가 + 세액), C(AP = 총액)  → 단일 비용 라인.
            # ══════════════════════════════════════════════════════════════════
            if txgubun == 'D' and vat > 0:
                _tax_lines    = [l for l in result_json["lines"] if l.get("type") == "TAX"]
                _nontax_lines = [l for l in result_json["lines"] if l.get("type") != "TAX"]
                _tax_total    = sum(float(l.get("bizamt", 0)) for l in _tax_lines)
                if _tax_total > 0:
                    # 차변 EXP 첫 라인에 세액 합산 (공급가 + 불공제 VAT = 전액 비용화)
                    for _l in _nontax_lines:
                        if _l.get("debcre") == "D" and _l.get("type") == "EXP":
                            _before = float(_l.get("bizamt", 0))
                            _l["bizamt"] = round(_before + _tax_total, 0)
                            _l["biztax"] = 0        # 불공제: 라인 세액 0
                            _l["source"] = "규칙 반영"
                            logger.info(
                                f"[불공제(D) PHASE E] EXP 차변 합산: "
                                f"{_before:,.0f} + {_tax_total:,.0f} = {_l['bizamt']:,.0f}"
                            )
                            break
                    result_json["lines"] = _nontax_lines
                    logger.info(
                        f"[불공제(D) PHASE E] TAX 라인 {len(_tax_lines)}개 제거 완료 "
                        f"| supply_base={base:,.0f} supply_vat={vat:,.0f} (t_ctax 분리 저장 예약)"
                    )

            # ── 차대 불일치 감지 및 자동 보정 ───────────────────────────────
            lines_out = result_json.get("lines", [])
            d_sum = sum(l.get("bizamt", 0) for l in lines_out if l.get("debcre") == "D")
            c_sum = sum(l.get("bizamt", 0) for l in lines_out if l.get("debcre") == "C")
            diff  = abs(d_sum - c_sum)
            if diff > 0:   # 오차 없이 무조건 균형 보정 (반올림 1원 포함)
                logger.warning(
                    f"[차대불일치] 차변={d_sum:,.0f} / 대변={c_sum:,.0f} / 차이={diff:,.0f} "
                    f"| tot={tot:,.0f} base={base:,.0f} vat={vat:,.0f}"
                )
                # ── 자동 보정: 부족한 쪽의 최대 금액 라인에 차액 추가 ──────
                # (단, 미세 반올림 오차가 아닌 의미 있는 불일치만 보정)
                if d_sum > c_sum:
                    _target = max(
                        (l for l in lines_out if l.get("debcre") == "C"),
                        key=lambda l: l.get("bizamt", 0), default=None
                    )
                else:
                    _target = max(
                        (l for l in lines_out if l.get("debcre") == "D"),
                        key=lambda l: l.get("bizamt", 0), default=None
                    )
                if _target:
                    _old = _target.get("bizamt", 0)
                    _target["bizamt"] = _old + diff
                    result_json["balance_warning"] = (
                        f"금액 균형을 맞추기 위해 '{_target.get('glname','?')}' 항목의 금액이 "
                        f"{_old:,.0f}원에서 {_target['bizamt']:,.0f}원으로 자동 조정되었습니다. "
                        f"상세 내역을 검토해 주세요."
                    )
                    result_json["needs_review"] = True
                    logger.info(
                        f"[차대보정 OK] {_target.get('glname','?')}({_target.get('glmaster','')}) "
                        f"{_old:,.0f} → {_target['bizamt']:,.0f} (+{diff:,.0f})"
                    )
                else:
                    result_json["balance_warning"] = (
                        f"차변({d_sum:,.0f}원)과 대변({c_sum:,.0f}원)의 합계가 맞지 않습니다. "
                        "계정 과목과 금액을 다시 확인해 주세요."
                    )
                    result_json["needs_review"] = True
            else:
                logger.info(f"[차대균형 OK] 차변=대변={c_sum:,.0f}")

            # ── 전표 성립 요건 최종 검증 (특허 P26LX017: 증빙일·계정코드·금액 필수) ──
            val_errors = []
            if not result_json.get("date"):
                val_errors.append("증빙일이 입력되지 않았습니다.")
            for idx, l in enumerate(result_json.get("lines", []), 1):
                if not l.get("glmaster"):
                    val_errors.append(f"{idx}번 라인: 계정 과목이 확정되지 않았습니다.")
                if not (l.get("bizamt") or 0):
                    val_errors.append(f"{idx}번 라인: 금액이 입력되지 않았습니다.")
            if val_errors:
                result_json.setdefault("validation_warnings", []).extend(val_errors)
                result_json["needs_review"] = True
                logger.warning(f"[전표검증] 필수 필드 누락: {'; '.join(val_errors)}")
            else:
                logger.info("[전표검증 OK] 증빙일·계정코드·금액 이상 없음")

            logger.info(
                f"[PROF] JSON parse+post-process: {time.time()-_t_parse:.2f}s | "
                f"TOTAL: {time.time()-_t_start:.2f}s"
            )

            # ── STEP1: 결과 캐시 저장 (FIFO, 최대 _CACHE_MAX 건) ─────────────
            if len(self._result_cache) >= self._CACHE_MAX:
                oldest = self._cache_order.pop(0)
                self._result_cache.pop(oldest, None)
            self._result_cache[_cache_key] = result_json
            self._cache_order.append(_cache_key)

            return result_json
        except Exception as e:
            logger.error(f"Final Parse Error: {e} | Raw: {resp_text}")
            raise Exception(f"AI 분석 처리 실패: {str(e)}")