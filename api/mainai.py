# mainai.py
import os, logging, json, jwt
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session
from database.database import get_db
from database.models import AiParseRequest, JournalPostRequest
from core.ai_engine import DabomHybridEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DaBom_AI_System")

router = APIRouter()
SECRET_KEY = "dabom_super_secret_key_for_jwt"
ALGORITHM = "HS256"

engine = DabomHybridEngine()

def get_current_user(request: Request):
    token = request.cookies.get("dabom_session")
    if not token: raise HTTPException(status_code=401)
    try: 
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception: 
        raise HTTPException(status_code=401)

@router.post("/api/ai-parse")
async def parse_natural_language(req: AiParseRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    try:
        comcd = current_user["comcd"]
        result_json = await engine.generate_final_journal(db, comcd, req.natural_text)
        
        try:
            db.execute(text("""
                INSERT INTO t_ai_log (comcd, user_id, raw_text, ai_json, status) 
                VALUES (:c, :u, :r, :j, 'SUCCESS')
            """), {
                "c": comcd, "u": current_user["userid"], "r": req.natural_text, 
                "j": json.dumps(result_json, ensure_ascii=False)
            })
            db.commit()
        except Exception: 
            db.rollback()

        return {
            "status": "success", 
            "parsed_entities": result_json, 
            "pattern_id": result_json.get("pattern_id"), 
            "recommended_pattern": result_json.get("lines", [])
        }
    except Exception as e:
        logger.error(f"API Error: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/api/journal-entries")
async def create_journal_entry(req: JournalPostRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    try:
        comcd = current_user["comcd"]
        # int 변환 필수: t_cdocnum.fisyr 컬럼 타입이 numeric(4)이므로
        # psycopg2가 문자열 파라미터를 varchar로 바인딩하면 타입 불일치로 0행 반환됨
        fisyr = int(req.pstdate[:4])
        month_str = f"{int(req.pstdate[5:7]):02d}"
        
        # 임베딩 생성 (학습 데이터용)
        # 학습 데이터 저장용 임베딩은 "document" 타입 사용 (검색용 "query"와 구분)
        vec_str = str(await engine.get_embedding(req.raw_text, input_type="document")) if not req.pattern_id else None
        
        now = datetime.now()
        sys_entdt = now.strftime("%Y-%m-%d")
        sys_enttm = now.strftime("%H:%M:%S")

        with db.begin():
            # 1. 채번 (t_cdocnum) ─ 존재 확인 → tonum 범위 확인 → maxnum 증가
            docnum = db.execute(text(
                "SELECT maxnum, tonum FROM t_cdocnum WHERE comcd=:c AND fisyr=:f AND doctype=:d"
            ), {"c": comcd, "f": fisyr, "d": req.doctyp}).fetchone()

            if not docnum:
                raise Exception(
                    f"해당 전표유형의 전표번호가 없습니다. 관리자에게 확인해주세요. "
                    f"(전표유형: {req.doctyp}, 회계연도: {fisyr})"
                )
            if docnum[0] >= docnum[1]:
                raise Exception(
                    f"채번 번호가 소진되었습니다. 관리자에게 확인해주세요. "
                    f"(전표유형: {req.doctyp}, 최대번호: {docnum[1]})"
                )

            res = db.execute(text("""
                UPDATE t_cdocnum SET maxnum = maxnum + 1
                WHERE comcd=:c AND fisyr=:f AND doctype=:d AND maxnum < tonum
                RETURNING maxnum
            """), {"c": comcd, "f": fisyr, "d": req.doctyp}).fetchone()

            if not res:
                raise Exception(
                    f"채번 업데이트에 실패했습니다. 관리자에게 확인해주세요. "
                    f"(전표유형: {req.doctyp}, 회계연도: {fisyr})"
                )
            slipno = str(int(res[0]))

            # 2. 헤더 저장 (t_lhead)
            header_p = {
                "c": comcd, "docno": slipno, "f": fisyr, "docty": req.doctyp[:2],
                "invdt": req.docdate, "posdt": req.pstdate, "period": req.pstdate[5:7],
                "trandt": req.trandt, "curren": req.currency[:3], "exrate": req.exrate,
                "dstat": "N", "entdt": sys_entdt, "enttm": sys_enttm
            }
            db.execute(text("""
                INSERT INTO t_lhead (comcd, docno, fisyr, docty, invdt, posdt, period, trandt, curren, exrate, dstat, entdt, enttm) 
                VALUES (:c, :docno, :f, :docty, :invdt, :posdt, :period, :trandt, :curren, :exrate, :dstat, :entdt, :enttm)
            """), header_p)

            # 세무 계산용 타겟 변수
            target_side = 'C' if req.doctyp == 'CI' else 'D'
            base_bizamt = sum(l.bizamt for l in req.lines if l.debcre == target_side and (not l.biztax or l.biztax == 0))
            base_locamt = round(base_bizamt * req.exrate, 2)
            
            for idx, line in enumerate(req.lines):
                p = {
                    "c": comcd, "f": fisyr, "s": slipno, "l": idx+1, "b": str(req.bizptcd).strip()[:10], 
                    "docty": req.doctyp[:2], "posdt": req.pstdate, "invdt": req.docdate, "curren": req.currency[:3], 
                    "locamt": round(line.bizamt * req.exrate, 2), "loctax": round((line.biztax or 0) * req.exrate, 2), 
                    "bizamt": line.bizamt, "biztax": line.biztax, "taxcd": req.taxcode[:10] if req.taxcode else "", 
                    "manaky": str(line.anakey).strip()[:8], "pctrcd": str(line.pctrcd).strip()[:10], 
                    "glmaster": str(line.glmaster).strip()[:10], 
                    "mulky": " ", 
                    "debcre": line.debcre, "duedt": line.duedt or '1900-01-01', 
                    "bookey": ("C1" if req.doctyp=="CI" else "S1" if req.doctyp=="SI" else "GA"), 
                    "base_bizamt": base_bizamt, "base_locamt": base_locamt
                }
                
                # 3. 상세 저장 (t_lbody)
                db.execute(text("INSERT INTO t_lbody (comcd, fisyr, docno, lineno, debcre, glmaster, bizamt, locamt, mulky, bookey, pctrcd, manaky, duedt) VALUES (:c, :f, :s, :l, :debcre, :glmaster, :bizamt, :locamt, :mulky, :bookey, :pctrcd, :manaky, :duedt)"), p)
                
                # 4. 🌟 [확인] 일반원장 보조부 저장 (t_gbody_o)
                db.execute(text("INSERT INTO t_gbody_o (comcd, glmaster, cscode, mulky, clrdt, clrdoc, fisyr, docno, lineno, docty, invdt, posdt, curren, bookey, debcre, pctrcd, bizamt, locamt, biztax, loctax, taxcd) VALUES (:c, :glmaster, :b, :mulky, '1900-01-01', '', :f, :s, :l, :docty, :invdt, :posdt, :curren, :bookey, :debcre, :pctrcd, :bizamt, :locamt, :biztax, :loctax, :taxcd)"), p)
                
                # 5. 거래처 보조부 저장 (t_cbody_o / t_sbody_o)
                if req.doctyp == 'CI': 
                    db.execute(text("INSERT INTO t_cbody_o (comcd, fisyr, docno, lineno, custcd, glmaster, mulky, clrdt, clrdoc, docty, invdt, posdt, curren, bookey, debcre, duedt, bizamt, locamt, taxcd) VALUES (:c, :f, :s, :l, :b, :glmaster, :mulky, '1900-01-01', '', :docty, :invdt, :posdt, :curren, :bookey, :debcre, :duedt, :bizamt, :locamt, :taxcd)"), p)
                elif req.doctyp == 'SI': 
                    db.execute(text("INSERT INTO t_sbody_o (comcd, fisyr, docno, lineno, suppcd, glmaster, mulky, clrdt, clrdoc, docty, invdt, posdt, curren, bookey, debcre, duedt, bizamt, locamt, taxcd) VALUES (:c, :f, :s, :l, :b, :glmaster, :mulky, '1900-01-01', '', :docty, :invdt, :posdt, :curren, :bookey, :debcre, :duedt, :bizamt, :locamt, :taxcd)"), p)
                
                # 6. 🌟 [확인] 세무 데이터 저장 (t_ctax)
                if line.biztax != 0: 
                    db.execute(text("INSERT INTO t_ctax (comcd, fisyr, docno, lineno, taxcd, debcre, bizamt, locamt, biztax, loctax) VALUES (:c, :f, :s, :l, :taxcd, :debcre, :base_bizamt, :base_locamt, :biztax, :loctax)"), p)
                
                # 7. 월 합계 잔액 업데이트 (t_totlg)
                db.execute(text(f"""
                    INSERT INTO t_totlg (fisyr, comcd, debcre, glmaster, bizcat, pctrcd, cctrcd, prjno, macarea, manaky, curren, ledger, trs{month_str}, loc{month_str}) 
                    VALUES (:f, :c, :debcre, :glmaster, '', :pctrcd, '', '', 0, :manaky, :curren, '0L', :bizamt, :locamt) 
                    ON CONFLICT (fisyr, comcd, debcre, glmaster, bizcat, pctrcd, cctrcd, prjno, macarea, manaky, curren, ledger) 
                    DO UPDATE SET trs{month_str} = t_totlg.trs{month_str} + EXCLUDED.trs{month_str}, loc{month_str} = t_totlg.loc{month_str} + EXCLUDED.loc{month_str}
                """), p)
            
            # 8. 사용자 학습 데이터 저장 (UPSERT)
            if vec_str:
                j = json.dumps([l.model_dump() for l in req.lines], ensure_ascii=False)
                db.execute(text("""
                    INSERT INTO t_v_user_learn (comcd, usrnm, input_tx, embedding, final_json, hit_count, upd_date) 
                    VALUES (:c, :u, :tx, :v, :j, 1, NOW())
                    ON CONFLICT (input_tx) DO UPDATE SET 
                        hit_count = t_v_user_learn.hit_count + 1, final_json = EXCLUDED.final_json, upd_date = NOW()
                """), {
                    "c": comcd, 
                    "u": current_user["username"], 
                    "tx": req.raw_text, 
                    "v": vec_str, 
                    "j": j
                })
        
        return {"status": "success", "slipno": slipno}
    except Exception as e:
        logger.error(f"Posting Error: {str(e)}")
        return {"status": "error", "message": str(e)}

@router.get("/api/patterns")
async def get_std_patterns(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """t_v_std_pattern의 패턴 목록 반환 — 상황별 예제 드롭다운에서 사용"""
    rows = db.execute(text(
        "SELECT id, pattern_nm, example_tx, docty FROM t_v_std_pattern ORDER BY id DESC LIMIT 100"
    )).fetchall()
    return [{"id": r[0], "pattern_nm": r[1], "example_tx": r[2], "docty": r[3]} for r in rows]

@router.get("/api/bizpt-gl")
async def get_bizpt_default_gl(bizptcd: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """거래처 선택 시 기본 AR(custgl) / AP(suppgl) 계정 자동 조회"""
    comcd = current_user["comcd"]
    row = db.execute(
        text("SELECT suppgl, custgl FROM t_cbizpt WHERE comcd=:c AND bizptcd=:b LIMIT 1"),
        {"c": comcd, "b": bizptcd}
    ).fetchone()
    if not row:
        return {"suppgl": "", "custgl": "", "suppgl_nm": "", "custgl_nm": ""}

    suppgl = row[0] or ""
    custgl = row[1] or ""
    suppgl_nm, custgl_nm = "", ""

    if suppgl:
        r = db.execute(
            text("SELECT glname1 FROM t_cglmst WHERE comcd=:c AND glmaster=:g LIMIT 1"),
            {"c": comcd, "g": suppgl}
        ).fetchone()
        suppgl_nm = r[0] if r else ""

    if custgl:
        r = db.execute(
            text("SELECT glname1 FROM t_cglmst WHERE comcd=:c AND glmaster=:g LIMIT 1"),
            {"c": comcd, "g": custgl}
        ).fetchone()
        custgl_nm = r[0] if r else ""

    return {"suppgl": suppgl, "custgl": custgl, "suppgl_nm": suppgl_nm, "custgl_nm": custgl_nm}

@router.get("/api/search/{search_type}")
async def master_search(search_type: str, q: str = "", db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    comcd = current_user["comcd"]
    qf = f"%{q}%"
    sql_map = {
        "bizpt": "SELECT bizptcd, bizname1, '' FROM t_cbizpt WHERE comcd = :c AND (bizptcd ILIKE :q OR bizname1 ILIKE :q) ORDER BY bizname1 LIMIT 50", 
        "taxkey": "SELECT taxcd, taxnm, '' FROM t_ctxkey WHERE comcd = :c AND (taxcd ILIKE :q OR taxnm ILIKE :q) ORDER BY taxcd LIMIT 50", 
        "pctr": "SELECT pctrcd, prcrnm, '' FROM t_cprocos WHERE comcd = :c AND (pctrcd ILIKE :q OR prcrnm ILIKE :q) ORDER BY prcrnm LIMIT 50", 
        "anakey": "SELECT manaky, mananm, '' FROM t_mbkey WHERE comcd = :c AND (manaky ILIKE :q OR mananm ILIKE :q) ORDER BY mananm LIMIT 50", 
        "glmaster": "SELECT glmaster, glname1, gltype FROM t_cglmst WHERE comcd = :c AND (glmaster ILIKE :q OR glname1 ILIKE :q) ORDER BY glmaster LIMIT 50"
    }
    query = sql_map.get(search_type)
    if not query: return []
    rows = db.execute(text(query), {"c": comcd, "q": qf}).fetchall()
    return [{"code": r[0], "name": r[1], "type": r[2]} for r in rows]