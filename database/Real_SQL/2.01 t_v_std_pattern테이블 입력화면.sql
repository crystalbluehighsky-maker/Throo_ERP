<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>다봄 AI 지식 빌더</title>
    <style>
        body { font-family: 'Malgun Gothic', sans-serif; background: #f4f7f6; padding: 40px; }
        .box { max-width: 900px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        h2 { color: #333; border-left: 5px solid #4CAF50; padding-left: 15px; }
        input, select, textarea { width: 100%; padding: 12px; margin: 8px 0; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #eee; padding: 12px; text-align: center; }
        th { background: #f8f9fa; }
        .btn { padding: 12px 24px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
        .btn-add { background: #2196F3; color: white; float: right; margin-bottom: 10px; }
        .btn-save { background: #4CAF50; color: white; width: 100%; font-size: 18px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="box">
        <h2>🧠 다봄 AI 지식 학습 데이터 입력</h2>
        <input type="text" id="pattern_nm" placeholder="패턴 명칭 (예: 외상매출금 회수)">
        <textarea id="example_tx" rows="3" placeholder="사용자가 입력할 자연어 문장 예시를 적어주세요."></textarea>
        <select id="docty">
            <option value="GL">일반전표 (GL)</option>
            <option value="CI">매출전표 (CI)</option>
            <option value="SI">매입전표 (SI)</option>
        </select>

        <h3>📝 하단 분개(Journal Entry) 설정</h3>
        <button class="btn btn-add" onclick="addRow()">+ 줄 추가</button>
        <table id="jTable">
            <thead>
                <tr>
                    <th>차/대</th><th>계정코드</th><th>계정명</th><th>금액</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><select class="side"><option value="D">차변</option><option value="C">대변</option></select></td>
                    <td><input type="text" class="acct"></td>
                    <td><input type="text" class="acct_nm"></td>
                    <td><input type="number" class="amt" value="0"></td>
                </tr>
            </tbody>
        </table>
        <button class="btn btn-save" onclick="sendData()">DB에 지식 저장하기</button>
    </div>

    <script>
        function addRow() {
            const tr = `<tr>
                <td><select class="side"><option value="D">차변</option><option value="C">대변</option></select></td>
                <td><input type="text" class="acct"></td>
                <td><input type="text" class="acct_nm"></td>
                <td><input type="number" class="amt" value="0"></td>
            </tr>`;
            document.querySelector('#jTable tbody').insertAdjacentHTML('beforeend', tr);
        }

        async function sendData() {
            const rows = document.querySelectorAll('#jTable tbody tr');
            const journal = Array.from(rows).map(r => ({
                side: r.querySelector('.side').value,
                acct: r.querySelector('.acct').value,
                acct_nm: r.querySelector('.acct_nm').value,
                amt: r.querySelector('.amt').value
            }));

            const payload = {
                pattern_nm: document.getElementById('pattern_nm').value,
                example_tx: document.getElementById('example_tx').value,
                docty: document.getElementById('docty').value,
                journal: journal
            };

            const res = await fetch('/api/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const result = await res.json();
            alert(result.message);
            if(result.status === 'success') location.reload();
        }
    </script>
</body>
</html>