import { Router } from 'express';
import { sendOtp, verifyOtp, maskEmail, maskAadhaar } from '../services/otp.service.js';
import { supabase, getSupabaseClient } from '../services/supabase.service.js';

const router = Router();

// POST /api/application/send-otp
router.post('/application/send-otp', (req, res, next) => {
  try {
    const { email, phone, aadhaar } = req.body;
    const result = sendOtp(email, phone, aadhaar);
    res.json(result);
  } catch (err) {
    res.status(err.status || 400).json({ detail: err.message });
  }
});

// POST /api/application/verify-otp
router.post('/application/verify-otp', (req, res, next) => {
  try {
    const { email, otp } = req.body;
    const result = verifyOtp(email, otp);
    res.json(result);
  } catch (err) {
    res.status(err.status || 400).json({ detail: err.message });
  }
});

// Handler for Borrower Registration
async function handleBorrowerRegistration(req, res) {
  try {
    const {
      full_name,
      email,
      mobile_number,
      state,
      city,
      aadhaar_number,
      password,
      confirm_password
    } = req.body;

    if (!full_name || !email || !mobile_number || !state || !city || !password) {
      return res.status(422).json({
        detail: 'All registration fields (Full Name, Email, Mobile, State, City, Password) are required.'
      });
    }

    if (password.length < 8) {
      return res.status(422).json({
        detail: 'Password must be at least 8 characters in length.'
      });
    }

    if (password !== confirm_password) {
      return res.status(422).json({
        detail: 'Passwords do not match.'
      });
    }

    const cleanMobile = String(mobile_number).replace(/[\s\-\+]/g, '').slice(-10);
    if (!/^[6-9]\d{9}$/.test(cleanMobile)) {
      return res.status(422).json({
        detail: 'Mobile number must be a valid 10-digit Indian mobile number.'
      });
    }

    let cleanAadhaar = null;
    if (aadhaar_number && String(aadhaar_number).trim()) {
      cleanAadhaar = String(aadhaar_number).replace(/[\s\-]/g, '');
      if (!/^\d{12}$/.test(cleanAadhaar)) {
        return res.status(422).json({
          detail: 'Aadhaar number must be exactly 12 numeric digits.'
        });
      }
    }

    const { data, error } = await supabase.auth.signUp({
      email: email.trim().toLowerCase(),
      password,
      options: {
        data: {
          full_name: full_name.trim(),
          mobile_number: cleanMobile,
          phone: cleanMobile,
          state: state.trim(),
          city: city.trim(),
          aadhaar_number: cleanAadhaar,
          role: 'borrower'
        }
      }
    });

    if (error) {
      return res.status(400).json({
        success: false,
        detail: error.message
      });
    }

    res.status(201).json({
      success: true,
      message: 'Borrower registered successfully.',
      user_id: data?.user?.id || null,
      email: email.trim().toLowerCase(),
      full_name: full_name.trim(),
      state: state.trim(),
      city: city.trim(),
      masked_aadhaar: cleanAadhaar ? maskAadhaar(cleanAadhaar) : null,
      is_active: true
    });
  } catch (err) {
    res.status(500).json({ detail: err.message });
  }
}

// POST /api/auth/register-borrower & POST /api/borrowers/register
router.post('/auth/register-borrower', handleBorrowerRegistration);
router.post('/borrowers/register', handleBorrowerRegistration);

// Handler for Lender Registration
async function handleLenderRegistration(req, res) {
  try {
    const {
      full_name,
      email,
      mobile_number,
      organization_name,
      lender_type,
      license_number,
      password,
      confirm_password
    } = req.body;

    if (!full_name || !email || !organization_name || !password) {
      return res.status(422).json({
        detail: 'Full Name, Email, Organization, and Password are required.'
      });
    }

    if (password !== confirm_password) {
      return res.status(422).json({
        detail: 'Passwords do not match.'
      });
    }

    const { data, error } = await supabase.auth.signUp({
      email: email.trim().toLowerCase(),
      password,
      options: {
        data: {
          full_name: full_name.trim(),
          organization_name: organization_name.trim(),
          lender_type: lender_type || 'Bank',
          license_number: license_number || '',
          phone: mobile_number || '',
          role: 'lender'
        }
      }
    });

    if (error) {
      return res.status(400).json({
        success: false,
        detail: error.message
      });
    }

    res.status(201).json({
      success: true,
      message: 'Lender registered successfully.',
      user_id: data?.user?.id || null,
      email: email.trim().toLowerCase(),
      full_name: full_name.trim(),
      organization_name: organization_name.trim()
    });
  } catch (err) {
    res.status(500).json({ detail: err.message });
  }
}

// POST /api/auth/register-lender & POST /api/lenders/register
router.post('/auth/register-lender', handleLenderRegistration);
router.post('/lenders/register', handleLenderRegistration);

// GET /api/borrowers/:id
router.get('/borrowers/:id', async (req, res) => {
  const userToken = req.headers.authorization?.startsWith('Bearer ')
    ? req.headers.authorization.split(' ')[1]
    : null;
  const client = getSupabaseClient(userToken);

  const { data, error } = await client
    .from('borrower_profiles')
    .select('*')
    .eq('id', req.params.id)
    .limit(1);

  if (error || !data || data.length === 0) {
    return res.status(404).json({ detail: `Borrower profile '${req.params.id}' not found.` });
  }
  res.json({ success: true, borrower: data[0] });
});

// GET /api/lenders/:id
router.get('/lenders/:id', async (req, res) => {
  const userToken = req.headers.authorization?.startsWith('Bearer ')
    ? req.headers.authorization.split(' ')[1]
    : null;
  const client = getSupabaseClient(userToken);

  const { data, error } = await client
    .from('lender_profiles')
    .select('*')
    .eq('id', req.params.id)
    .limit(1);

  if (error || !data || data.length === 0) {
    return res.status(404).json({ detail: `Lender profile '${req.params.id}' not found.` });
  }
  res.json({ success: true, lender: data[0] });
});

export default router;
