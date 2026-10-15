-- 002_create_security_and_rls_policies.sql
-- Helper functions, security triggers, and Row Level Security (RLS) policies

-- ============================================================================
-- 1. Helper Security Functions (SECURITY DEFINER with fixed search_path)
-- ============================================================================

CREATE OR REPLACE FUNCTION public.get_auth_role()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT role FROM public.profiles WHERE id = auth.uid();
$$;

CREATE OR REPLACE FUNCTION public.is_active_borrower(p_user_id UUID DEFAULT auth.uid())
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.profiles
    WHERE id = p_user_id
      AND role = 'borrower'
      AND is_active = TRUE
  );
$$;

CREATE OR REPLACE FUNCTION public.is_active_lender(p_user_id UUID DEFAULT auth.uid())
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.profiles
    WHERE id = p_user_id
      AND role = 'lender'
      AND is_active = TRUE
  );
$$;

CREATE OR REPLACE FUNCTION public.is_org_member(p_user_id UUID, p_org_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.profiles p
    JOIN public.lender_organization_members lom ON lom.lender_id = p.id
    WHERE p.id = p_user_id
      AND lom.organization_id = p_org_id
      AND p.role = 'lender'
      AND p.is_active = TRUE
  );
$$;

CREATE OR REPLACE FUNCTION public.is_verified_lender(p_user_id UUID DEFAULT auth.uid())
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.profiles p
    JOIN public.lender_organization_members lom ON lom.lender_id = p.id
    JOIN public.lender_organizations lo ON lo.id = lom.organization_id
    WHERE p.id = p_user_id
      AND p.role = 'lender'
      AND p.is_active = TRUE
      AND lo.verification_status = 'verified'
  );
$$;

CREATE OR REPLACE FUNCTION public.lookup_lender_email(p_lender_code TEXT)
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT email
  FROM public.profiles
  WHERE UPPER(lender_code) = UPPER(TRIM(p_lender_code))
    AND role = 'lender'
    AND is_active = TRUE;
$$;

GRANT EXECUTE ON FUNCTION public.lookup_lender_email(TEXT) TO anon, authenticated;

-- Sequence and generator for lender code assignment
CREATE SEQUENCE IF NOT EXISTS public.seq_lender_code START WITH 1001;

CREATE OR REPLACE FUNCTION public.generate_lender_code()
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  RETURN 'LND-' || nextval('public.seq_lender_code')::text;
END;
$$;

-- ============================================================================
-- 2. Business Protection Triggers
-- ============================================================================

-- Profile fields protection: Users cannot tamper with role, lender_code, or is_active
CREATE OR REPLACE FUNCTION public.protect_profile_fields()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF (auth.jwt() ->> 'role') = 'authenticated' OR (auth.jwt() ->> 'role') = 'anon' THEN
    IF NEW.role IS DISTINCT FROM OLD.role THEN
      RAISE EXCEPTION 'Modifying user role is not permitted.';
    END IF;
    IF NEW.lender_code IS DISTINCT FROM OLD.lender_code THEN
      RAISE EXCEPTION 'Modifying lender code is not permitted.';
    END IF;
    IF NEW.is_active IS DISTINCT FROM OLD.is_active THEN
      RAISE EXCEPTION 'Modifying account activation status is not permitted.';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_protect_profile_fields
BEFORE UPDATE ON public.profiles
FOR EACH ROW
EXECUTE FUNCTION public.protect_profile_fields();

-- Loan Application fields protection: Borrowers cannot approve/reject or reassign borrower_id
CREATE OR REPLACE FUNCTION public.protect_loan_application_fields()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_role TEXT;
BEGIN
  SELECT role INTO v_role FROM public.profiles WHERE id = auth.uid();

  IF v_role = 'borrower' THEN
    IF NEW.borrower_id IS DISTINCT FROM OLD.borrower_id THEN
      RAISE EXCEPTION 'Cannot modify loan application ownership.';
    END IF;
    -- Borrowers cannot set review or decision statuses
    IF NEW.status IN ('under_review', 'assessment_ready', 'approved', 'rejected') AND NEW.status IS DISTINCT FROM OLD.status THEN
      RAISE EXCEPTION 'Borrowers are not permitted to change status to %.', NEW.status;
    END IF;
    -- Borrowers cannot edit applications already under review or decided
    IF OLD.status IN ('under_review', 'assessment_ready', 'approved', 'rejected') THEN
      RAISE EXCEPTION 'Application cannot be modified once in review or decided.';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_protect_loan_application_fields
BEFORE UPDATE ON public.loan_applications
FOR EACH ROW
EXECUTE FUNCTION public.protect_loan_application_fields();

