import { Router } from 'express';
import multer from 'multer';
import crypto from 'crypto';
import { analyzeDocuments } from '../services/ai.service.js';
import { uploadToSupabaseStorage, insertBorrowerDocument, getSupabaseClient } from '../services/supabase.service.js';

const router = Router();
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 20 * 1024 * 1024, files: 10 }
});

// POST /api/documents/analyze
router.post('/documents/analyze', upload.any(), async (req, res) => {
  try {
    const files = req.files || [];
    if (files.length === 0) {
      return res.status(400).json({
        detail: 'Please select at least one document to analyze.'
      });
    }

    const details = {
      applicant_name: req.body.fullName || req.body.applicant_name || 'Applicant',
      loan_type: req.body.loanType || req.body.loan_type || 'Personal Loan',
      lender_type: req.body.lenderType || req.body.lender_type || 'Bank',
      monthly_income: req.body.income || req.body.monthly_income || 0,
      loan_amount: req.body.loanAmount || req.body.requested_loan_amount || 0,
      existing_monthly_debt: req.body.emis || req.body.existing_monthly_obligations || 0
    };

    const result = await analyzeDocuments(details, files);
    res.json(result);
  } catch (err) {
    console.error('[Document Analyze Route] Error:', err);
    res.status(500).json({ detail: err.message || 'Error analyzing documents.' });
  }
});

// POST /api/documents/upload
router.post('/documents/upload', upload.single('file'), async (req, res) => {
  try {
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;

    const file = req.file;
    if (!file) {
      return res.status(400).json({ detail: 'No file provided.' });
    }

    const docType = req.body.documentType || 'general';
    const applicationId = req.body.applicationId || crypto.randomUUID();
    const cleanFilename = (file.originalname || 'document.dat').replace(/[^\w.-]/g, '_');
    const storagePath = `uploads/${applicationId}/${cleanFilename}`;

    const uploaded = await uploadToSupabaseStorage(
      'borrower-documents',
      storagePath,
      file.buffer,
      file.mimetype,
      userToken
    );

    const docId = crypto.randomUUID();
    const docRecord = {
      id: docId,
      application_id: applicationId,
      document_type: docType,
      file_name: cleanFilename,
      storage_path: storagePath,
      mime_type: file.mimetype || 'application/octet-stream',
      file_size: file.buffer?.length || 0,
      verification_status: 'pending'
    };

    await insertBorrowerDocument(docRecord, userToken);

    res.status(201).json({
      success: true,
      document_id: docId,
      file_name: cleanFilename,
      storage_path: storagePath,
      uploaded
    });
  } catch (err) {
    res.status(500).json({ detail: err.message });
  }
});

// GET /api/documents/:id/status
router.get('/documents/:id/status', async (req, res) => {
  try {
    const userToken = req.headers.authorization?.startsWith('Bearer ')
      ? req.headers.authorization.split(' ')[1]
      : null;
    const client = getSupabaseClient(userToken);

    const { data, error } = await client
      .from('borrower_documents')
      .select('id, file_name, document_type, verification_status, created_at')
      .eq('id', req.params.id)
      .limit(1);

    if (error || !data || data.length === 0) {
      return res.status(404).json({ detail: `Document '${req.params.id}' not found.` });
    }
    res.json({ success: true, document: data[0] });
  } catch (err) {
    res.status(500).json({ detail: err.message });
  }
});

export default router;
