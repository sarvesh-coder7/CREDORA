import { Router } from 'express';
import crypto from 'crypto';
import multer from 'multer';
import { verifyVerificationToken } from '../services/otp.service.js';
import {
  resolveOrCreateBorrowerProfile,
  uploadToSupabaseStorage,
  insertLoanApplication,
  insertOrUpdateLoanApplication,
  insertBorrowerDocument,
  insertLoanAssessment,
  listApplications,
  getApplicationById,
  updateApplicationStatus,
  uploadLoanDocument,
  getApplicationLoanDocuments,
  downloadStorageFile,
  saveDocumentAnalysisReport,
  getDocumentAnalysisReport,
  saveReportPdf,
  getReportPdfBuffer
} from '../services/supabase.service.js';
import { analyzeDocuments } from '../services/ai.service.js';

const router = Router();
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024, files: 15 } // 10MB per file
});

const ALLOWED_MIME_TYPES = new Set([
  'application/pdf',
  'image/jpeg',
  'image/jpg',
  'image/png'
]);

const ALLOWED_EXTENSIONS = new Set(['.pdf', '.jpg', '.jpeg', '.png']);

function isValidFileType(filename, mimetype) {
  const ext = (filename.lastIndexOf('.') !== -1 ? filename.slice(filename.lastIndexOf('.')).toLowerCase() : '');
  const mime = (mimetype || '').toLowerCase();
  return ALLOWED_EXTENSIONS.has(ext) || ALLOWED_MIME_TYPES.has(mime);
}

