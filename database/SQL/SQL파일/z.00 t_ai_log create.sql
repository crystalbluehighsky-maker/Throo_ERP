1. t_ai_log 테이블 설계 가이드
log_id: 로그를 식별하는 기본키.
raw_text: 사용자가 입력한 자연어 원문 (분석의 시작점).
ai_json: AI가 추출한 구조화된 데이터 (JSONB 타입을 사용하여 검색 효율 증대).
final_slip_no: 실제 전표로 저장되었을 경우 연결되는 전표 번호 (추적용).
is_modified: 사용자가 AI 결과값을 수정했는지 여부 (AI 성능 측정 지표).

2.왜 이렇게 설계했나요? (전문가 코멘트)
JSONB 타입 활용: * PostgreSQL의 JSONB는 단순 텍스트보다 저장 공간은 조금 더 차지하지만, JSON 내부의 특정 필드(예: 특정 거래처명)를 조건으로 검색할 때 압도적으로 빠릅니다. 나중에 AI 모델 개선을 위해 특정 패턴을 추출하기 매우 좋습니다.

is_modified 필드: * 이 필드는 시스템 운영의 핵심 지표가 됩니다. 예를 들어, "최근 한 달간 AI 전표 입력의 90%가 수정 없이 통과됨"이라는 데이터를 얻으면 시스템의 신뢰성을 증명할 수 있습니다.

final_slip_no 연결: * 나중에 전표 조회 화면에서 "이 전표를 만든 원본 AI 입력 문장이 뭐였지?"라고 물었을 때 바로 역추적하여 보여줄 수 있습니다.


-- AI 분석 로그 테이블 생성
CREATE TABLE t_ai_log (
    log_id          BIGSERIAL PRIMARY KEY,           -- 로그 고유 ID (자동 증가)
    comcd           VARCHAR(10) NOT NULL,            -- 회사 코드
    user_id         VARCHAR(20) NOT NULL,            -- 입력 사용자 ID
    input_at        TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, -- 입력 일시
    
    -- AI 입력 및 결과
    raw_text        TEXT NOT NULL,                   -- 사용자가 입력한 자연어 문장 원문
    ai_json         JSONB NOT NULL,                  -- AI가 분석한 JSON 결과값 (추출된 거래처, 금액 등)
    
    -- 실행 및 결과 추적
    is_modified     BOOLEAN DEFAULT FALSE,           -- 사용자가 AI 추천값을 수정했는지 여부
    final_slip_no   VARCHAR(20),                     -- 최종 저장된 전표번호 (성공 시 매핑)
    status          VARCHAR(10) DEFAULT 'SUCCESS',    -- 성공 여부 (SUCCESS, FAIL, CANCEL 등)
    error_msg       TEXT,                            -- 에러 발생 시 내용 기록
    
    -- 성능 최적화를 위한 인덱스
    CONSTRAINT fk_ai_log_company FOREIGN KEY (comcd) REFERENCES t_company(comcd)
);

-- 검색 속도를 위한 인덱스 설정
CREATE INDEX idx_ai_log_comcd_date ON t_ai_log (comcd, input_at);
CREATE INDEX idx_ai_log_json_data ON t_ai_log USING GIN (ai_json); -- JSON 내부 데이터 검색용