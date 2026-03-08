INSERT INTO t_nbank (bctry, bankcd, bnknm, swift, address) 
VALUES ('KR', '088', '신한은행', 'SHBXKRSE', '서울특별시 중구 세종대로 9길 20');

INSERT INTO t_nbank (bctry, bankcd, bnknm, swift, address) 
VALUES ('KR', '004', 'KB국민은행', 'CZNBKRSE', '서울특별시 영등포구 국제금융로 8길 26');

-- 확인
SELECT * FROM t_nbank ORDER BY bankcd;