-- Borrower document protection: Borrowers cannot tamper with verification status
CREATE OR REPLACE FUNCTION public.protect_borrower_doc_status()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_role TEXT;
BEGIN
  SELECT role INTO v_role FROM public.profiles WHERE id = auth.uid();
  IF v_role = 'borrower' THEN
    IF NEW.verification_status IS DISTINCT FROM OLD.verification_status THEN
      RAISE EXCEPTION 'Borrowers are not permitted to modify document verification status.';
    END IF;
    IF NEW.application_id IS DISTINCT FROM OLD.application_id OR NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by THEN
      RAISE EXCEPTION 'Cannot modify document ownership or linked application.';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_protect_borrower_doc_status
BEFORE UPDATE ON public.borrower_documents
FOR EACH ROW
EXECUTE FUNCTION public.protect_borrower_doc_status();

-- Review decisions sync trigger: Automatically synchronize application status & notify borrower
CREATE OR REPLACE FUNCTION public.handle_loan_review_submission()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_borrower_id UUID;
  v_new_status TEXT;
  v_title TEXT;
  v_msg TEXT;
BEGIN
  IF NEW.decision = 'approved' THEN
    v_new_status := 'approved';
    v_title := 'Loan Application Approved';
    v_msg := 'Your loan application has been approved. Decision notes: ' || NEW.reason;
  ELSIF NEW.decision = 'rejected' THEN
    v_new_status := 'rejected';
    v_title := 'Loan Application Decision';
    v_msg := 'Your loan application was rejected. Reason: ' || NEW.reason;
  ELSIF NEW.decision = 'request_information' THEN
    v_new_status := 'more_information_required';
    v_title := 'More Information Required';
    v_msg := 'The lender requested more information: ' || NEW.reason;
  ELSE
    RAISE EXCEPTION 'Invalid review decision: %', NEW.decision;
  END IF;

  UPDATE public.loan_applications
  SET status = v_new_status, updated_at = NOW()
  WHERE id = NEW.application_id
  RETURNING borrower_id INTO v_borrower_id;

  IF v_borrower_id IS NOT NULL THEN
    INSERT INTO public.notifications (user_id, application_id, title, message)
    VALUES (v_borrower_id, NEW.application_id, v_title, v_msg);
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_handle_loan_review_submission
AFTER INSERT ON public.loan_reviews
FOR EACH ROW
EXECUTE FUNCTION public.handle_loan_review_submission();

-- Notification protection: Users can only mark is_read on their own notifications
CREATE OR REPLACE FUNCTION public.protect_notification_fields()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF (auth.jwt() ->> 'role') = 'authenticated' THEN
    IF NEW.user_id IS DISTINCT FROM OLD.user_id OR
       NEW.application_id IS DISTINCT FROM OLD.application_id OR
       NEW.title IS DISTINCT FROM OLD.title OR
       NEW.message IS DISTINCT FROM OLD.message THEN
      RAISE EXCEPTION 'Users may only update read status of their own notifications.';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_protect_notification_fields
BEFORE UPDATE ON public.notifications
FOR EACH ROW
EXECUTE FUNCTION public.protect_notification_fields();

-- ============================================================================
-- 3. Row Level Security Policies
-- ============================================================================

-- A. PROFILES
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "profiles_select_own"
ON public.profiles FOR SELECT TO authenticated
USING (id = auth.uid());

CREATE POLICY "profiles_select_borrowers_by_verified_lender"
ON public.profiles FOR SELECT TO authenticated
USING (
  public.is_verified_lender(auth.uid()) AND role = 'borrower'
);

CREATE POLICY "profiles_select_lender_colleagues"
ON public.profiles FOR SELECT TO authenticated
USING (
  role = 'lender' AND EXISTS (
    SELECT 1 FROM public.lender_organization_members m1
    JOIN public.lender_organization_members m2 ON m1.organization_id = m2.organization_id
    WHERE m1.lender_id = auth.uid() AND m2.lender_id = profiles.id
  )
);

CREATE POLICY "profiles_insert_own_borrower"
ON public.profiles FOR INSERT TO authenticated
WITH CHECK (
  id = auth.uid() AND role = 'borrower' AND lender_code IS NULL AND is_active = TRUE
);

CREATE POLICY "profiles_update_own"
ON public.profiles FOR UPDATE TO authenticated
USING (id = auth.uid())
WITH CHECK (id = auth.uid());

-- B. LENDER ORGANIZATIONS
ALTER TABLE public.lender_organizations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "lender_org_select_members"
ON public.lender_organizations FOR SELECT TO authenticated
USING (
  public.is_org_member(auth.uid(), id) OR
  (public.is_verified_lender(auth.uid()) AND verification_status = 'verified')
);

-- C. LENDER ORGANIZATION MEMBERS
ALTER TABLE public.lender_organization_members ENABLE ROW LEVEL SECURITY;

CREATE POLICY "lender_org_members_select"
ON public.lender_organization_members FOR SELECT TO authenticated
USING (
  lender_id = auth.uid() OR public.is_org_member(auth.uid(), organization_id)
);

-- D. LOAN APPLICATIONS
ALTER TABLE public.loan_applications ENABLE ROW LEVEL SECURITY;

