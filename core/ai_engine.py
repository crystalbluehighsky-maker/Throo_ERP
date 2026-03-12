# ai_engine.py
import os, json, voyageai, re, asyncio, csv, io, time, hashlib
from datetime import date
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging
from dotenv import load_dotenv, find_dotenv
from google import genai
from google.genai import types

# .env 로드 (파일 위치와 무관하게 상위 디렉터리까지 자동 탐색)
load_dotenv(find_dotenv())

logger = logging.getLogger("DaBom_AI_Engine")

class DabomHybridEngine:
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
■ 기타비용: 523000|소모품비  524000|업무추진비(접대비)  533000|운반비  542000|지급수수료  542200|임차료  560000|보험료  579000|잡비
■ 영업외  : 710000|이자수익  752000|이자비용  753000|잡손실"""

    # GL Master CSV 파일 경로 (전체 계정 참조용 — 미사용 시 fallback)
    _GL_MASTER_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "workingD_RR", "GL Master.csv"
    )

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
- 차변과 대변의 합계는 반드시 일치해야 하며, 모든 금액은 숫자 형태로 출력한다.
- 알 수 없는 계정코드는 절대 임의로 만들지 말고 glmaster를 ""(빈 문자열)로 출력하라.

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
    _SMALL_CASH_KW  = ['퀵', '퀵비', '택배', '택배비']             # 소액 운반 비용

    def _detect_doctype_hint(self, raw_text: str) -> str:
        """
        Python 규칙 기반 전표 유형 1차 추론 (AI 호출 전).

        판정 우선순위:
        1. 거래처 상호 탐지 + AR/AP 방향 키워드 조합 → CI / SI
        2. 거래처 미탐지여도 강한 행위 키워드(_STRONG_AR/AP_KW)만으로 판정 → CI / SI
           예: "LG전자에 납품하여 매출" — LG전자가 정규식에서 인식 안 될 때도 CI 보장
        3. 위 모두 해당 없음 → GL (일반전표)
        """
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

    async def generate_final_journal(self, db: Session, comcd: str, raw_text: str):
        _t_start = time.time()

        # ── STEP1: 캐시 히트 체크 (동일 입력 → 즉시 반환) ───────────────────
        _cache_key = hashlib.md5(f"{comcd}::{raw_text.strip()}".encode()).hexdigest()
        if _cache_key in self._result_cache:
            logger.info(f"[CACHE HIT] '{raw_text[:30]}...' → {time.time()-_t_start:.3f}s")
            return self._result_cache[_cache_key]

        # 0. Python 규칙 기반 전표 유형 사전 추론
        doctype_hint = self._detect_doctype_hint(raw_text)
        logger.info(f"Doctype hint: {doctype_hint} for: '{raw_text[:40]}'")

        # ── 특수 패턴 플래그 (AR수금폴백 / 소액현금지출) ─────────────────────
        # Vector DB 미매칭 시 패턴 가이드를 분기하고, 후처리 단계에서 라인을 직접 생성한다.
        flag_cash_receipt   = self._is_cash_receipt_only(raw_text)
        flag_small_cash_exp = self._is_small_cash_expense(raw_text)
        if flag_cash_receipt:   logger.info(f"[플래그] AR 수금 폴백 활성화: '{raw_text[:30]}'")
        if flag_small_cash_exp: logger.info(f"[플래그] 소액 현금 지출 활성화: '{raw_text[:30]}'")

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

            # ── 2순위 안전장치: DB가 GL이어도 AR/AP 키워드가 명확하면 CI/SI 강제 전환 ──
            if db_docty == 'GL' and doctype_hint in ('CI', 'SI'):
                effective_docty = doctype_hint
                logger.info(f"Doctype override: DB=GL → keyword_hint={doctype_hint} (safety net)")
            else:
                effective_docty = db_docty

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
                        "대변 glmaster: '현금1' 또는 '가수금' 계정 코드를 알면 채워라. "
                        "모르면 \"\"로 두면 백엔드가 t_cglmst에서 자동으로 채운다."
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

                    # 1순위: 회사 사용 계정 t_cglmst
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

                    # 2순위: 전체 표준 계정 t_nglmst
                    if not gl_hints:
                        for kw in keywords:
                            rows = db.execute(
                                text("SELECT glmaster, glname1, gltype FROM t_nglmst WHERE glname1 ILIKE :k LIMIT 3"),
                                {"k": f"%{kw}%"}
                            ).fetchall()
                            for row in rows:
                                if row[0] not in seen_gl:
                                    seen_gl.add(row[0])
                                    gl_hints.append({"src": "ngl", "glmaster": row[0], "glname1": row[1], "gltype": row[2]})
                            if len(gl_hints) >= 8:
                                break

                    if gl_hints:
                        src_label = "회사계정(t_cglmst)" if gl_hints[0]["src"] == "cgl" else "표준계정(t_nglmst)"
                        pattern_guide = (
                            f"### [계정과목 마스터 참조 후보({src_label}) - 아래 중 적절한 계정 사용]\n"
                            f"{json.dumps(gl_hints, ensure_ascii=False)}"
                        )
                        gl_fallback_matched = True
                        logger.info(f"GL fallback({src_label}) matched {len(gl_hints)} accounts")
                    else:
                        # ── STEP3: 완전 매칭 실패 → needs_review 플래그 + 잡비 안내 ──
                        pattern_guide = (
                            "### [계정 검색 완전 실패 — 기본 안내]\n"
                            "적절한 패턴 및 계정 후보를 찾지 못했습니다.\n"
                            "비용성 거래는 '잡비(579000)'를, 미결제 대변은 '미지급금(211000)'을 우선 사용하고 "
                            "glmaster 코드를 반드시 출력하라. 모르는 코드는 \"\"로 두어라."
                        )
                        gl_fallback_matched = False
                        needs_review = True
                        logger.warning(
                            f"[STEP3 Miss] Vector+Keyword 모두 실패 — needs_review=True 설정, "
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

        # 2. AI 지시문 구성
        if not self.system_prompt_template:
            raise Exception("System prompt template not loaded.")

        today_str = date.today().isoformat()   # 예: 2026-03-09
        prompt = (
            self.system_prompt_template
            .replace("{{TODAY_DATE}}", today_str)
            .replace("{{RAW_TEXT}}", raw_text)
            .replace("{{PATTERN_GUIDE}}", f"{hint_section}\n{pattern_guide}")
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
                        max_output_tokens=8192,
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
            result_json['source']       = "DB" if (pattern_id or gl_fallback_matched) else "AI"
            result_json['needs_review'] = needs_review   # STEP3: UI "수동 확인 필요" 플래그

            # ── 라인별 출처 태그 초기화 (프론트엔드 배지 결정용) ──────────────────────
            # "DB"       : Vector DB 패턴 매칭 (t_v_std_pattern / t_v_user_learn)
            # "FALLBACK" : t_cglmst 텍스트 검색(GL fallback) 또는 현금/규칙 기반 패턴 가이드
            # "AI"       : Vector DB 미매칭, t_cglmst 미탐지 → Gemini 순수 추론
            # AR수금폴백(시스템 규칙), Intent Override(intent_override=True)는
            # 이후 단계에서 각자 설정/플래그로 처리되므로 여기서는 기본값만 주입.
            # gl_fallback_matched = t_cglmst 텍스트 검색으로 실제 계정을 찾은 경우 → 'DB 참조'
            _line_src = "DB" if (pattern_id or gl_fallback_matched) else "AI"
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
            
            # 💡 파이썬 백엔드 수학적 금액 강제 계산 로직
            tot = float(result_json.get("total_amount", 0))
            vr  = float(result_json.get("vat_rate", 0))

            # ── total_amount 유효성 검증 ──────────────────────────────────────
            if tot <= 0:
                logger.error(f"total_amount가 0 또는 음수: {tot} | raw_text='{raw_text[:60]}'")
                raise Exception(f"AI가 금액을 추출하지 못했습니다 (total_amount={tot}). 문장을 다시 입력하세요.")

            # ── 부가세 안분 (부가세 포함 총액 기준) ──────────────────────────
            # 공식: base = round(tot / 1.1),  vat = tot - base
            # 예시: tot=330,000 → base=300,000, vat=30,000
            base = round(tot / 1.1) if vr > 0 else tot
            vat  = tot - base       if vr > 0 else 0

            logger.info(f"금액 안분: tot={tot:,.0f} / vr={vr}% → base={base:,.0f}, vat={vat:,.0f}")

            # 만기일 변수 확보
            due_date = result_json.get("due_date", "")
            
            # 3. 마스터 DB 정밀 매핑 (pg_trgm similarity 검색, 전처리 후 검색어 사용)
            # ── 거래처 (t_cbizpt) ─────────────────────────────────────────────
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

            dt = result_json.get("doctyp", "GL")
            txcd = "S010" if dt == "CI" and vr > 0 else "S170" if dt == "CI" and vr == 0 else "P010" if dt == "SI" and vr > 0 else "P110" if dt == "SI" and vr == 0 else ""
            result_json["taxcd"], result_json["taxnm"] = txcd, ""
            if txcd:
                row = db.execute(text("SELECT taxcd, taxnm FROM t_ctxkey WHERE comcd=:c AND taxcd=:t LIMIT 1"), {"c": comcd, "t": txcd}).fetchone()
                if row: result_json["taxcd"], result_json["taxnm"] = row[0], row[1]

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

            # 💡 라인별 금액 및 만기일 강제 주입
            used_intent_gl = set()  # Intent Override 중복 방지: 동일 계정을 두 라인에 동시 적용 금지
            for line in result_json.get("lines", []):
                l_type = line.get("type", "").upper()

                # ── 금액 분할 적용 (type 기준) ────────────────────────────────
                if l_type in ("AR", "AP"):
                    # 채권·채무 라인: 부가세 포함 총액
                    line["bizamt"], line["biztax"] = tot, 0
                    line["duedt"] = line.get("due_date", "") or due_date
                elif l_type in ("REV", "EXP"):
                    # 수익·비용 라인: 공급가액(부가세 제외)
                    line["bizamt"], line["biztax"] = base, 0
                    line["duedt"] = ""
                elif l_type == "TAX":
                    # 세금 라인: 부가세액
                    line["bizamt"], line["biztax"] = vat, vat
                    line["duedt"] = ""
                else:
                    # type 미인식 라인 — debcre 기반으로 안전 추론
                    # (패턴에 type 필드 없는 구형 데이터 대비 fallback)
                    debcre  = line.get("debcre", "")
                    gltype_ = line.get("gltype", "")
                    if debcre == "C" and gltype_ in ("C", "S"):
                        # 대변 부채 계정: AP로 처리
                        line["bizamt"], line["biztax"] = tot, 0
                        line["duedt"] = due_date
                        logger.warning(f"type 미인식 → AP fallback: glmaster={line.get('glmaster','')}")
                    elif debcre == "D" and gltype_ == "X":
                        # 차변 세금 계정 (gltype=X: 세금 관련)
                        line["bizamt"], line["biztax"] = vat if vr > 0 else tot, vat if vr > 0 else 0
                        line["duedt"] = ""
                        logger.warning(f"type 미인식 → TAX fallback: glmaster={line.get('glmaster','')}")
                    else:
                        # 그 외: 공급가액 할당 (비용 추정)
                        line["bizamt"], line["biztax"] = base, 0
                        line["duedt"] = ""
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
                    # 폴백 코드가 실제 DB에 있는지 한 번만 확인
                    fb_row = db.execute(
                        text("SELECT glname1, gltype FROM t_cglmst WHERE comcd=:c AND glmaster=:g LIMIT 1"),
                        {"c": comcd, "g": fb_code}
                    ).fetchone()
                    if fb_row:
                        line["glmaster"]     = fb_code
                        line["glname"]       = fb_row[0]
                        line["gltype"]       = fb_row[1]
                    else:
                        line["glmaster"]     = fb_code
                        line["glname"]       = fb_name
                    line["needs_review"] = True
                    line["source"]       = "규칙 반영"   # STEP4 강제 주입 → 규칙 반영
                    result_json["needs_review"] = True   # 헤더 레벨도 플래그
                    logger.warning(
                        f"[STEP4 폴백] type={l_type} debcre={line.get('debcre','')} "
                        f"→ {fb_code}({fb_name}) [needs_review][규칙 반영]"
                    )

                # ★ Intent Override: 사용자가 직접 언급한 계정명이 있을 때 AI 추론 계정보다 우선 적용
                # AR/AP(거래처 마스터), TAX 라인 및 이미 거래처 잠금된 라인은 제외
                if (intent_gl_map
                        and not line.get("biz_gl_locked")
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

            # ── gltype 정합성 검증: bizptcd 없는 전표에서 C/S 계정 강제 차단 ────────────
            # t_cglmst.gltype = 'C': 고객 오픈아이템(AR) → bizptcd 필수
            # t_cglmst.gltype = 'S': 공급업체 오픈아이템(AP) → bizptcd 필수
            # 거래처 없이 이 계정을 사용하면 오픈아이템 정산 불가 → 규칙상 교체 필요.
            if not result_json.get("bizptcd"):
                for line in result_json.get("lines", []):
                    orig_gltype = line.get("gltype")
                    if orig_gltype in ('C', 'S'):
                        old_code = line.get("glmaster", "")
                        debcre   = line.get("debcre", "")
                        # 대변 → null-gltype 가계정, 차변 → 잡비(579000)
                        if debcre == "C":
                            rep_code = _clr_code or "579000"
                            rep_name = _clr_name if _clr_code else "잡수익"
                        else:
                            rep_code, rep_name = "579000", "잡비"
                        rep_row = db.execute(
                            text("SELECT glname1, gltype FROM t_cglmst WHERE comcd=:c AND glmaster=:g LIMIT 1"),
                            {"c": comcd, "g": rep_code}
                        ).fetchone()
                        line["glmaster"]            = rep_code
                        line["glname"]              = rep_row[0] if rep_row else rep_name
                        line["gltype"]              = (rep_row[1] if rep_row else "") or ""
                        line["needs_review"]        = True
                        result_json["needs_review"] = True
                        result_json["bizptcd_required"] = True
                        logger.warning(
                            f"[gltype 차단] bizptcd 없음, gltype='{orig_gltype}' 계정({old_code})"
                            f" → {rep_code}({rep_name}) 대체"
                        )

            # ── 차대 불일치 감지 및 로깅 ─────────────────────────────────────
            lines_out = result_json.get("lines", [])
            d_sum = sum(l.get("bizamt", 0) for l in lines_out if l.get("debcre") == "D")
            c_sum = sum(l.get("bizamt", 0) for l in lines_out if l.get("debcre") == "C")
            diff  = abs(d_sum - c_sum)
            if diff > 1:   # 1원 오차는 반올림 허용
                logger.warning(
                    f"[차대불일치] 차변={d_sum:,.0f} / 대변={c_sum:,.0f} / 차이={diff:,.0f} "
                    f"| tot={tot:,.0f} base={base:,.0f} vat={vat:,.0f}"
                )
                # 소계 불일치 정보를 프론트로 전달 (UI 경고 표시용)
                result_json["balance_warning"] = (
                    f"차대 불일치 감지 (차변 {d_sum:,.0f} / 대변 {c_sum:,.0f}). "
                    "계정·금액을 확인하세요."
                )
            else:
                logger.info(f"[차대균형 OK] 차변=대변={c_sum:,.0f}")

            # ── 전표 성립 요건 최종 검증 (특허 P26LX017: 증빙일·계정코드·금액 필수) ──
            val_errors = []
            if not result_json.get("date"):
                val_errors.append("증빙일(date) 누락")
            for idx, l in enumerate(result_json.get("lines", []), 1):
                if not l.get("glmaster"):
                    val_errors.append(f"Line {idx}: 계정코드 미확정")
                if not (l.get("bizamt") or 0):
                    val_errors.append(f"Line {idx}: 금액 누락")
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