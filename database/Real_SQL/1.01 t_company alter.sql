-- 1. t_company: 시스템 숙성도(Maturity) 계산을 위한 가입일 추가
ALTER TABLE t_company 
ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;