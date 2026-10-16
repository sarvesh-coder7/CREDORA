import os
import sys
from pathlib import Path
import hashlib
import hmac
import io

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient
from backend.main import app, OTP_STORE

client = TestClient(app)

def test_full_backend_flow():
    # 1. Health check
    res = client.get('/api/health')
    print('1. Health check:', res.status_code, res.json())
    assert res.status_code == 200

    # 2. Send OTP
    email = 'borrower.test@example.com'
    send_res = client.post('/api/application/send-otp', json={
        'email': email,
        'phone': '9876543210',
        'aadhaar': '123456789012'
    })
    print('2. Send OTP status:', send_res.status_code, send_res.json())
    assert send_res.status_code == 200

    # Retrieve OTP from in-memory store
    record = OTP_STORE.get(email)
    assert record is not None, "OTP record not created"

    # 3. Test wrong OTP
    bad_res = client.post('/api/application/verify-otp', json={'email': email, 'otp': '000000'})
    print('3. Bad OTP check (should be 400):', bad_res.status_code)
    assert bad_res.status_code == 400

    # 4. Verify valid OTP
    found_otp = None
    for candidate in range(100000, 1000000):
        cand_str = str(candidate)
        h = hashlib.sha256(f"{cand_str}:{record['salt']}".encode('utf-8')).hexdigest()
        if hmac.compare_digest(record['hash'], h):
            found_otp = cand_str
            break

    print('4. Found valid OTP:', found_otp)
    verify_res = client.post('/api/application/verify-otp', json={'email': email, 'otp': found_otp})
    print('   Verify OTP status:', verify_res.status_code, verify_res.json())
    assert verify_res.status_code == 200
    token = verify_res.json().get('verificationToken')
    assert token

    # 5. Test AI Document Analysis endpoint
    dummy_doc = io.BytesIO(b'MONTHLY SALARY SLIP: Basic: INR 50,000. Net Pay: INR 45,000. Employer: Tech Corp. PAN: ABCDE1234F')
    files = [('documents[salary]', ('salary_slip.txt', dummy_doc, 'text/plain'))]
    data = {
        'loanType': 'Personal Loan',
        'lenderType': 'Bank',
        'fullName': 'Test Borrower',
        'income': '45000',
        'loanAmount': '200000',
        'emis': '5000',
        'applicationVerificationToken': token
    }
    analyze_res = client.post('/api/documents/analyze', data=data, files=files)
    print('5. Analyze status:', analyze_res.status_code)
    summary = analyze_res.json().get('summary') or ''
    ascii_summary = summary.encode('ascii', errors='replace').decode('ascii')
    print('   Summary preview:', ascii_summary[:150])

    # 6. Test Application Submission
    submit_doc = io.BytesIO(b'AADHAAR CARD: 1234 5678 9012. Name: Test Borrower. DOB: 01/01/1990.')
    sub_files = [('documents[identity]', ('aadhaar_card.txt', submit_doc, 'text/plain'))]
    sub_data = {
        'fullName': 'Test Borrower',
        'email': email,
        'phone': '9876543210',
        'lenderType': 'Bank',
        'loanType': 'Home Loan',
        'employment': 'Salaried',
        'income': '75000',
        'loanAmount': '1500000',
        'emis': '10000',
        'tenure': '5-10 years',
        'purpose': 'Purchasing residential property in Chennai',
        'applicationVerificationToken': token
    }
    sub_res = client.post('/api/applications', data=sub_data, files=sub_files)
    print('6. Application Submission status:', sub_res.status_code, 'App ID:', sub_res.json().get('applicationId'))
    assert sub_res.status_code == 200
    app_id = sub_res.json().get('applicationId')
    assert app_id

    # 7. Test Application Tracking
    track_res = client.get(f'/api/applications/{app_id}')
    print('7. Application Tracking status:', track_res.status_code)
    assert track_res.status_code == 200
    app_record = track_res.json().get('application')
    print('   Retrieved application ID:', app_record.get('id'), 'Status:', app_record.get('status'))
    assert app_record.get('id') == app_id

    print('\n>>> ALL BACKEND FLOW & SUBMISSION & TRACKING TESTS PASSED! <<<')

if __name__ == '__main__':
    test_full_backend_flow()
