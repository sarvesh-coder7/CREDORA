import http from 'http';
import { createApp } from '../src/app.js';
import { OTP_STORE } from '../src/services/otp.service.js';
import crypto from 'crypto';

const app = createApp();

let server;
const PORT = 8999;
const BASE_URL = `http://127.0.0.1:${PORT}`;

async function runTests() {
  console.log('=== RUNNING CREDORA SERVER.JS INTEGRATION TEST SUITE ===\n');

  server = app.listen(PORT, '127.0.0.1');
  await new Promise(r => setTimeout(r, 200));

  let totalPassed = 0;
  let totalTests = 0;

  function assert(condition, testName, details = '') {
    totalTests++;
    if (condition) {
      totalPassed++;
      console.log(`[PASS] ${testName}`);
    } else {
      console.error(`[FAIL] ${testName} - ${details}`);
    }
  }

  try {
    // 1. Health check
    const healthRes = await fetch(`${BASE_URL}/api/health`);
    const healthData = await healthRes.json();
    assert(healthRes.status === 200 && healthData.status === 'healthy', '1. GET /api/health returns healthy', JSON.stringify(healthData));

    // 2. Geo States
    const statesRes = await fetch(`${BASE_URL}/api/geo/states`);
    const statesData = await statesRes.json();
    assert(statesRes.status === 200 && Array.isArray(statesData) && statesData.includes('Maharashtra'), '2. GET /api/geo/states returns Indian states');

    // 3. Geo Cities
    const citiesRes = await fetch(`${BASE_URL}/api/geo/cities?state=Maharashtra`);
    const citiesData = await citiesRes.json();
    assert(citiesRes.status === 200 && Array.isArray(citiesData) && citiesData.includes('Mumbai'), '3. GET /api/geo/cities returns cities for state');

    // 4. Send OTP
    const testEmail = `borrower.test.${Date.now()}@example.com`;
    const sendOtpRes = await fetch(`${BASE_URL}/api/application/send-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: testEmail,
        phone: '9876543210',
        aadhaar: '123456789012'
      })
    });
    const sendOtpData = await sendOtpRes.json();
    assert(sendOtpRes.status === 200 && sendOtpData.success === true, '4. POST /api/application/send-otp succeeds', JSON.stringify(sendOtpData));

    // 5. Test Invalid OTP
    const badOtpRes = await fetch(`${BASE_URL}/api/application/verify-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: testEmail,
        otp: '000000'
      })
    });
    assert(badOtpRes.status === 400, '5. POST /api/application/verify-otp rejects invalid code');

    // 6. Verify Valid OTP
    const otpRecord = OTP_STORE.get(testEmail);
    assert(Boolean(otpRecord), '6a. In-memory OTP record created securely');

    let validOtp = null;
    for (let c = 100000; c < 1000000; c++) {
      const cand = String(c);
      const h = crypto.createHash('sha256').update(`${cand}:${otpRecord.salt}`).digest('hex');
      if (h === otpRecord.hash) {
        validOtp = cand;
        break;
      }
    }

    const verifyRes = await fetch(`${BASE_URL}/api/application/verify-otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: testEmail,
        otp: validOtp
      })
    });
    const verifyData = await verifyRes.json();
    assert(verifyRes.status === 200 && verifyData.verificationToken, '6b. POST /api/application/verify-otp issues cryptographic token', JSON.stringify(verifyData));
    const token = verifyData.verificationToken;

    // 7. Analyze Documents Endpoint
    const formData = new FormData();
    formData.append('loanType', 'Personal Loan');
    formData.append('lenderType', 'Bank');
    formData.append('fullName', 'Sarvesh Borrower');
    formData.append('income', '60000');
    formData.append('loanAmount', '250000');
    formData.append('emis', '4500');
    formData.append('applicationVerificationToken', token);

    const dummySlip = new Blob(['MONTHLY PAYSLIP: Gross: INR 65,000. Net Salary: INR 60,000. Org: Tech Ltd. PAN: ABCDE1234F'], { type: 'text/plain' });
    formData.append('documents[salary]', dummySlip, 'salary_slip.txt');

    const analyzeRes = await fetch(`${BASE_URL}/api/documents/analyze`, {
      method: 'POST',
      body: formData
    });
    const analyzeData = await analyzeRes.json();
    assert(analyzeRes.status === 200 && analyzeData.success === true && analyzeData.summary, '7. POST /api/documents/analyze processes document and computes financial review', JSON.stringify(analyzeData).slice(0, 100));

    // 8. Submit Application
    const submitForm = new FormData();
    submitForm.append('fullName', 'Sarvesh Borrower');
    submitForm.append('email', testEmail);
    submitForm.append('phone', '9876543210');
    submitForm.append('lenderType', 'Bank');
    submitForm.append('loanType', 'Personal Loan');
    submitForm.append('employment', 'Salaried');
    submitForm.append('purpose', 'Home renovation');
    submitForm.append('income', '60000');
    submitForm.append('loanAmount', '250000');
    submitForm.append('emis', '4500');
    submitForm.append('applicationVerificationToken', token);

    const docBlob = new Blob(['AADHAAR CARD: 1234 5678 9012. Name: Sarvesh Borrower'], { type: 'text/plain' });
    submitForm.append('documents[identity]', docBlob, 'aadhaar.txt');

    const subRes = await fetch(`${BASE_URL}/api/applications`, {
      method: 'POST',
      body: submitForm
    });
    const subData = await subRes.json();
    assert(subRes.status === 200 && subData.success === true && subData.applicationId, '8. POST /api/applications records submission', JSON.stringify(subData));
    const createdAppId = subData.applicationId;

    // 9. Track Application Details
    if (createdAppId) {
      const getRes = await fetch(`${BASE_URL}/api/applications/${createdAppId}`);
      const getData = await getRes.json();
      assert(getRes.status === 200 && getData.success === true, '9. GET /api/applications/:id retrieves submitted record');
    }

    // 10. List Applications
    const listRes = await fetch(`${BASE_URL}/api/applications`);
    const listData = await listRes.json();
    assert(listRes.status === 200 && listData.success === true && Array.isArray(listData.applications), '10. GET /api/applications lists application records');

    console.log(`\n========================================`);
    console.log(`TEST RESULTS: ${totalPassed} / ${totalTests} PASSED`);
    console.log(`========================================\n`);

  } catch (err) {
    console.error('[Test Error]', err);
  } finally {
    server.close();
    process.exit(totalPassed === totalTests ? 0 : 1);
  }
}

runTests();
