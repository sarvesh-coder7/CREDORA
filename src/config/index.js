import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';
import dotenv from 'dotenv';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT_DIR = path.resolve(__dirname, '../../');

// Load environment variables from project root .env
const envPath = path.join(ROOT_DIR, '.env');
if (fs.existsSync(envPath)) {
  dotenv.config({ path: envPath });
} else {
  dotenv.config();
}

export const config = {
  PORT: parseInt(process.env.PORT || '8000', 10),
  NODE_ENV: process.env.NODE_ENV || 'development',
  ROOT_DIR,
  SUPABASE_URL: (process.env.SUPABASE_URL || 'https://qjjewofsqjusfkwioquf.supabase.co').replace(/\/$/, ''),
  SUPABASE_ANON_KEY: process.env.SUPABASE_ANON_KEY || 'sb_publishable_-9PKQZETYwZxIMcqlIVPgA_46FsGLpN',
  SECRET_KEY: process.env.SECRET_KEY || 'credora-secret-key-2026-production-token',
  AI_SERVICE_URL: (process.env.AI_SERVICE_URL || 'http://127.0.0.1:8001').replace(/\/$/, ''),
  GEMINI_API_KEY: process.env.GEMINI_API_KEY || '',
  GEMINI_MODEL: process.env.GEMINI_MODEL || 'gemini-2.0-flash',
  ALLOWED_ORIGINS: (process.env.ALLOWED_ORIGINS || 'http://localhost:3000,http://localhost:5173,http://localhost:8000,http://127.0.0.1:5173,http://127.0.0.1:8000')
    .split(',')
    .map(o => o.trim())
    .filter(Boolean),
  GEO_DATA_PATH: path.join(ROOT_DIR, 'shared', 'india_geo_data.json'),
  SMTP_HOST: process.env.SMTP_HOST || '',
  SMTP_PORT: parseInt(process.env.SMTP_PORT || '587', 10),
  SMTP_USER: process.env.SMTP_USER || '',
  SMTP_PASSWORD: process.env.SMTP_PASSWORD || '',
  SENDER_EMAIL: process.env.SENDER_EMAIL || 'noreply@credora.example.com',
  SENDER_NAME: process.env.SENDER_NAME || 'CREDORA Notifications'
};

export default config;
