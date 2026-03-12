# models.py
from pydantic import BaseModel
from typing import List, Optional

class AiParseRequest(BaseModel):
    user_id: str
    natural_text: str

class JournalLine(BaseModel):
    debcre: str      # 'D'(차변) 또는 'C'(대변)
    glmaster: str    # 계정코드
    glname: Optional[str] = "" 
    bizamt: float    # 통화금액
    biztax: float    # 부가세금액
    type: Optional[str] = "" # 💡 [심층대책] AR, REV, TAX 등 식별자
    pctrcd: str      # 손익부서 코드
    pctrnm: Optional[str] = "" 
    anakey: str      # 관리항목(manaky)
    mananm: Optional[str] = "" 
    duedt: str       # 만기일
    text: str        # 적요

class JournalPostRequest(BaseModel):
    user_id: str
    pattern_id: Optional[int] = None
    bizptcd: str
    doctyp: str
    pstdate: str
    docdate: str
    trandt: str      # 💡 DB 스키마와 일치
    currency: str
    exrate: float
    taxcode: str
    modify_reason: str
    raw_text: str
    match_score: Optional[float] = None   # pg_trgm 유사도 최고점 (t_ai_log 업데이트용)
    lines: List[JournalLine]