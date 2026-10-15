-- 001_create_lending_core_schema.sql
-- Core tables, constraints, indexes, and triggers for Borrower & Lender platform

-- 1. Helper trigger function for updating updated_at timestamp
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

-- 2. Profiles table (shared for borrowers and lenders)
CREATE TABLE IF NOT EXISTS public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  lender_code TEXT UNIQUE,
  full_name TEXT NOT NULL,
  email TEXT,
  phone TEXT,
  role TEXT NOT NULL CHECK (role IN ('borrower', 'lender')),
  state TEXT,
  city TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_lender_code_role CHECK (
    (role = 'lender' AND lender_code IS NOT NULL) OR
    (role = 'borrower' AND lender_code IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_profiles_role ON public.profiles(role);
CREATE INDEX IF NOT EXISTS idx_profiles_lender_code ON public.profiles(lender_code) WHERE lender_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_profiles_email ON public.profiles(email);

CREATE TRIGGER trg_profiles_updated_at
BEFORE UPDATE ON public.profiles
FOR EACH ROW
EXECUTE FUNCTION public.handle_updated_at();

-- 3. Lender Organizations table
CREATE TABLE IF NOT EXISTS public.lender_organizations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_name TEXT NOT NULL,
  organization_type TEXT NOT NULL,
  registration_number TEXT NOT NULL UNIQUE,
  official_email TEXT NOT NULL,
  official_phone TEXT,
  registered_address TEXT,
  state TEXT,
  city TEXT,
  verification_status TEXT NOT NULL DEFAULT 'pending' CHECK (verification_status IN ('pending', 'verified', 'rejected')),
  verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lender_org_verification_status ON public.lender_organizations(verification_status);
CREATE INDEX IF NOT EXISTS idx_lender_org_reg_number ON public.lender_organizations(registration_number);

CREATE TRIGGER trg_lender_organizations_updated_at
BEFORE UPDATE ON public.lender_organizations
FOR EACH ROW
EXECUTE FUNCTION public.handle_updated_at();

-- 4. Lender Organization Members table
CREATE TABLE IF NOT EXISTS public.lender_organization_members (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id UUID NOT NULL REFERENCES public.lender_organizations(id) ON DELETE CASCADE,
  lender_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  member_role TEXT NOT NULL DEFAULT 'lender' CHECK (member_role IN ('lender', 'admin', 'manager', 'underwriter')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_lender_org_member UNIQUE (organization_id, lender_id)
);

CREATE INDEX IF NOT EXISTS idx_lender_org_members_org ON public.lender_organization_members(organization_id);
CREATE INDEX IF NOT EXISTS idx_lender_org_members_lender ON public.lender_organization_members(lender_id);

-- Enforce that member profile has role = 'lender'
CREATE OR REPLACE FUNCTION public.check_lender_member_role()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.profiles
    WHERE id = NEW.lender_id AND role = 'lender'
  ) THEN
    RAISE EXCEPTION 'Linked profile % must have role lender', NEW.lender_id;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_check_lender_member_role
BEFORE INSERT OR UPDATE ON public.lender_organization_members
FOR EACH ROW
EXECUTE FUNCTION public.check_lender_member_role();

-- 5. Loan Applications table
CREATE TABLE IF NOT EXISTS public.loan_applications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  borrower_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  loan_amount NUMERIC(14,2) NOT NULL CHECK (loan_amount > 0),
  loan_purpose TEXT NOT NULL,
  monthly_income NUMERIC(14,2) CHECK (monthly_income IS NULL OR monthly_income >= 0),
  employment_type TEXT,
  existing_monthly_debt NUMERIC(14,2) CHECK (existing_monthly_debt IS NULL OR existing_monthly_debt >= 0),
  status TEXT NOT NULL DEFAULT 'submitted' CHECK (
    status IN ('draft', 'submitted', 'under_review', 'assessment_ready', 'approved', 'rejected', 'more_information_required')
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_loan_applications_borrower_id ON public.loan_applications(borrower_id);
CREATE INDEX IF NOT EXISTS idx_loan_applications_status ON public.loan_applications(status);
CREATE INDEX IF NOT EXISTS idx_loan_applications_created_at ON public.loan_applications(created_at DESC);

CREATE TRIGGER trg_loan_applications_updated_at
BEFORE UPDATE ON public.loan_applications
FOR EACH ROW
EXECUTE FUNCTION public.handle_updated_at();

-- 6. Borrower Documents table
CREATE TABLE IF NOT EXISTS public.borrower_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id UUID NOT NULL REFERENCES public.loan_applications(id) ON DELETE CASCADE,
  uploaded_by UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  document_type TEXT NOT NULL,
  file_name TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  mime_type TEXT,
  file_size BIGINT CHECK (file_size IS NULL OR (file_size >= 0 AND file_size <= 52428800)), -- max 50MB
  verification_status TEXT NOT NULL DEFAULT 'pending' CHECK (verification_status IN ('pending', 'verified', 'rejected')),
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_borrower_docs_application_id ON public.borrower_documents(application_id);
CREATE INDEX IF NOT EXISTS idx_borrower_docs_uploaded_by ON public.borrower_documents(uploaded_by);
CREATE INDEX IF NOT EXISTS idx_borrower_docs_verification_status ON public.borrower_documents(verification_status);

-- 7. Lender Documents table
CREATE TABLE IF NOT EXISTS public.lender_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id UUID NOT NULL REFERENCES public.lender_organizations(id) ON DELETE CASCADE,
  document_type TEXT NOT NULL,
  file_name TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  mime_type TEXT,
  file_size BIGINT CHECK (file_size IS NULL OR (file_size >= 0 AND file_size <= 52428800)), -- max 50MB
  verification_status TEXT NOT NULL DEFAULT 'pending' CHECK (verification_status IN ('pending', 'verified', 'rejected')),
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lender_docs_org_id ON public.lender_documents(organization_id);
CREATE INDEX IF NOT EXISTS idx_lender_docs_verification_status ON public.lender_documents(verification_status);

-- 8. Loan Assessments table
CREATE TABLE IF NOT EXISTS public.loan_assessments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id UUID NOT NULL REFERENCES public.loan_applications(id) ON DELETE CASCADE,
  model_version TEXT,
  risk_score NUMERIC(5,2) CHECK (risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)),
  risk_level TEXT CHECK (risk_level IS NULL OR risk_level IN ('low', 'medium', 'high')),
  assessment_summary TEXT,
  extracted_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_loan_assessments_application_id ON public.loan_assessments(application_id);
CREATE INDEX IF NOT EXISTS idx_loan_assessments_created_at ON public.loan_assessments(created_at DESC);

-- 9. Loan Reviews table
CREATE TABLE IF NOT EXISTS public.loan_reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id UUID NOT NULL REFERENCES public.loan_applications(id) ON DELETE CASCADE,
  lender_id UUID NOT NULL REFERENCES public.profiles(id),
  decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'request_information')),
  reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_loan_reviews_application_id ON public.loan_reviews(application_id);
CREATE INDEX IF NOT EXISTS idx_loan_reviews_lender_id ON public.loan_reviews(lender_id);

-- 10. Notifications table
CREATE TABLE IF NOT EXISTS public.notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  application_id UUID REFERENCES public.loan_applications(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  is_read BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON public.notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON public.notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_notifications_created_at ON public.notifications(created_at DESC);
