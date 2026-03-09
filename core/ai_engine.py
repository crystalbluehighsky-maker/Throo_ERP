# ai_engine.py
import os, json, anthropic, voyageai, re, asyncio
from datetime import date
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging
from dotenv import load_dotenv

# .env 로드
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

logger = logging.getLogger("DaBom_AI_Engine")

class DabomHybridEngine:
    # 전표 유형 사전 분류용 키워드 정의
    _AR_KEYWORDS  = ['매출', '입금', '수금', '판매', '납품', '청구', '세금계산서 발행']
    _AP_KEYWORDS  = ['매입', '지급', '송금', '구매', '발주', '구입', '결제', '출금', '세금계산서 수취']
    # 거래처 상호 탐지: 법인격 키워드 또는 업종명 포함 패턴
    _COUNTERPARTY_RE = re.compile(
        r'(?:㈜|주식회사|\(주\)|유한회사|\(유\))[가-힣A-Za-z\s]{1,10}'
        r'|[가-힣A-Za-z]{2,8}(?:주식회사|㈜|\(주\)|유한회사|\(유\))'
        r'|[가-힣]{2,5}(?:전자|화학|건설|물산|통신|식품|유통|제약|은행|자동차|시스템|솔루션|물류|에너지)'
    )

    def __init__(self):
        self.vo = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        try:
            prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "journal_generation.txt")
            with open(prompt_path, "r", encoding="utf-8") as f:
                self.system_prompt_template = f.read()
        except Exception as e:
            logger.error(f"Failed to load prompt template: {e}")
            self.system_prompt_template = ""

    def _detect_doctype_hint(self, raw_text: str) -> str:
        """
        Python 규칙 기반 전표 유형 1차 추론 (AI 호출 전).
        - 거래처 탐지 + AR 방향 키워드 → CI (매출)
        - 거래처 탐지 + AP 방향 키워드 → SI (매입)
        - 거래처 미탐지 또는 방향 불명확 → GL (일반) 기본값
        """
        has_counterparty = bool(self._COUNTERPARTY_RE.search(raw_text))
        has_ar = any(kw in raw_text for kw in self._AR_KEYWORDS)
        has_ap = any(kw in raw_text for kw in self._AP_KEYWORDS)

        if has_counterparty:
            if has_ar and not has_ap:
                return 'CI'
            if has_ap and not has_ar:
                return 'SI'
        return 'GL'

    async def get_embedding(self, text_input: str, input_type: str = "query") -> list:
        """
        Voyage AI voyage-3 임베딩 생성.
        - 벡터 DB 검색(쿼리)  → input_type="query"   (기본값)
        - 패턴 저장(문서 삽입) → input_type="document"
        """
        result = self.vo.embed([text_input], model="voyage-3", input_type=input_type)
        return result.embeddings[0]

    async def generate_final_journal(self, db: Session, comcd: str, raw_text: str):
        # 0. Python 규칙 기반 전표 유형 사전 추론
        doctype_hint = self._detect_doctype_hint(raw_text)
        logger.info(f"Doctype hint: {doctype_hint} for: '{raw_text[:40]}'")

        # 1. Vector DB 검색 (RAG) — 쿼리용 임베딩, docty 컬럼 포함 조회
        vec = await self.get_embedding(raw_text, input_type="query")
        query = text("""
            SELECT id, journal_json, docty, (embedding <=> :v) as dist FROM t_v_std_pattern
            UNION ALL
            SELECT id, final_json AS journal_json, NULL::text AS docty, (embedding <=> :v) AS dist
            FROM t_v_user_learn WHERE comcd = :c
            ORDER BY dist ASC LIMIT 1
        """)
        cand = db.execute(query, {"v": str(vec), "c": comcd}).fetchone()

        pattern_id          = None
        pattern_guide       = ""
        gl_fallback_matched = False
        effective_docty     = doctype_hint  # 기본값: Python 키워드 힌트

        if cand and (1 - cand.dist) > 0.55:
            # ── 1순위: Vector DB 패턴 매칭 성공 → DB 패턴의 docty 사용 ──
            pattern_id = cand.id
            db_docty   = (cand.docty or 'GL').strip().upper()
            raw_data   = cand.journal_json
            parsed_pattern = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
            pattern_guide  = f"### [필수 참조 패턴]\n{json.dumps(parsed_pattern, ensure_ascii=False)}"

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

        max_retries = 3
        resp_text = ""
        for attempt in range(max_retries):
            try:
                response = self.client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=1500,
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}]
                )
                resp_text = response.content[0].text
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise e

        try:
            start_idx = resp_text.find('{')
            if start_idx != -1:
                brace_count = 0
                for i, char in enumerate(resp_text[start_idx:]):
                    if char == '{': brace_count += 1
                    elif char == '}': brace_count -= 1
                    if brace_count == 0:
                        resp_text = resp_text[start_idx : start_idx + i + 1]
                        break
            
            result_json = json.loads(re.sub(r',\s*([\]}])', r'\1', resp_text), strict=False)
            result_json['pattern_id'] = pattern_id
            # 하이브리드 결정 전표유형 강제 적용 (DB패턴 + 키워드 분석 결과 → AI 응답 override)
            result_json['doctyp'] = effective_docty
            # Vector DB 패턴 매칭 또는 GL 계정마스터 fallback 검색 성공 시 "DB", 순수 AI 생성 시 "AI"
            result_json['source'] = "DB" if (pattern_id or gl_fallback_matched) else "AI"
            
            # 💡 [핵심 추가] 파이썬 백엔드 수학적 금액 강제 계산 로직
            tot = float(result_json.get("total_amount", 0))
            vr = float(result_json.get("vat_rate", 0))
            base = round(tot / 1.1) if vr > 0 else tot
            vat = tot - base if vr > 0 else 0

            # 만기일 변수 확보
            due_date = result_json.get("due_date", "")
            
            # 3. 마스터 DB 정밀 매핑
            bn = result_json.get("bizname", "")
            result_json["bizptcd"] = ""
            if bn:
                row = db.execute(text("SELECT bizptcd, bizname1 FROM t_cbizpt WHERE comcd=:c AND bizname1 ILIKE :n LIMIT 1"), {"c": comcd, "n": f"%{bn}%"}).fetchone()
                if row: result_json["bizptcd"], result_json["bizname"] = row[0], row[1]

            im = result_json.get("item_name", "")
            result_json["manaky"], result_json["mananm"] = "", ""
            if im:
                row = db.execute(text("SELECT manaky, mananm FROM t_mbkey WHERE comcd=:c AND mananm ILIKE :n LIMIT 1"), {"c": comcd, "n": f"%{im}%"}).fetchone()
                if row: result_json["manaky"], result_json["mananm"] = row[0], row[1]

            pn = result_json.get("profit_center_name", "")
            result_json["pctrcd"], result_json["pctrnm"] = "", ""
            if pn:
                row = db.execute(text("SELECT pctrcd, prcrnm FROM t_cprocos WHERE comcd=:c AND prcrnm ILIKE :n LIMIT 1"), {"c": comcd, "n": f"%{pn}%"}).fetchone()
                if row: result_json["pctrcd"], result_json["pctrnm"] = row[0], row[1]

            dt = result_json.get("doctyp", "GL")
            txcd = "S010" if dt == "CI" and vr > 0 else "S170" if dt == "CI" and vr == 0 else "P010" if dt == "SI" and vr > 0 else "P110" if dt == "SI" and vr == 0 else ""
            result_json["taxcd"], result_json["taxnm"] = txcd, ""
            if txcd:
                row = db.execute(text("SELECT taxcd, taxnm FROM t_ctxkey WHERE comcd=:c AND taxcd=:t LIMIT 1"), {"c": comcd, "t": txcd}).fetchone()
                if row: result_json["taxcd"], result_json["taxnm"] = row[0], row[1]

            # 💡 [핵심 추가] 라인별 금액 및 만기일 강제 주입
            for line in result_json.get("lines", []):
                l_type = line.get("type", "")

                # 금액 분할 적용
                if l_type in ["AR", "AP"]:
                    line["bizamt"], line["biztax"] = tot, 0
                    # AR: 입금 예정일(입금 후 매출채권 정리 기준), AP: 지급 만기일
                    # AI가 라인별 due_date를 계산해 넣었으면 우선 사용, 없으면 헤더 due_date fallback
                    line["duedt"] = line.get("due_date", "") or due_date
                elif l_type in ["REV", "EXP"]:
                    line["bizamt"], line["biztax"] = base, 0
                    line["duedt"] = ""
                elif l_type == "TAX":
                    line["bizamt"], line["biztax"] = vat, vat
                    line["duedt"] = ""
                else:
                    line["duedt"] = ""

                # GL 계정명 무결성 검증: DB에 없는 AI 임의 추측 코드는 빈칸으로 초기화해 사용자 선택 유도
                glcd = line.get("glmaster", "")
                line["glname"], line["gltype"] = "", ""
                if glcd:
                    row = db.execute(
                        text("SELECT glname1, gltype FROM t_cglmst WHERE comcd=:c AND glmaster=:g LIMIT 1"),
                        {"c": comcd, "g": glcd}
                    ).fetchone()
                    if row:
                        line["glname"], line["gltype"] = row[0], row[1]
                    else:
                        # DB에 없는 코드 → AI 임의 추측 방지: 코드 초기화, 계정명으로 검색 필요 안내
                        line["glmaster"] = ""
                        line["glname"] = "(계정 검색 필요)"

            return result_json
        except Exception as e:
            logger.error(f"Final Parse Error: {e} | Raw: {resp_text}")
            raise Exception(f"AI 분석 처리 실패: {str(e)}")