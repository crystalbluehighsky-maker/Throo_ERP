# ai_engine.py
import os, json, anthropic, voyageai, re, asyncio
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging
from dotenv import load_dotenv

# .env 로드
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

logger = logging.getLogger("DaBom_AI_Engine")

class DabomHybridEngine:
    def __init__(self):
        self.vo = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        
        # Load prompt template
        try:
            prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "journal_generation.txt")
            with open(prompt_path, "r", encoding="utf-8") as f:
                self.system_prompt_template = f.read()
        except Exception as e:
            logger.error(f"Failed to load prompt template: {e}")
            self.system_prompt_template = "" # Fallback or error handling

    async def get_embedding(self, text_input: str) -> list:
        result = self.vo.embed([text_input], model="voyage-3")
        return result.embeddings[0]

    async def generate_final_journal(self, db: Session, comcd: str, raw_text: str):
        vec = await self.get_embedding(raw_text)

        # 1. Vector DB 검색 (RAG)
        query = text("""
            SELECT id, journal_json, (embedding <=> :v) as dist FROM t_v_std_pattern
            UNION ALL
            SELECT id, final_json as journal_json, (embedding <=> :v) as dist FROM t_v_user_learn WHERE comcd = :c
            ORDER BY dist ASC LIMIT 1
        """)
        cand = db.execute(query, {"v": str(vec), "c": comcd}).fetchone()

        pattern_id = None
        pattern_guide = ""
        gl_fallback_matched = False  # GL 계정마스터 키워드 매칭 성공 여부

        if cand and (1 - cand.dist) > 0.55:
            # Vector DB 패턴 매칭 성공
            pattern_id = cand.id
            raw_data = cand.journal_json
            parsed_pattern = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
            pattern_guide = f"### [필수 참조 패턴]\n{json.dumps(parsed_pattern, ensure_ascii=False)}"
        else:
            # Vector DB 매칭 실패: 원문 한글 키워드로 계정과목 마스터 검색
            # 2~5글자 한글 토큰을 점진적으로 단축해 ILIKE 매칭 가능성을 높임
            kw_set = set()
            for word in re.findall(r'[가-힣]{2,}', raw_text):
                for ln in range(min(len(word), 5), 1, -1):
                    kw_set.add(word[:ln])
            keywords = list(kw_set)[:12]

            seen_gl = set()
            gl_hints = []

            # 1순위: 회사 사용 계정 t_cglmst (comcd 필터)
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

            # 2순위: t_cglmst 미검색 시 전체 표준 계정 t_nglmst (comcd 조건 없음)
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
                pattern_guide = f"### [계정과목 마스터 참조 후보({src_label}) - 아래 중 적절한 계정 사용]\n{json.dumps(gl_hints, ensure_ascii=False)}"
                gl_fallback_matched = True
                logger.info(f"GL fallback({src_label}) matched {len(gl_hints)} accounts for: '{raw_text[:30]}'")

        # 2. AI 지시문
        if not self.system_prompt_template:
             raise Exception("System prompt template not loaded.")
             
        prompt = self.system_prompt_template.replace("{{RAW_TEXT}}", raw_text).replace("{{PATTERN_GUIDE}}", pattern_guide)

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
                    line["duedt"] = due_date # AR/AP 라인에만 입금일(만기일) 주입
                elif l_type in ["REV", "EXP"]:
                    line["bizamt"], line["biztax"] = base, 0
                    line["duedt"] = ""
                elif l_type == "TAX":
                    line["bizamt"], line["biztax"] = vat, vat
                    line["duedt"] = ""
                else:
                    line["duedt"] = ""

                # GL 계정명 무결성 검증
                glcd = line.get("glmaster", "")
                line["glname"], line["gltype"] = "", ""
                if glcd:
                    row = db.execute(text("SELECT glname1, gltype FROM t_cglmst WHERE comcd=:c AND glmaster=:g LIMIT 1"), {"c": comcd, "g": glcd}).fetchone()
                    if row: 
                        line["glname"], line["gltype"] = row[0], row[1]

            return result_json
        except Exception as e:
            logger.error(f"Final Parse Error: {e} | Raw: {resp_text}")
            raise Exception(f"AI 분석 처리 실패: {str(e)}")