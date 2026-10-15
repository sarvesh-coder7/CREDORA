/**
 * CREDORA - Reusable Supabase Authentication & Database Client
 * Reads Supabase URL and publishable/anon key from environment variables.
 * Never exposes service-role keys in frontend code.
 */

import { createClient } from '@supabase/supabase-js';

// Resolve configuration from Vite import.meta.env, window config, or fallback defines
const SUPABASE_URL = 
  (typeof import.meta !== 'undefined' && import.meta.env && (import.meta.env.VITE_SUPABASE_URL || import.meta.env.SUPABASE_URL)) ||
  (typeof window !== 'undefined' && window.__CREDORA_CONFIG__?.SUPABASE_URL) ||
  (typeof __SUPABASE_URL__ !== 'undefined' ? __SUPABASE_URL__ : '') ||
  'https://qjjewofsqjusfkwioquf.supabase.co';

const SUPABASE_ANON_KEY = 
  (typeof import.meta !== 'undefined' && import.meta.env && (import.meta.env.VITE_SUPABASE_ANON_KEY || import.meta.env.SUPABASE_ANON_KEY)) ||
  (typeof window !== 'undefined' && window.__CREDORA_CONFIG__?.SUPABASE_ANON_KEY) ||
  (typeof __SUPABASE_ANON_KEY__ !== 'undefined' ? __SUPABASE_ANON_KEY__ : '') ||
  'sb_publishable_-9PKQZETYwZxIMcqlIVPgA_46FsGLpN';

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  console.error('[CREDORA Auth] Supabase URL or Anon Key is missing. Check your environment variables.');
}

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    storage: typeof window !== 'undefined' ? window.localStorage : undefined
  }
});

/**
 * Resolves a Lender ID (code) to the registered email address via Supabase RPC.
 * @param {string} lenderCode - e.g. "LEN-1001"
 * @returns {Promise<string|null>} - Registered email or null if not found
 */
export async function lookupLenderEmail(lenderCode) {
  if (!lenderCode) return null;
  const cleanCode = lenderCode.trim().toUpperCase();
  const { data, error } = await supabase.rpc('lookup_lender_email', {
    p_lender_code: cleanCode
  });
  if (error) {
    console.warn('[CREDORA Auth] lookup_lender_email error:', error);
    return null;
  }
  return data || null;
}

/**
 * Retrieves the borrower profile for a given user ID.
 * @param {string} userId - UUID of auth user
 */
export async function getBorrowerProfile(userId) {
  if (!userId) return null;
  const { data, error } = await supabase
    .from('borrower_profiles')
    .select('*')
    .eq('id', userId)
    .single();

  if (error) {
    console.warn('[CREDORA Auth] Failed to fetch borrower profile:', error.message);
    return null;
  }
  return data;
}

/**
 * Retrieves the lender profile for a given user ID.
 * @param {string} userId - UUID of auth user
 */
export async function getLenderProfile(userId) {
  if (!userId) return null;
  const { data, error } = await supabase
    .from('lender_profiles')
    .select('*')
    .eq('id', userId)
    .single();

  if (error) {
    console.warn('[CREDORA Auth] Failed to fetch lender profile:', error.message);
    return null;
  }
  return data;
}

/**
 * Determines whether the authenticated user is a borrower or lender,
 * and fetches the corresponding profile.
 * @param {string} userId
 */
export async function getUserRoleAndProfile(userId) {
  if (!userId) return { role: null, profile: null };

  // First check lender_profiles
  const lender = await getLenderProfile(userId);
  if (lender) {
    return { role: 'lender', profile: lender };
  }

  // Then check borrower_profiles
  const borrower = await getBorrowerProfile(userId);
  if (borrower) {
    return { role: 'borrower', profile: borrower };
  }

  return { role: null, profile: null };
}

/**
 * Registers a new borrower using Supabase Auth signUp and records profile in borrower_profiles.
 */
