import { Router } from 'express';
import crypto from 'crypto';
import multer from 'multer';
import { verifyVerificationToken } from '../services/otp.service.js';
import {
  resolveOrCreateBorrowerProfile,
  uploadToSupabaseStorage,
  insertLoanApplication,
  insertBorrowerDocument,
  insertLoanAssessment,
  listApplications,
  getApplicationById,
  updateApplicationStatus
} from '../services/supabase.service.js';

const router = Router();
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 20 * 1024 * 1024, files: 10 }
});

// POST /api/applications
router.post('/applications', upload.any(), async (req, res) => {
  try {
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    const token = String(req.body.applicationVerificationToken || '').trim();
    let verifiedMeta = {};
    if (token) {
      verifiedMeta = verifyVerificationToken(token) || {};
    }

    const fullName = String(req.body.fullName || req.body.applicant_name || '').trim();
    const email = String(req.body.email || verifiedMeta.email || '').trim().toLowerCase();
    const phone = String(req.body.phone || verifiedMeta.phone || '').trim();
    const lenderType = String(req.body.lenderType || 'Bank').trim();
    const loanType = String(req.body.loanType || 'Personal Loan').trim();
    const employment = String(req.body.employment || 'Salaried').trim();
    const purpose = String(req.body.purpose || 'Loan requirement').trim();
    const tenure = String(req.body.tenure || '3-5 years').trim();

    const income = parseFloat(req.body.income || req.body.monthly_income) || 0;
    const loanAmount = parseFloat(req.body.loanAmount || req.body.requested_loan_amount) || 0;
    const emis = parseFloat(req.body.emis || req.body.existing_monthly_obligations) || 0;

    if (!fullName || !email || loanAmount <= 0) {
      return res.status(422).json({
        detail: 'Full name, valid email, and loan amount (> 0) are required.'
      });
    }

    // Resolve or create borrower profile record
    const borrowerId = await resolveOrCreateBorrowerProfile(email, fullName, phone, userToken);
    const appId = crypto.randomUUID();

    // 1. Insert into public.loan_applications
    const appPayload = {
      id: appId,
      borrower_id: borrowerId,
      loan_amount: loanAmount,
      loan_purpose: purpose,
      monthly_income: income,
      employment_type: employment,
      existing_monthly_debt: emis,
      loan_type: loanType,
      lender_type: lenderType,
      tenure,
      applicant_name: fullName,
      applicant_email: email,
      applicant_phone: phone,
      status: 'submitted'
    };

    await insertLoanApplication(appPayload, userToken);

    // 2. Upload supporting documents
    const uploadedDocs = [];
    const files = req.files || [];
    for (const file of files) {
      const cleanFilename = (file.originalname || 'document.dat').replace(/[^\w.-]/g, '_');
      const storagePath = `${borrowerId}/${appId}/${cleanFilename}`;
      const docType = file.fieldname && file.fieldname.includes('[')
        ? file.fieldname.split('[')[1].split(']')[0]
        : 'general';

      await uploadToSupabaseStorage(
        'borrower-documents',
        storagePath,
        file.buffer,
        file.mimetype,
        userToken
      );

      const docId = crypto.randomUUID();
      await insertBorrowerDocument(
        {
          id: docId,
          application_id: appId,
          uploaded_by: borrowerId,
          document_type: docType,
          file_name: cleanFilename,
          storage_path: storagePath,
          mime_type: file.mimetype || 'application/octet-stream',
          file_size: file.buffer?.length || 0,
          verification_status: 'pending'
        },
        userToken
      );

      uploadedDocs.push({
        name: cleanFilename,
        type: docType,
        size: file.buffer?.length || 0
      });
    }

    // 3. Generate initial assessment summary
    const summaryText =
      `Application ${appId.slice(0, 8).toUpperCase()} submitted by ${fullName} for a ${loanType} of ₹${loanAmount.toLocaleString('en-IN', { minimumFractionDigits: 2 })}. ` +
      `Selected Institution Type: ${lenderType}. Supporting documents: ${uploadedDocs.length} item(s) uploaded. ` +
      `Status: Submitted and queued for initial lender assessment.`;

    const assessmentPayload = {
      id: crypto.randomUUID(),
      application_id: appId,
      model_version: 'gemini-2.0-flash',
      risk_score: 35.0,
      risk_level: 'low',
      assessment_summary: summaryText,
      extracted_data: {
        applicant: fullName,
        email,
        phone,
        loan_type: loanType,
        lender_type: lenderType,
        documents_count: uploadedDocs.length
      }
    };

    await insertLoanAssessment(assessmentPayload, userToken);

    res.status(200).json({
      success: true,
      applicationId: appId,
      referenceId: `CR-${appId.slice(0, 8).toUpperCase()}`,
      status: 'submitted',
      applicant: fullName,
      loanType,
      loanAmount,
      documentsCount: uploadedDocs.length,
      summary: summaryText,
      message: 'Loan application and supporting documents submitted successfully.'
    });
  } catch (err) {
    console.error('[Applications Route] Error:', err);
    res.status(500).json({ detail: err.message || 'Error submitting application.' });
  }
});

// GET /api/applications
router.get('/applications', async (req, res) => {
  try {
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    const email = req.query.email ? String(req.query.email) : null;
    const statusFilter = req.query.status ? String(req.query.status) : null;

    const apps = await listApplications({ email, statusFilter, userToken });
    res.json({ success: true, applications: apps });
  } catch (err) {
    res.status(500).json({ success: false, applications: [], detail: err.message });
  }
});

// GET /api/applications/:id
router.get('/applications/:id', async (req, res) => {
  try {
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    const app = await getApplicationById(req.params.id, userToken);
    if (!app) {
      return res.status(404).json({ detail: `Loan application '${req.params.id}' not found.` });
    }
    res.json({ success: true, application: app });
  } catch (err) {
    res.status(500).json({ detail: err.message });
  }
});

// PATCH /api/applications/:id/status
router.patch('/applications/:id/status', async (req, res) => {
  try {
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    const validStatuses = new Set([
      'draft',
      'submitted',
      'under_review',
      'assessment_ready',
      'approved',
      'rejected',
      'more_information_required'
    ]);

    const newStatus = String(req.query.status || req.body.status || '').trim().toLowerCase();
    if (!validStatuses.has(newStatus)) {
      return res.status(400).json({
        detail: `Status '${newStatus}' is invalid. Allowed: ${Array.from(validStatuses).sort().join(', ')}`
      });
    }

    const updated = await updateApplicationStatus(req.params.id, newStatus, userToken);
    res.json({ success: true, application_id: req.params.id, status: newStatus });
  } catch (err) {
    res.status(500).json({ detail: err.message });
  }
});

export default router;
