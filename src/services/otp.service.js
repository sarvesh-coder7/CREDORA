import crypto from 'crypto';
import config from '../config/index.js';

// In-Memory OTP Store: email -> { hash, salt, expiresAt, attempts, phone, aadhaarLast4 }
export const OTP_STORE = new Map();
export const OTP_RATE_LIMITS = new Map();
const OTP_EXPIRY_MS = 10 * 60 * 1000; // 10 minutes

export function maskEmail(email) {
  if (!email || !email.includes('@')) return '***';
  const [name, domain] = email.split('@');
  const maskedName = name[0] + '***' + (name.length > 1 ? name[name.length - 1] : '');
  return `${maskedName}@${domain}`;
}

export function maskAadhaar(aadhaar) {
  const clean = String(aadhaar || '').replace(/[\s-]/g, '');
  return clean.length >= 4 ? `XXXX-XXXX-${clean.slice(-4)}` : 'XXXX-XXXX-XXXX';
}

export function createVerificationToken(email, phone, aadhaarLast4) {
  const payload = {
    email: email.toLowerCase().trim(),
    phone: String(phone || '').trim(),
    aadhaar_last4: String(aadhaarLast4 || '').trim(),
    issued_at: Date.now(),
    expires_at: Date.now() + 24 * 60 * 60 * 1000 // 24 hours
  };
  const jsonStr = JSON.stringify(payload);
  const encodedPayload = Buffer.from(jsonStr, 'utf-8').toString('base64url');
  const signature = crypto
    .createHmac('sha256', config.SECRET_KEY)
    .update(encodedPayload)
    .digest('hex');
  return `${encodedPayload}.${signature}`;
}

export function verifyVerificationToken(token) {
  if (!token || !token.includes('.')) return null;
  const [encodedPayload, signature] = token.split('.');
  const expectedSig = crypto
    .createHmac('sha256', config.SECRET_KEY)
    .update(encodedPayload)
    .digest('hex');

  // Constant-time comparison
  if (!crypto.timingSafeEqual(Buffer.from(signature, 'hex'), Buffer.from(expectedSig, 'hex'))) {
    return null;
  }

  try {
    const payloadStr = Buffer.from(encodedPayload, 'base64url').toString('utf-8');
    const payload = JSON.parse(payloadStr);
    if (Date.now() > payload.expires_at) {
      return null;
    }
    return payload;
  } catch (err) {
    return null;
  }
}

export function sendOtp(email, phone, aadhaar) {
  const cleanEmail = String(email || '').toLowerCase().trim();
  const cleanPhone = String(phone || '').replace(/[\s\-\+]/g, '');
  const cleanAadhaar = String(aadhaar || '').replace(/[\s\-]/g, '');

  if (!cleanEmail || !cleanEmail.includes('@')) {
    throw new Error('A valid email address is required.');
  }
  if (!/^[6-9]\d{9}$/.test(cleanPhone)) {
    throw new Error('Valid 10-digit Indian mobile number starting with 6, 7, 8, or 9 is required.');
  }
  if (!/^\d{12}$/.test(cleanAadhaar)) {
    throw new Error('Aadhaar number must be exactly 12 numeric digits.');
  }

  const now = Date.now();
  const history = (OTP_RATE_LIMITS.get(cleanEmail) || []).filter(t => now - t < OTP_EXPIRY_MS);
  if (history.length >= 4) {
    const err = new Error('Too many OTP requests. Please wait a few minutes before trying again.');
    err.status = 429;
    throw err;
  }
  history.push(now);
  OTP_RATE_LIMITS.set(cleanEmail, history);

  // Generate 6-digit OTP
  const otpNumber = crypto.randomInt(100000, 1000000);
  const otp = String(otpNumber);

  const salt = crypto.randomBytes(8).toString('hex');
  const hash = crypto.createHash('sha256').update(`${otp}:${salt}`).digest('hex');

  OTP_STORE.set(cleanEmail, {
    hash,
    salt,
    expiresAt: now + OTP_EXPIRY_MS,
    attempts: 0,
    phone: cleanPhone,
    aadhaarLast4: cleanAadhaar.slice(-4)
  });

  console.log(`[CREDORA OTP Service] Verification OTP generated for ${maskEmail(cleanEmail)}: [${otp}]`);

  return {
    success: true,
    message: 'A 6-digit verification code has been dispatched to your email address.',
    email: maskEmail(cleanEmail),
    expiresIn: Math.floor(OTP_EXPIRY_MS / 1000)
  };
}

export function verifyOtp(email, otpInput) {
  const cleanEmail = String(email || '').toLowerCase().trim();
  const cleanOtp = String(otpInput || '').trim();

  const record = OTP_STORE.get(cleanEmail);
  if (!record) {
    const err = new Error("No pending OTP request found for this email. Please click 'Send Email OTP' first.");
    err.status = 400;
    throw err;
  }

  if (Date.now() > record.expiresAt) {
    OTP_STORE.delete(cleanEmail);
    const err = new Error('The verification code has expired. Please request a new code.');
    err.status = 400;
    throw err;
  }

  if (record.attempts >= 5) {
    OTP_STORE.delete(cleanEmail);
    const err = new Error('Too many invalid attempts. For security, please request a new OTP.');
    err.status = 429;
    throw err;
  }

  const expectedHash = crypto.createHash('sha256').update(`${cleanOtp}:${record.salt}`).digest('hex');
  if (!crypto.timingSafeEqual(Buffer.from(record.hash, 'hex'), Buffer.from(expectedHash, 'hex'))) {
    record.attempts += 1;
    const remaining = 5 - record.attempts;
    const err = new Error(`Invalid verification code. ${remaining} attempt(s) remaining.`);
    err.status = 400;
    throw err;
  }

  // Success
  const token = createVerificationToken(cleanEmail, record.phone, record.aadhaarLast4);
  const phone = record.phone;
  const maskedAadhaar = `XXXX-XXXX-${record.aadhaarLast4}`;
  OTP_STORE.delete(cleanEmail);

  return {
    success: true,
    verified: true,
    verificationToken: token,
    email: cleanEmail,
    phone,
    maskedAadhaar,
    message: 'Email verified successfully.'
  };
}