export async function registerBorrower({
  email,
  password,
  fullName,
  mobile,
  state,
  city,
  aadhaarNumber
}) {
  const cleanEmail = email.trim().toLowerCase();
  const cleanMobile = mobile.replace(/[\s\-\+]/g, '').slice(-10);

  // 1. Call supabase.auth.signUp()
  const { data, error } = await supabase.auth.signUp({
    email: cleanEmail,
    password: password,
    options: {
      data: {
        full_name: fullName.trim(),
        name: fullName.trim(),
        mobile_number: cleanMobile,
        phone: cleanMobile,
        state: (state || '').trim(),
        city: (city || '').trim(),
        aadhaar_number: aadhaarNumber ? aadhaarNumber.replace(/[\s\-]/g, '') : null,
        role: 'borrower'
      }
    }
  });

  if (error) {
    // Check for duplicate registration or existing account
    if (
      error.message?.toLowerCase().includes('already registered') ||
      error.message?.toLowerCase().includes('already exists') ||
      error.code === 'user_already_exists'
    ) {
      throw new Error('An account with this email address already exists. Please sign in instead.');
    }
    if (error.message?.toLowerCase().includes('rate limit')) {
      throw new Error('Email request rate limit reached. Please wait a short while before trying again.');
    }
    throw new Error(error.message || 'Registration failed. Please try again.');
  }

  const user = data?.user;
  // If user object exists but identities is empty, Supabase obfuscates that user already exists
  if (user && Array.isArray(user.identities) && user.identities.length === 0) {
    throw new Error('An account with this email address already exists. Please sign in instead.');
  }

  // If user session was returned immediately (email confirmation disabled), ensure profile exists
  if (data?.session && user) {
    try {
      await supabase.from('borrower_profiles').upsert({
        id: user.id,
        full_name: fullName.trim(),
        email: cleanEmail,
        mobile_number: cleanMobile,
        state: (state || '').trim(),
        city: (city || '').trim(),
        aadhaar_number: aadhaarNumber ? aadhaarNumber.replace(/[\s\-]/g, '') : null,
        is_active: true
      }, { onConflict: 'id' });

      // Save user session in localStorage for site-wide navbar compatibility
      saveUserToStorage({
        id: user.id,
        name: fullName.trim(),
        email: cleanEmail,
        role: 'borrower'
      });
    } catch (upsertErr) {
      console.warn('[CREDORA Auth] Profile upsert warning:', upsertErr);
    }
  }

  return {
    user,
    session: data?.session,
    requiresVerification: !data?.session
  };
}

/**
 * Authenticates a borrower using email and password.
 * Verifies that the borrower profile exists and is active.
 */
export async function loginBorrower(email, password) {
  const cleanEmail = email.trim().toLowerCase();

  const { data, error } = await supabase.auth.signInWithPassword({
    email: cleanEmail,
    password: password
  });

  if (error) {
    if (error.message?.toLowerCase().includes('invalid login credentials')) {
      throw new Error('Invalid email or password. Please check your credentials and try again.');
    }
    if (error.message?.toLowerCase().includes('email not confirmed')) {
      throw new Error('Please verify your email address before signing in. Check your inbox for the verification link.');
    }
    throw new Error(error.message || 'Login failed. Please check your network and credentials.');
  }

  const user = data?.user;
  if (!user) throw new Error('Authentication succeeded but no user data was returned.');

  // Verify borrower profile
  let profile = await getBorrowerProfile(user.id);

  // If profile is not found in borrower_profiles, check if account is actually a lender
  if (!profile) {
    const lenderProfile = await getLenderProfile(user.id);
    if (lenderProfile) {
      await supabase.auth.signOut();
      throw new Error('This account is registered as a lender. Please use the Lender Login page.');
    }

    // Try fallback sync from user metadata if profile wasn't created yet
    const meta = user.user_metadata || {};
    if (meta.role === 'borrower' || !meta.role) {
      try {
        const { data: newProfile } = await supabase.from('borrower_profiles').upsert({
          id: user.id,
          full_name: meta.full_name || meta.name || cleanEmail.split('@')[0],
          email: cleanEmail,
          mobile_number: meta.mobile_number || meta.phone || '9876543210',
          state: meta.state || '',
          city: meta.city || '',
          is_active: true
        }, { onConflict: 'id' }).select().single();
        profile = newProfile;
      } catch (e) {
        console.warn('Fallback borrower profile creation error:', e);
      }
    }
  }

  if (!profile) {
    await supabase.auth.signOut();
    throw new Error('Borrower profile record not found. Please contact support.');
  }

  if (profile.is_active === false) {
    await supabase.auth.signOut();
    throw new Error('Your borrower account has been deactivated. Please contact support.');
  }

  // Update localStorage session for compatibility
  saveUserToStorage({
    id: user.id,
    name: profile.full_name || 'CREDORA Borrower',
    email: cleanEmail,
    role: 'borrower'
  });

  return { user, profile, session: data.session };
}

/**
 * Authenticates a lender using either Email or Lender ID (code).
 * Verifies that the lender account exists, is authorized, and is active.
 */
