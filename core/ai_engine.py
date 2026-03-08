# ai_engine.py
import os, json, anthropic, voyageai, re, asyncio
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger("DaBom_AI_Engine")

class DabomHybridEngine:
    def __init__(self):
        self.vo = voyageai.Client(api_key=os.getenv("VOYAGE_API_KEY"))
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

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

        if cand and (1 - cand.dist) > 0.55:
            pattern_id = cand.id
            raw_data = cand.journal_json
            parsed_pattern = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
            pattern_guide = f"### [필수 참조 패턴]\n{json.dumps(parsed_pattern, ensure_ascii=False)}"

        # 2. AI 지시문
        prompt = f"""당신은 데이터를 JSON으로만 변환하는 기계입니다. 회계적 지식을 임의로 발휘하지 마십시오.

[분개 상세(lines) 생성 절대 규칙 - 1순위]
- 하단에 '[필수 참조 패턴]'이 주어졌다면: 그 안의 'lines' 배열 구조(debcre, glmaster, type 등)를 단 한 글자도 바꾸지 말고 100% 똑같이 복사하십시오.

[분개 상세(lines) 생성 절대 규칙 - 2순위 (패턴이 없을 때)]
- 패턴이 주어지지 않았다면, 반드시 아래의 고정된 배열 중 하나를 선택하여 '그대로' 출력하십시오.
  1) 매출(CI) 거래일 경우 (무조건 3줄):
     [
       {{ "debcre": "D", "glmaster": "110000", "type": "AR", "text": "적요" }},
       {{ "debcre": "C", "glmaster": "410000", "type": "REV", "text": "적요" }},
       {{ "debcre": "C", "glmaster": "214000", "type": "TAX", "text": "적요" }}
     ]
  2) 매입(SI) 거래일 경우 (무조건 3줄):
     [
       {{ "debcre": "D", "glmaster": "비용계정", "type": "EXP", "text": "적요" }},
       {{ "debcre": "D", "glmaster": "135000", "type": "TAX", "text": "적요" }},
       {{ "debcre": "C", "glmaster": "253000", "type": "AP", "text": "적요" }}
     ]

[기타 데이터 추출 규칙]
- 전표유형: 매출(CI), 매입(SI), 일반(GL)
- 금액: 부가세 포함 총액
- 거래처명(bizname), 품목명(item_name), 손익부서명(profit_center_name) 추출. (없으면 무조건 "" 처리)
- 날짜 추출 (반드시 YYYY-MM-DD 포맷으로 변환할 것. 예: 26.3.31 -> 2026-03-31):
  1. (세금)계산서 발행일: 문장에 "세금계산서 발행일", "계산서 발행일", "발행일"이 있으면 `tax_date`에 추출. 없으면 "".
  2. 만기일(결제일): 문장에 "입금일", "입금일자", "출금일", "출금일자", "지급일자", "만기일", "만기일자"가 있으면 `due_date`에 추출. 없으면 "".

[출력 포맷 예시]
{{
  "bizname": "...", "item_name": "...", "profit_center_name": "", 
  "date": "YYYY-MM-DD", "tax_date": "YYYY-MM-DD", "due_date": "YYYY-MM-DD",
  "doctyp": "CI", "vat_rate": 10, "total_amount": 2200000, 
  "lines": [위 규칙에 따른 배열 삽입]
}}

[사용자 입력]: {raw_text}
{pattern_guide}
"""

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