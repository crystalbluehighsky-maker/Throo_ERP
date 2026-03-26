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
    type: Optional[str] = ""    # 💡 [심층대책] AR, REV, TAX 등 식별자
    gltype: Optional[str] = ""  # 마스터 계정 유형: 'C'(고객AR), 'S'(공급AP), 'X'(세금), ''(일반)
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
    match_score:  Optional[float] = None   # pg_trgm 유사도 최고점 (t_ai_log 업데이트용)
    supply_base:  Optional[float] = None   # 공급가액 (t_ctax bizamt 적재용)
    supply_vat:   Optional[float] = None   # 부가세액 (t_ctax biztax 적재용)
    txgubun:      Optional[str]   = ""     # 세금 구분 코드 (t_ntxkey.txgubun)
    mulky:        Optional[str]   = " "    # 멀티키: ' '(일반), 'A'(선수/선급금)
    lines: List[JournalLine]