export async function loginLender(identifier, password) {
  const cleanInput = identifier.trim();
  let authEmail = cleanInput;

  // If identifier does not contain '@', resolve Lender ID to email
  if (!cleanInput.includes('@')) {
    const resolvedEmail = await lookupLenderEmail(cleanInput);
    if (!resolvedEmail) {
      throw new Error(`No active lender account was found matching Lender ID "${cleanInput}".`);
    }
    authEmail = resolvedEmail;
  }

  const { data, error } = await supabase.auth.signInWithPassword({
    email: authEmail.toLowerCase(),
    password: password
  });

  if (error) {
    if (error.message?.toLowerCase().includes('invalid login credentials')) {
      throw new Error('Invalid credentials. Please verify your Lender ID/Email and password.');
    }
    if (error.message?.toLowerCase().includes('email not confirmed')) {
      throw new Error('Lender email verification is required. Please check your inbox.');
    }
    throw new Error(error.message || 'Lender authentication failed. Please try again.');
  }

  const user = data?.user;
  if (!user) throw new Error('Authentication succeeded but no user data was returned.');

  // Verify lender profile in lender_profiles table
  const profile = await getLenderProfile(user.id);

  if (!profile) {
    await supabase.auth.signOut();
    throw new Error('Access denied. This account does not possess authorized lender privileges.');
  }

  if (profile.is_active === false) {
    await supabase.auth.signOut();
    throw new Error('This lender account has been deactivated. Please contact your organization administrator.');
  }

  // Save session to storage
  saveUserToStorage({
    id: user.id,
    name: profile.full_name || 'CREDORA Lender',
    email: profile.email || authEmail,
    role: 'lender',
    lenderCode: profile.lender_code
  });

  return { user, profile, session: data.session };
}

/**
 * Sends a password reset email via Supabase Auth.
 */
export async function resetPassword(email) {
  const cleanEmail = email.trim().toLowerCase();
  const redirectUrl = window.location.origin + '/login.html';
  const { error } = await supabase.auth.resetPasswordForEmail(cleanEmail, {
    redirectTo: redirectUrl
  });

  if (error) {
    throw new Error(error.message || 'Failed to send password reset email.');
  }
  return true;
}

/**
 * Signs the user out of Supabase and clears local storage.
 */
export async function logout() {
  try {
    await supabase.auth.signOut();
  } catch (err) {
    console.warn('[CREDORA Auth] Sign out error:', err);
  }
  clearUserFromStorage();
}

/**
 * Checks the current active Supabase session.
 */
export async function checkSession() {
  const { data: { session }, error } = await supabase.auth.getSession();
  if (error || !session) {
    clearUserFromStorage();
    return null;
  }

  // Validate or refresh stored user
  const user = session.user;
  const { role, profile } = await getUserRoleAndProfile(user.id);
  if (profile) {
    saveUserToStorage({
      id: user.id,
      name: profile.full_name || user.email,
      email: user.email,
      role: role,
      lenderCode: profile.lender_code
    });
  }
  return { session, user, role, profile };
}

/**
 * Route protection helper for Lender-only routes (e.g. applications.html).
 * Rejects unauthorized users or borrowers.
 */
export async function guardLenderRoute() {
  const sessionInfo = await checkSession();
  if (!sessionInfo || !sessionInfo.session) {
    // Unauthenticated: redirect to login
    window.location.replace('login.html?view=lender-login&auth_error=' + encodeURIComponent('Please sign in with your lender account to access the dashboard.'));
    return false;
  }

  if (sessionInfo.role !== 'lender' || !sessionInfo.profile?.is_active) {
    // Borrower or unauthorized attempting to access lender route
    alert('Access Denied: Lender privileges are required to view this page.');
    window.location.replace('index.html');
    return false;
  }

  return true;
}

function saveUserToStorage(userObj) {
  try {
    localStorage.setItem('credoraUser', JSON.stringify(userObj));
    // Dispatch custom event so listeners can update UI reactively
    window.dispatchEvent(new CustomEvent('credora:auth-change', { detail: userObj }));
  } catch (e) {
    console.warn('Storage save failed:', e);
  }
}

function clearUserFromStorage() {
  try {
    localStorage.removeItem('credoraUser');
    window.dispatchEvent(new CustomEvent('credora:auth-change', { detail: null }));
  } catch (e) {
    console.warn('Storage clear failed:', e);
  }
}

// Attach to window for global access across vanilla scripts
if (typeof window !== 'undefined') {
  window.CredoraAuth = {
    supabase,
    lookupLenderEmail,
    getBorrowerProfile,
    getLenderProfile,
    getUserRoleAndProfile,
    registerBorrower,
    loginBorrower,
    loginLender,
    resetPassword,
    logout,
    checkSession,
    guardLenderRoute
  };
  window.supabaseClient = supabase;
}
