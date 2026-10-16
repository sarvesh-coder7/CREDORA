import { Router } from 'express';
import config from '../config/index.js';

const router = Router();

router.get('/health', async (req, res) => {
  let aiServiceReachable = false;
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1500);
    const aiRes = await fetch(`${config.AI_SERVICE_URL}/health`, { signal: controller.signal });
    clearTimeout(timeout);
    aiServiceReachable = aiRes.ok;
  } catch (err) {
    aiServiceReachable = false;
  }

  res.json({
    status: 'healthy',
    service: 'CREDORA Lending & AI Document Analysis Express API',
    version: '1.2.0',
    node_version: process.version,
    supabase_connected: Boolean(config.SUPABASE_URL && config.SUPABASE_ANON_KEY),
    ai_service_configured: Boolean(config.GEMINI_API_KEY),
    ai_microservice_online: aiServiceReachable,
    smtp_configured: Boolean(config.SMTP_HOST && config.SMTP_USER)
  });
});

export default router;
