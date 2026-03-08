	T_CBODY_O		customer AR 보조부_미결데이터		
Key여부	Field	Field name	Type	길이 	샘플
PK	comcd	회사코드	varchar2	10	1458703198
PK	custcd	customer 코드	varchar2	10	
PK	mulky	multi key	char	1	D:down pay. 추가로 더 설정
PK	clrdt	clearing date	date	8	
PK	clrdoc	clearing 전표번호	varchar2	10	
PK	fisyr	회계기간	number	4	*원전표번호 회계연도
PK	docno	전표번호	varchar2	10	*원전표번호
PK	lineno	라인아이템번호	number	4	0001
	docty	전표종류	char	2	GL: General Doc
	invdt	증빙일자	date	8	20250630
	posdt	전기일자	date	8	20250630
	nodate	정규전표생성일	date	8	20250701
	reftx	참조내역	varchar2	20	
	pclrdoc	부분반제전표번호	varchar2	10	
	pclryr	부분반제년도	number	4	
	pclrlin	부분반제라인	number	4	0001
	curren	통화	varchar2	5	KRW
	bookey	장부키	char	2	C1:AR, C3: AR credit, 
	prjno	프로젝트 번호	varchar2	30	프로젝트번호
	debcre	차대구분 지시자	char	1	D : Debit(차변), C : Credit(대변)
	glmaster	GL계정코드	num	6	111111
	taxcd	Tax code	char	2	
	pmethod	지급방법			
	pblck	지불보류	char	1	A (지불보류란 의미)
	pterm	지급조건	char	4	1000(즉시지급),1100: 30일
	basedt	기산일	date	8	기산일:만기일계산위산 시작일
	dueday	일수	num	3	
	duedt	만기일	date	8	
	bizamt	거래통화금액	number	25	소수점 2자리 포함
	locamt	Local 금액	number	25	소수점 2자리 포함
	biztax	base tax금액	number	25	소수점 2자리 포함
	loctax	Local base tax 금액	number	25	소수점 2자리 포함
	pbank	지급bank key	num	4	
	bizcat	사업구분	char	4	설정나름,  1000:재무팀,2000:영업1팀등. 손익부서등에 의해 영향을 받을것임
	pctrcd	손익부서코드	varchar2	10	손익부서
	cctrcd	비용부서 코드	varchar2	10	비용부서
