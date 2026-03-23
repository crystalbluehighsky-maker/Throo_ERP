# mainai.py
import os, logging, json, jwt
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from database.database import get_db
from database.models import AiParseRequest, JournalPostRequest
from core.ai_engine import ThrooHybridEngine

class ValidateMasterRequest(BaseModel):
    type: str   # "glmaster" | "pctr" | "anakey"
    value: str

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Throo_AI_System")

router = APIRouter()

def _q(value, places: str = '0.01') -> Decimal:
    """회계 표준 반올림 헬퍼 (ROUND_HALF_UP).
    places='1'   → 원(KRW) 단위 정수 반올림
    places='0.01'→ 외화 환산금액 소수 2자리 반올림
    float 부동소수점 오차를 방지하기 위해 str 경유 Decimal 변환을 사용한다.
    """
    return Decimal(str(value or 0)).quantize(Decimal(places), rounding=ROUND_HALF_UP)

SECRET_KEY = "throo_super_secret_key_for_jwt"
ALGORITHM = "HS256"

engine = ThrooHybridEngine()

def get_current_user(request: Request):
    token = request.cookies.get("throo_session")
    if not token: raise HTTPException(status_code=401)
    try: 
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception: 
        raise HTTPException(status_code=401)

@router.post("/api/ai-parse")
async def parse_natural_language(req: AiParseRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    try:
        comcd = current_user["comcd"]
        result_json = await engine.analyze_and_generate_journal(db, comcd, req.natural_text)
        
        try:
            db.execute(text("""
                INSERT INTO t_ai_log (comcd, user_id, raw_text, ai_json, status, match_score)
                VALUES (:c, :u, :r, :j, 'SUCCESS', :ms)
            """), {
                "c":  comcd,
                "u":  current_user["userid"],
                "r":  req.natural_text,
                "j":  json.dumps(result_json, ensure_ascii=False),
                "ms": result_json.get("match_score"),   # 분석 시점 즉시 기록
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

        # ── 저장 전 필수 필드 공백 검증 (Phase A) ───────────────────────────
        # 프론트 우회 저장 차단 — DB 조회 전에 먼저 값 존재 여부 확인
        if not req.lines:
            raise HTTPException(status_code=400, detail="저장할 분개 라인이 없습니다.")
        for idx, line in enumerate(req.lines, 1):
            if not (line.glmaster or "").strip():
                raise HTTPException(status_code=400, detail=f"[{idx}번 라인] 계정코드가 없습니다.")
            if (line.bizamt or 0) <= 0:
                raise HTTPException(status_code=400, detail=f"[{idx}번 라인] 금액을 입력하세요.")
            if not (line.pctrcd or "").strip():
                raise HTTPException(status_code=400, detail=f"[{idx}번 라인] 부서(손익부서)가 없습니다.")
            if not (line.anakey or "").strip():
                raise HTTPException(status_code=400, detail=f"[{idx}번 라인] 관리항목이 없습니다.")
            if (line.gltype or "").strip() in ("C", "S") and not (line.duedt or "").strip():
                raise HTTPException(status_code=400, detail=f"[{idx}번 라인] 채권/채무 계정은 만기일자가 필수입니다.")

        # ── 저장 전 마스터 데이터 이중 검증 (Phase B) ───────────────────────
        # 프론트 경고를 무시하고 강제 저장하는 케이스까지 완전 차단
        for idx, line in enumerate(req.lines, 1):
            if line.glmaster:
                row = db.execute(
                    text("SELECT 1 FROM t_cglmst WHERE comcd=:c AND glmaster=:v LIMIT 1"),
                    {"c": comcd, "v": line.glmaster}
                ).fetchone()
                if not row:
                    raise HTTPException(
                        status_code=400,
                        detail=f"[{idx}번 라인] 계정코드 '{line.glmaster}'가 마스터에 존재하지 않습니다."
                    )
            if line.pctrcd:
                row = db.execute(
                    text("SELECT 1 FROM t_cprocos WHERE comcd=:c AND pctrcd=:v LIMIT 1"),
                    {"c": comcd, "v": line.pctrcd}
                ).fetchone()
                if not row:
                    raise HTTPException(
                        status_code=400,
                        detail=f"[{idx}번 라인] 손익부서 코드 '{line.pctrcd}'가 마스터에 존재하지 않습니다."
                    )
            if line.anakey:
                row = db.execute(
                    text("SELECT 1 FROM t_mbkey WHERE comcd=:c AND manaky=:v LIMIT 1"),
                    {"c": comcd, "v": line.anakey}
                ).fetchone()
                if not row:
                    raise HTTPException(
                        status_code=400,
                        detail=f"[{idx}번 라인] 관리항목 코드 '{line.anakey}'가 마스터에 존재하지 않습니다."
                    )
        # ── 검증 완료 ──────────────────────────────────────────────────────
        # 검증 SELECT로 autobegin된 세션 트랜잭션을 명시적으로 종료한 뒤
        # with db.begin()으로 쓰기 전용 새 트랜잭션을 시작한다.
        # (SQLAlchemy 2.x autobegin 상태에서 db.begin() 호출 시 InvalidRequestError 방지)
        db.rollback()

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

            # 멀티키: 선수금('A') 등. 빈 문자열 방지를 위해 공백 1자리 보장.
            _mulky = (req.mulky or " ").strip() or " "

            # 2. 헤더 저장 (t_lhead)
            # ⚠️ t_lhead 스키마에는 mulky·taxcode 컬럼이 없음 → 포함 금지(UndefinedColumn 방지)
            # mulky·taxcd는 라인 저장 시 body 테이블에만 기록한다.
            header_p = {
                "c": comcd, "docno": slipno, "f": fisyr, "docty": req.doctyp[:2],
                "invdt": req.docdate, "posdt": req.pstdate, "period": req.pstdate[5:7],
                "trandt": req.trandt, "curren": req.currency[:3], "exrate": req.exrate,
                "dstat": "N", "entdt": sys_entdt, "enttm": sys_enttm,
            }
            db.execute(text("""
                INSERT INTO t_lhead (comcd, docno, fisyr, docty, invdt, posdt, period, trandt, curren, exrate, dstat, entdt, enttm) 
                VALUES (:c, :docno, :f, :docty, :invdt, :posdt, :period, :trandt, :curren, :exrate, :dstat, :entdt, :enttm)
            """), header_p)

            # 세무 계산용 타겟 변수
            target_side = 'C' if req.doctyp == 'CI' else 'D'
            base_bizamt = sum(Decimal(str(l.bizamt)) for l in req.lines if l.debcre == target_side and (not l.biztax or l.biztax == 0))
            _exrate     = Decimal(str(req.exrate))
            base_locamt = _q(base_bizamt * _exrate)

            for idx, line in enumerate(req.lines):
                p = {
                    "c": comcd, "f": fisyr, "s": slipno, "l": idx+1, "b": str(req.bizptcd).strip()[:10], 
                    "docty": req.doctyp[:2], "posdt": req.pstdate, "invdt": req.docdate, "curren": req.currency[:3], 
                    "locamt": _q(Decimal(str(line.bizamt)) * _exrate), "loctax": _q(Decimal(str(line.biztax or 0)) * _exrate), 
                    "bizamt": line.bizamt, "biztax": line.biztax, "taxcd": req.taxcode[:10] if req.taxcode else "", 
                    "manaky": str(line.anakey).strip()[:8], "pctrcd": str(line.pctrcd).strip()[:10], 
                    "glmaster": str(line.glmaster).strip()[:10], 
                    "mulky": _mulky,
                    "debcre": line.debcre, "duedt": line.duedt or '1900-01-01', 
                    "bookey": ("C1" if req.doctyp=="CI" else "S1" if req.doctyp=="SI" else "GA"), 
                    "base_bizamt": base_bizamt, "base_locamt": base_locamt
                }
                
                # 3. 상세 저장 (t_lbody)
                db.execute(text("INSERT INTO t_lbody (comcd, fisyr, docno, lineno, debcre, glmaster, bizamt, locamt, mulky, bookey, pctrcd, manaky, duedt) VALUES (:c, :f, :s, :l, :debcre, :glmaster, :bizamt, :locamt, :mulky, :bookey, :pctrcd, :manaky, :duedt)"), p)
                
                # 4. 🌟 [확인] 일반원장 보조부 저장 (t_gbody_o)
                db.execute(text("INSERT INTO t_gbody_o (comcd, glmaster, cscode, mulky, clrdt, clrdoc, fisyr, docno, lineno, docty, invdt, posdt, curren, bookey, debcre, pctrcd, bizamt, locamt, biztax, loctax, taxcd) VALUES (:c, :glmaster, :b, :mulky, '1900-01-01', '', :f, :s, :l, :docty, :invdt, :posdt, :curren, :bookey, :debcre, :pctrcd, :bizamt, :locamt, :biztax, :loctax, :taxcd)"), p)
                
                # 5. 거래처 보조부 저장 — gltype 마스터값 기준 필터링
                # 'C'(AR 고객 오픈아이템) → t_cbody_o 1줄
                # 'S'(AP 공급업체 오픈아이템) → t_sbody_o 1줄
                # 그 외(비용·세금·자산 등) → 건너뜀
                _gltype = (line.gltype or "").strip()
                if _gltype == 'C':
                    db.execute(text("INSERT INTO t_cbody_o (comcd, fisyr, docno, lineno, custcd, glmaster, mulky, clrdt, clrdoc, docty, invdt, posdt, curren, bookey, debcre, duedt, bizamt, locamt, taxcd) VALUES (:c, :f, :s, :l, :b, :glmaster, :mulky, '1900-01-01', '', :docty, :invdt, :posdt, :curren, :bookey, :debcre, :duedt, :bizamt, :locamt, :taxcd)"), p)
                elif _gltype == 'S':
                    db.execute(text("INSERT INTO t_sbody_o (comcd, fisyr, docno, lineno, suppcd, glmaster, mulky, clrdt, clrdoc, docty, invdt, posdt, curren, bookey, debcre, duedt, bizamt, locamt, taxcd) VALUES (:c, :f, :s, :l, :b, :glmaster, :mulky, '1900-01-01', '', :docty, :invdt, :posdt, :curren, :bookey, :debcre, :duedt, :bizamt, :locamt, :taxcd)"), p)
                
                # 7. 월 합계 잔액 업데이트 (t_totlg)
                db.execute(text(f"""
                    INSERT INTO t_totlg (fisyr, comcd, debcre, glmaster, bizcat, pctrcd, cctrcd, prjno, macarea, manaky, curren, ledger, trs{month_str}, loc{month_str}) 
                    VALUES (:f, :c, :debcre, :glmaster, '', :pctrcd, '', '', 0, :manaky, :curren, '0L', :bizamt, :locamt) 
                    ON CONFLICT (fisyr, comcd, debcre, glmaster, bizcat, pctrcd, cctrcd, prjno, macarea, manaky, curren, ledger) 
                    DO UPDATE SET trs{month_str} = t_totlg.trs{month_str} + EXCLUDED.trs{month_str}, loc{month_str} = t_totlg.loc{month_str} + EXCLUDED.loc{month_str}
                """), p)
            
            # ── t_ctax: 세금코드 존재 시 무조건 1건 저장 (lineno=0 헤더 레벨) ─────────
            # 세금코드가 입력된 모든 전표에 세금 거래 기록을 남긴다.
            # txgubun·supply_vat 조건으로 저장을 스킵하던 기존 로직 제거 → 무결성 보장.

            # 1. 세금코드 정리
            _taxcd_clean = (req.taxcode or "").split(' : ')[0].strip()[:10]

            # 2. txgubun / taxtyp 파싱
            #    프론트가 'txgubun|taxtyp|taxrate' 형식으로 전달하므로 파이프 분리
            _raw_tx  = (req.txgubun or "").strip()
            _parts   = _raw_tx.split('|')
            _txgubun = _parts[0] if _parts else ""
            _taxtyp  = _parts[1] if len(_parts) > 1 else ""

            # 3. 정보 부족 시 DB 보완 (구버전 클라이언트 · txgubun 미전달 호환)
            if _taxcd_clean and (not _txgubun or not _taxtyp):
                _tg_row = db.execute(text("""
                    SELECT n.txgubun, c.taxtyp
                    FROM   t_ctxkey c
                    LEFT JOIN t_ntxkey n ON c.taxcd = n.taxcd
                    WHERE  c.comcd = :c AND c.taxcd = :t
                    LIMIT  1
                """), {"c": comcd, "t": _taxcd_clean}).fetchone()
                if _tg_row:
                    _txgubun = (_tg_row[0] or "").strip()
                    _taxtyp  = (_tg_row[1] or "").strip()

            # 4. 세금코드가 있으면 금액·구분 관계없이 무조건 INSERT
            if _taxcd_clean:
                _supply_base = Decimal(str(req.supply_base)) if req.supply_base is not None else base_bizamt
                _supply_vat  = Decimal(str(req.supply_vat))  if req.supply_vat  is not None else Decimal('0')
                _tax_debcre  = 'C' if req.doctyp == 'CI' else 'D'
                db.execute(text("""
                    INSERT INTO t_ctax
                        (comcd, fisyr, docno, lineno, taxcd, debcre, taxtyp,
                         srcdoc, srcyr, srclin, bizamt, locamt, biztax, loctax)
                    VALUES
                        (:c, :f, :s, 0, :taxcd, :debcre, :tt,
                         '', 0, 0, :bizamt, :locamt, :biztax, :loctax)
                """), {
                    "c":      comcd,
                    "f":      fisyr,
                    "s":      slipno,
                    "taxcd":  _taxcd_clean,
                    "debcre": _tax_debcre,
                    "tt":     _taxtyp,
                    "bizamt": _supply_base,
                    "locamt": _q(_supply_base * _exrate),
                    "biztax": _supply_vat,
                    "loctax": _q(_supply_vat  * _exrate),
                })
                logger.info(
                    f"[t_ctax] 저장 성공: docno={slipno}, taxcd={_taxcd_clean}, "
                    f"txgubun={_txgubun}, taxtyp={_taxtyp}, "
                    f"bizamt={_supply_base:,.0f}, biztax={_supply_vat:,.0f}"
                )

            # 8. 사용자 학습 데이터 저장 (UPSERT)
            # final_json 구조: {"lines": [...], "doctyp": "...", "modify_reason": "..."}
            # - lines      : 분개 라인 목록 (RAG 패턴 매칭 시 참조)
            # - modify_reason: 수정 사유 (특허 P26LX017 — 수정 의도 데이터 / 능동 학습 가중치 근거)
            # - doctyp     : 전표 유형 (패턴 매칭 시 전표유형 추론 보조)
            if vec_str:
                learn_data = {
                    "lines":         [l.model_dump() for l in req.lines],
                    "doctyp":        req.doctyp,
                    "modify_reason": req.modify_reason or "",   # 수정 의도 — 빈 문자열은 AI 원본 사용
                }
                j = json.dumps(learn_data, ensure_ascii=False)
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
        
        # match_score가 전달된 경우 t_ai_log 최신 레코드에 반영
        if req.match_score is not None:
            try:
                db.execute(text("""
                    UPDATE t_ai_log SET match_score = :s
                    WHERE id = (
                        SELECT id FROM t_ai_log
                        WHERE comcd = :c AND user_id = :u AND raw_text = :r AND status = 'SUCCESS'
                        ORDER BY id DESC
                        LIMIT 1
                    )
                """), {
                    "s": req.match_score,
                    "c": comcd,
                    "u": current_user["userid"],
                    "r": req.raw_text,
                })
                db.commit()
            except Exception:
                db.rollback()

        return {"status": "success", "slipno": slipno}
    except HTTPException:
        # 마스터 검증 실패(400) 등 의도된 HTTP 에러는 그대로 상위로 전파
        db.rollback()
        raise
    except Exception as e:
        # 예상치 못한 서버 오류 — 열려 있을 수 있는 트랜잭션을 안전하게 롤백
        db.rollback()
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

@router.post("/api/validate-master")
async def validate_master(req: ValidateMasterRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """comcd는 JWT에서 추출 — 프론트에서 전달 불필요. type/value만 검증."""
    comcd = current_user["comcd"]
    _QUERIES = {
        "glmaster": text("SELECT 1 FROM t_cglmst  WHERE comcd=:c AND glmaster=:v LIMIT 1"),
        "pctr":     text("SELECT 1 FROM t_cprocos WHERE comcd=:c AND pctrcd=:v  LIMIT 1"),
        "anakey":   text("SELECT 1 FROM t_mbkey   WHERE comcd=:c AND manaky=:v  LIMIT 1"),
    }
    if req.type not in _QUERIES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 검증 유형입니다: {req.type}")
    row = db.execute(_QUERIES[req.type], {"c": comcd, "v": req.value}).fetchone()
    return {"exists": bool(row)}

@router.get("/api/search/{search_type}")
async def master_search(search_type: str, q: str = "", db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    comcd = current_user["comcd"]
    qf = f"%{q}%"

    # glmaster: 계정과목 전체가 소규모 마스터이므로 검색어 없을 때 전체 조회,
    #           검색어 있을 때도 LIMIT 500으로 여유 있게 반환 (LIMIT 50에서 변경)
    if search_type == "glmaster":
        if q.strip():
            # 검색어 있음: 코드 또는 계정명 ILIKE 검색 (대소문자 무시)
            sql = """
                SELECT glmaster, glname1, gltype
                FROM t_cglmst
                WHERE comcd = :c
                  AND use_yn = 'Y'
                  AND (glmaster ILIKE :q OR glname1 ILIKE :q)
                ORDER BY glmaster ASC
                LIMIT 500
            """
        else:
            # 검색어 없음: 전체 계정 조회 (LIMIT 없음 — 마스터 특성상 수백 건 이하)
            sql = """
                SELECT glmaster, glname1, gltype
                FROM t_cglmst
                WHERE comcd = :c
                  AND use_yn = 'Y'
                ORDER BY glmaster ASC
            """
        rows = db.execute(text(sql), {"c": comcd, "q": qf}).fetchall()
        return [{"code": r[0], "name": r[1], "type": r[2]} for r in rows]

    # 나머지 마스터: 기존 LIMIT 100으로 상향 (bizpt 등은 대규모 가능)
    sql_map = {
        "bizpt":   "SELECT bizptcd, bizname1, '' FROM t_cbizpt  WHERE comcd = :c AND (bizptcd  ILIKE :q OR bizname1 ILIKE :q) ORDER BY bizname1 LIMIT 100",
        # type 컬럼 = 'txgubun|taxtyp|taxrate' 형식으로 합산 전달
        # · txgubun : 세금구분 코드 (1=일반, D=불공제, 2=영세, 3=면세)  — t_ntxkey
        # · taxtyp  : AR/AP 방향 구분 ('S'=매출, 'P'=매입, 'D'=불공제매입) — t_ctxkey
        # · taxrate : 실제 세율 숫자 (0, 10 등) — t_ctxkey
        # 프론트에서 split('|')로 파싱하여 각각 사용
        "taxkey":  "SELECT c.taxcd, c.taxnm, COALESCE(n.txgubun, '') || '|' || COALESCE(c.taxtyp, '') || '|' || COALESCE(c.taxrate, 0) FROM t_ctxkey c LEFT JOIN t_ntxkey n ON c.taxcd = n.taxcd WHERE c.comcd = :c AND (c.taxcd ILIKE :q OR c.taxnm ILIKE :q) ORDER BY c.taxcd LIMIT 100",
        "pctr":    "SELECT pctrcd,  prcrnm,  '' FROM t_cprocos  WHERE comcd = :c AND (pctrcd   ILIKE :q OR prcrnm   ILIKE :q) ORDER BY prcrnm   LIMIT 100",
        "anakey":  "SELECT manaky,  mananm,  '' FROM t_mbkey    WHERE comcd = :c AND (manaky   ILIKE :q OR mananm   ILIKE :q) ORDER BY mananm   LIMIT 100",
    }
    query = sql_map.get(search_type)
    if not query:
        return []
    rows = db.execute(text(query), {"c": comcd, "q": qf}).fetchall()
    return [{"code": r[0], "name": r[1], "type": r[2]} for r in rows]