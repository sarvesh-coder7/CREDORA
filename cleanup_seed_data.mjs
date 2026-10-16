/**
 * CREDORA - Seed Data & Test Artifacts Cleanup Utility
 * 
 * Safely removes ephemeral test borrower profiles, test applications,
 * and test accounts generated during automated test suites and seed runs.
 * Preserves core seed data (such as verified lender Apex Capital LEN-1001).
 *
 * Usage:
 *   node cleanup_seed_data.mjs [--dry-run]
 */

import { createClient } from '@supabase/supabase-js';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

dotenv.config({ path: path.join(__dirname, '.env') });

const SUPABASE_URL = process.env.SUPABASE_URL || 'https://qjjewofsqjusfkwioquf.supabase.co';
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_ANON_KEY || 'sb_publishable_-9PKQZETYwZxIMcqlIVPgA_46FsGLpN';

const isDryRun = process.argv.includes('--dry-run');

console.log(`[CREDORA Cleanup] Connecting to Supabase at ${SUPABASE_URL}...`);
if (isDryRun) {
  console.log('[CREDORA Cleanup] Running in DRY-RUN mode. No rows will be deleted.');
}

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY, {
  auth: { persistSession: false, autoRefreshToken: false }
});

async function runCleanup() {
  try {
    // 1. Find ephemeral test borrower accounts (e.g., borrower.verify.*)
    const { data: testBorrowers, error: queryErr } = await supabase
      .from('borrower_profiles')
      .select('id, email, full_name, created_at')
      .or('email.like.borrower.verify.%,email.like.test.%,email.like.%@example.com');

    if (queryErr) {
      console.warn('[CREDORA Cleanup] Notice:', queryErr.message);
      console.log('[CREDORA Cleanup] Schema RLS protected; service-role key recommended for full cascade cleanup.');
      return;
    }

    if (!testBorrowers || testBorrowers.length === 0) {
      console.log('[CREDORA Cleanup] No ephemeral test borrower profiles found.');
      return;
    }

    console.log(`[CREDORA Cleanup] Found ${testBorrowers.length} ephemeral test borrower profile(s):`);
    for (const b of testBorrowers) {
      console.log(`  - ${b.email} (ID: ${b.id})`);
    }

    if (isDryRun) {
      console.log('[CREDORA Cleanup] Dry-run completed. Exiting without modifying database.');
      return;
    }

    const ids = testBorrowers.map(b => b.id);
    const { error: delErr } = await supabase
      .from('borrower_profiles')
      .delete()
      .in('id', ids);

    if (delErr) {
      console.error('[CREDORA Cleanup] Error deleting test borrower profiles:', delErr.message);
    } else {
      console.log(`[CREDORA Cleanup] Successfully cleaned up ${ids.length} test borrower record(s).`);
    }
  } catch (err) {
    console.error('[CREDORA Cleanup] Unexpected error:', err.message);
  }
}

runCleanup();
