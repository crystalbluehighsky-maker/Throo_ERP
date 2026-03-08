INSERT INTO t_cobank (comcd, bctry, bankcd, branch, brchnm, bookid, booknm, acctno, curren, gl,moacct)
VALUES (
    '1091264100', 'KR', '088', '서초지점', '운영자금통장', 
    'B01', '보통예금-신한', '110-123-456789', 'KRW', '10101','N'
);

INSERT INTO t_cobank (comcd, bctry, bankcd, branch, brchnm, bookid, booknm, acctno, curren, gl,moacct)
VALUES (
    '1091264100', 'KR', '088', '서초지점', '예금통장', 
    'B02', '장기예금통장-신한', '110-123-456789', 'KRW', '10101','X'
);

-- 확인
SELECT * FROM t_cobank;