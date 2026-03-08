-- 패턴 이용 통계 테이블 (특허 로직의 Numerical Grounds 저장소)
CREATE TABLE t_v_pattern_stats (
    comcd           VARCHAR(10) NOT NULL, -- 회사 코드
    pattern_id      INTEGER NOT NULL,      -- t_v_std_pattern의 ID
    
    usage_count     INTEGER DEFAULT 0,     -- 확정 빈도 (Frequency)
    last_used_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, -- 최신성 (Recency) 기준일
    
    -- 회사별, 패턴별로 유일해야 함
    CONSTRAINT pk_t_v_pattern_stats PRIMARY KEY (comcd, pattern_id),
    CONSTRAINT fk_stats_company FOREIGN KEY (comcd) REFERENCES t_company(comcd),
    CONSTRAINT fk_stats_pattern FOREIGN KEY (pattern_id) REFERENCES t_v_std_pattern(id)
);

-- 주석 추가
COMMENT ON TABLE t_v_pattern_stats IS '회사별 분개 패턴 사용 빈도 및 최신성 통계 (특허 점수 산출용)';