// ─────────────────────────────────────────────────────────────────────────────
// POST /api/applications - Create or update loan application record
// ─────────────────────────────────────────────────────────────────────────────
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

    const fullName = String(req.body.fullName || req.body.applicant_name || req.body.full_name || '').trim();
    const email = String(req.body.email || req.body.applicant_email || verifiedMeta.email || '').trim().toLowerCase();
    const phone = String(req.body.phone || req.body.applicant_phone || verifiedMeta.phone || '').trim();
    const lenderType = String(req.body.lenderType || req.body.lender_type || 'Bank').trim();
    const loanType = String(req.body.loanType || req.body.loan_type || 'Personal Loan').trim();
    const employment = String(req.body.employment || req.body.employment_type || 'Salaried').trim();
    const purpose = String(req.body.purpose || req.body.loan_purpose || '').trim();
    const tenure = String(req.body.tenure || req.body.preferred_tenure || '3-5 years').trim();

    const income = parseFloat(req.body.income || req.body.monthly_income) || 0;
    const loanAmount = parseFloat(req.body.loanAmount || req.body.requested_loan_amount || req.body.loan_amount) || 0;
    const emis = parseFloat(req.body.emis || req.body.existing_monthly_obligations || req.body.existing_monthly_emis || req.body.existing_monthly_debt) || 0;

    if (!fullName || !email) {
      return res.status(422).json({
        detail: 'Full name and valid email are required to start an application.'
      });
    }

    // Resolve or create borrower profile record if token/email exists
    let borrowerId = null;
    try {
      borrowerId = await resolveOrCreateBorrowerProfile(email, fullName, phone, userToken);
    } catch {
      borrowerId = null;
    }

    // Reuse existing ID if provided from frontend draft state, or generate a fresh UUID
    const appId = req.body.id || req.body.applicationId || crypto.randomUUID();
    const appStatus = req.body.application_status || 'draft';

    const appPayload = {
      id: appId,
      borrower_id: borrowerId,
      full_name: fullName,
      email: email,
      phone: phone,
      lender_type: lenderType,
      loan_type: loanType,
      employment_type: employment,
      monthly_income: income,
      requested_loan_amount: loanAmount,
      existing_monthly_emis: emis,
      preferred_tenure: tenure,
      loan_purpose: purpose,
      application_status: appStatus,
      analysis_status: 'pending',
      // Legacy column compatibility
      applicant_name: fullName,
      applicant_email: email,
      applicant_phone: phone,
      loan_amount: loanAmount,
      existing_monthly_debt: emis,
      tenure: tenure,
      status: appStatus
    };

    const savedApp = await insertOrUpdateLoanApplication(appPayload, userToken);

    // If any files were attached directly with this creation call
    const files = req.files || [];
    const uploadedDocs = [];
    for (const file of files) {
      if (isValidFileType(file.originalname || '', file.mimetype || '')) {
        const docType = file.fieldname && file.fieldname.includes('[')
          ? file.fieldname.split('[')[1].split(']')[0]
          : 'general';

        const docRecord = await uploadLoanDocument({
          applicationId: appId,
          fileBuffer: file.buffer,
          originalFilename: file.originalname,
          mimeType: file.mimetype,
          documentType: docType,
          userToken
        });
        uploadedDocs.push(docRecord);
      }
    }

    res.status(200).json({
      success: true,
      applicationId: appId,
      referenceId: `CR-${appId.slice(0, 8).toUpperCase()}`,
      application: savedApp,
      documentsCount: uploadedDocs.length,
      message: 'Loan application created successfully.'
    });
  } catch (err) {
    console.error('[Applications Route] Error:', err);
    res.status(500).json({ detail: err.message || 'Error creating application.' });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// POST /api/applications/:id/documents - Secure upload of supporting documents
// ─────────────────────────────────────────────────────────────────────────────
router.post('/applications/:id/documents', upload.any(), async (req, res) => {
  try {
    const applicationId = req.params.id;
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    const files = req.files || [];
    if (files.length === 0) {
      return res.status(400).json({ detail: 'No files provided for upload.' });
    }

    const uploadedDocs = [];
    const rejectedDocs = [];

    for (const file of files) {
      const originalName = file.originalname || 'document.dat';
      // Server-side validation of size and mime type
      if (file.size > 10 * 1024 * 1024) {
        rejectedDocs.push({
          filename: originalName,
          reason: 'File exceeds 10 MB size limit.'
        });
        continue;
      }

      if (!isValidFileType(originalName, file.mimetype)) {
        rejectedDocs.push({
          filename: originalName,
          reason: 'Unsupported format. Allowed formats: PDF, JPG, JPEG, PNG.'
        });
        continue;
      }

      const docType = req.body.documentType || (file.fieldname && file.fieldname.includes('[')
        ? file.fieldname.split('[')[1].split(']')[0]
        : 'supporting');

      const docRecord = await uploadLoanDocument({
        applicationId,
        fileBuffer: file.buffer,
        originalFilename: originalName,
        mimeType: file.mimetype,
        documentType: docType,
        userToken
      });

      uploadedDocs.push(docRecord);
    }

    res.status(200).json({
      success: true,
      applicationId,
      uploadedCount: uploadedDocs.length,
      documents: uploadedDocs,
      rejected: rejectedDocs
    });
  } catch (err) {
    console.error('[Upload Documents Route] Error:', err);
    res.status(500).json({ detail: err.message || 'Error uploading documents.' });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// POST /api/applications/:id/analyze - Real AI Document Analysis
// ─────────────────────────────────────────────────────────────────────────────
router.post('/api/applications/:id/analyze', async (req, res) => {
  return handleAnalyze(req, res);
});
router.post('/applications/:id/analyze', async (req, res) => {
  return handleAnalyze(req, res);
});

async function handleAnalyze(req, res) {
  try {
    const applicationId = req.params.id;
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    // 1. Retrieve application details
    const application = await getApplicationById(applicationId, userToken);
    if (!application) {
      return res.status(404).json({ detail: `Loan application '${applicationId}' not found.` });
    }

    // 2. Retrieve documents metadata from loan_documents table
    const dbDocs = await getApplicationLoanDocuments(applicationId, userToken);

    // 3. Download actual document buffers securely from private storage bucket
    const filesWithBuffers = [];
    for (const doc of dbDocs) {
      if (doc.storage_path) {
        const buffer = await downloadStorageFile('loan-documents', doc.storage_path, userToken);
        if (buffer) {
          filesWithBuffers.push({
            originalname: doc.original_filename || 'document.pdf',
            mimetype: doc.mime_type || 'application/pdf',
            buffer,
            documentType: doc.document_type
          });
        }
      }
    }

    const applicationDetails = {
      id: applicationId,
      applicant_name: application.full_name || application.applicant_name || 'Applicant',
      email: application.email || application.applicant_email || '',
      phone: application.phone || application.applicant_phone || '',
      loan_type: application.loan_type || 'Personal Loan',
      lender_type: application.lender_type || 'Bank',
      monthly_income: application.monthly_income || 0,
      loan_amount: application.requested_loan_amount || application.loan_amount || 0,
      existing_monthly_debt: application.existing_monthly_emis || application.existing_monthly_debt || 0,
      tenure: application.preferred_tenure || application.tenure || '3-5 years',
      purpose: application.loan_purpose || ''
    };

    // 4. Run real AI document analysis (FastAPI on Port 8001 / Gemini 2.0 Flash)
    const analysisResult = await analyzeDocuments(applicationDetails, filesWithBuffers);

    // 5. Persist report into public.document_analysis_reports
    const savedReport = await saveDocumentAnalysisReport(
      applicationId,
      {
        overall_status: analysisResult.overall_status || 'completed',
        executive_summary: analysisResult.executive_summary || analysisResult.summary || 'Document analysis completed.',
        extracted_information: analysisResult.extracted_information || {},
        document_results: analysisResult.document_results || [],
        missing_documents: analysisResult.missing_documents || [],
        inconsistencies: analysisResult.inconsistencies || [],
        risk_flags: analysisResult.risk_flags || [],
        recommendations: analysisResult.recommendations || ['Review submitted documents with credit underwriter'],
        limitations: analysisResult.limitations || 'Preliminary automated review. Final terms subject to credit policy.',
        model_name: analysisResult.model_name || 'gemini-2.0-flash'
      },
      userToken
    );

    res.status(200).json({
      success: true,
      applicationId,
      analysis_status: savedReport.overall_status,
      report: savedReport
    });
  } catch (err) {
    console.error('[Analyze Route] Error:', err);
    res.status(500).json({ detail: err.message || 'Error analyzing loan documents.' });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/applications/:id/report - Retrieve saved document analysis report
// ─────────────────────────────────────────────────────────────────────────────
router.get('/applications/:id/report', async (req, res) => {
  try {
    const applicationId = req.params.id;
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    const report = await getDocumentAnalysisReport(applicationId, userToken);
    const application = await getApplicationById(applicationId, userToken);
    const documents = await getApplicationLoanDocuments(applicationId, userToken);

    if (!report) {
      return res.status(404).json({
        success: false,
        detail: `No document analysis report found for application '${applicationId}'.`
      });
    }

    res.status(200).json({
      success: true,
      applicationId,
      report,
      application,
      documents
    });
  } catch (err) {
    console.error('[Get Report Route] Error:', err);
    res.status(500).json({ detail: err.message || 'Error retrieving report.' });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// POST /api/applications/:id/report/pdf - Upload generated PDF to storage & DB
// ─────────────────────────────────────────────────────────────────────────────
router.post('/applications/:id/report/pdf', upload.single('pdf'), async (req, res) => {
  try {
    const applicationId = req.params.id;
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    if (!req.file || !req.file.buffer) {
      return res.status(400).json({ detail: 'No PDF file provided.' });
    }

    const result = await saveReportPdf(applicationId, req.file.buffer, userToken);

    res.status(200).json({
      success: true,
      applicationId,
      storagePath: result.storagePath,
      message: 'Assessment report PDF saved to database successfully.'
    });
  } catch (err) {
    console.error('[PDF Upload Route] Error:', err);
    res.status(500).json({ detail: err.message || 'Error saving PDF report.' });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/applications/:id/report/pdf - Download stored PDF from DB
// ─────────────────────────────────────────────────────────────────────────────
router.get('/applications/:id/report/pdf', async (req, res) => {
  try {
    const applicationId = req.params.id;
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    const pdfBuffer = await getReportPdfBuffer(applicationId, userToken);

    if (!pdfBuffer) {
      return res.status(404).json({ detail: 'No PDF report found for this application.' });
    }

    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="CREDORA_Report_${applicationId}.pdf"`);
    res.send(pdfBuffer);
  } catch (err) {
    console.error('[PDF Download Route] Error:', err);
    res.status(500).json({ detail: err.message || 'Error retrieving PDF report.' });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/applications/:id - Retrieve application details
// ─────────────────────────────────────────────────────────────────────────────
router.get('/applications/:id', async (req, res) => {
  try {
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    const app = await getApplicationById(req.params.id, userToken);
    if (!app) {
      return res.status(404).json({ detail: `Loan application '${req.params.id}' not found.` });
    }

    const documents = await getApplicationLoanDocuments(req.params.id, userToken);

    res.json({
      success: true,
      application: app,
      documents
    });
  } catch (err) {
    res.status(500).json({ detail: err.message });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/applications - List applications with filters
// ─────────────────────────────────────────────────────────────────────────────
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

// ─────────────────────────────────────────────────────────────────────────────
// PATCH /api/applications/:id/status - Update application status
// ─────────────────────────────────────────────────────────────────────────────
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

    await updateApplicationStatus(req.params.id, newStatus, userToken);
    res.json({ success: true, application_id: req.params.id, status: newStatus });
  } catch (err) {
    res.status(500).json({ detail: err.message });
  }
});

export default router;