CREATE POLICY "loan_applications_select_borrower"
ON public.loan_applications FOR SELECT TO authenticated
USING (borrower_id = auth.uid());

CREATE POLICY "loan_applications_select_verified_lenders"
ON public.loan_applications FOR SELECT TO authenticated
USING (
  public.is_verified_lender(auth.uid()) AND status <> 'draft'
);

CREATE POLICY "loan_applications_insert_borrower"
ON public.loan_applications FOR INSERT TO authenticated
WITH CHECK (
  borrower_id = auth.uid()
  AND public.is_active_borrower(auth.uid())
  AND status IN ('draft', 'submitted')
);

CREATE POLICY "loan_applications_update_borrower"
ON public.loan_applications FOR UPDATE TO authenticated
USING (
  borrower_id = auth.uid() AND status IN ('draft', 'more_information_required')
)
WITH CHECK (
  borrower_id = auth.uid() AND status IN ('draft', 'submitted')
);

CREATE POLICY "loan_applications_update_lender"
ON public.loan_applications FOR UPDATE TO authenticated
USING (
  public.is_verified_lender(auth.uid()) AND status <> 'draft'
)
WITH CHECK (
  public.is_verified_lender(auth.uid()) AND status IN ('under_review', 'assessment_ready', 'approved', 'rejected', 'more_information_required')
);

-- E. BORROWER DOCUMENTS
ALTER TABLE public.borrower_documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "borrower_docs_select_owner"
ON public.borrower_documents FOR SELECT TO authenticated
USING (
  uploaded_by = auth.uid() OR EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = borrower_documents.application_id AND borrower_id = auth.uid()
  )
);

CREATE POLICY "borrower_docs_select_verified_lenders"
ON public.borrower_documents FOR SELECT TO authenticated
USING (
  public.is_verified_lender(auth.uid())
);

CREATE POLICY "borrower_docs_insert_borrower"
ON public.borrower_documents FOR INSERT TO authenticated
WITH CHECK (
  uploaded_by = auth.uid()
  AND verification_status = 'pending'
  AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = application_id
      AND borrower_id = auth.uid()
      AND status IN ('draft', 'submitted', 'more_information_required')
  )
);

CREATE POLICY "borrower_docs_update_lender"
ON public.borrower_documents FOR UPDATE TO authenticated
USING (
  public.is_verified_lender(auth.uid())
)
WITH CHECK (
  public.is_verified_lender(auth.uid())
);

CREATE POLICY "borrower_docs_delete_borrower"
ON public.borrower_documents FOR DELETE TO authenticated
USING (
  uploaded_by = auth.uid() AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = borrower_documents.application_id
      AND borrower_id = auth.uid()
      AND status = 'draft'
  )
);

-- F. LENDER DOCUMENTS
ALTER TABLE public.lender_documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "lender_docs_select_org_members"
ON public.lender_documents FOR SELECT TO authenticated
USING (
  public.is_org_member(auth.uid(), organization_id)
);

CREATE POLICY "lender_docs_insert_org_members"
ON public.lender_documents FOR INSERT TO authenticated
WITH CHECK (
  public.is_org_member(auth.uid(), organization_id)
  AND verification_status = 'pending'
);

-- G. LOAN ASSESSMENTS
ALTER TABLE public.loan_assessments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "loan_assessments_select_borrower"
ON public.loan_assessments FOR SELECT TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = loan_assessments.application_id AND borrower_id = auth.uid()
  )
);

CREATE POLICY "loan_assessments_select_lenders"
ON public.loan_assessments FOR SELECT TO authenticated
USING (
  public.is_verified_lender(auth.uid())
);

CREATE POLICY "loan_assessments_insert_lenders"
ON public.loan_assessments FOR INSERT TO authenticated
WITH CHECK (
  public.is_verified_lender(auth.uid())
);

-- H. LOAN REVIEWS
ALTER TABLE public.loan_reviews ENABLE ROW LEVEL SECURITY;

CREATE POLICY "loan_reviews_select_borrower"
ON public.loan_reviews FOR SELECT TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = loan_reviews.application_id AND borrower_id = auth.uid()
  )
);

CREATE POLICY "loan_reviews_select_lenders"
ON public.loan_reviews FOR SELECT TO authenticated
USING (
  public.is_verified_lender(auth.uid())
);

CREATE POLICY "loan_reviews_insert_lenders"
ON public.loan_reviews FOR INSERT TO authenticated
WITH CHECK (
  lender_id = auth.uid() AND public.is_verified_lender(auth.uid())
);

-- I. NOTIFICATIONS
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY "notifications_select_own"
ON public.notifications FOR SELECT TO authenticated
USING (user_id = auth.uid());

CREATE POLICY "notifications_update_own"
ON public.notifications FOR UPDATE TO authenticated
USING (user_id = auth.uid())
WITH CHECK (user_id = auth.uid());

CREATE POLICY "notifications_insert_own"
ON public.notifications FOR INSERT TO authenticated
WITH CHECK (user_id = auth.uid());
