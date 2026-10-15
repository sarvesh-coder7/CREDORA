-- 006_replace_profiles_with_borrower_and_lender_profiles.sql
-- Replaces shared profiles table with borrower_profiles and lender_profiles
-- Secures Aadhaar data, re-points foreign keys, and updates auth synchronization

-- 1. Create borrower_profiles table
CREATE TABLE IF NOT EXISTS public.borrower_profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL,
  mobile_number TEXT NOT NULL,
  state TEXT NOT NULL,
  city TEXT NOT NULL,
  aadhaar_number TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_borrower_mobile CHECK (
    mobile_number ~ '^[6-9][0-9]{9}$' OR mobile_number ~ '^\+91[6-9][0-9]{9}$'
  ),
  CONSTRAINT chk_borrower_aadhaar CHECK (
    aadhaar_number IS NULL OR 
    aadhaar_number ~ '^[0-9]{12}$' OR 
    aadhaar_number ~ '^[0-9]{4}\s[0-9]{4}\s[0-9]{4}$' OR 
    aadhaar_number ~ '^XXXX-XXXX-[0-9]{4}$'
  )
);

CREATE INDEX IF NOT EXISTS idx_borrower_profiles_email ON public.borrower_profiles(email);
CREATE INDEX IF NOT EXISTS idx_borrower_profiles_mobile ON public.borrower_profiles(mobile_number);
CREATE INDEX IF NOT EXISTS idx_borrower_profiles_state ON public.borrower_profiles(state);
CREATE INDEX IF NOT EXISTS idx_borrower_profiles_city ON public.borrower_profiles(city);

CREATE OR REPLACE TRIGGER trg_borrower_profiles_updated_at
BEFORE UPDATE ON public.borrower_profiles
FOR EACH ROW
EXECUTE FUNCTION public.handle_updated_at();

-- 2. Create lender_profiles table to preserve lender institutions and reviews
CREATE TABLE IF NOT EXISTS public.lender_profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  lender_code TEXT NOT NULL UNIQUE,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL,
  phone TEXT,
  state TEXT,
  city TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lender_profiles_code ON public.lender_profiles(lender_code);
CREATE INDEX IF NOT EXISTS idx_lender_profiles_email ON public.lender_profiles(email);

CREATE OR REPLACE TRIGGER trg_lender_profiles_updated_at
BEFORE UPDATE ON public.lender_profiles
FOR EACH ROW
EXECUTE FUNCTION public.handle_updated_at();

-- 3. Re-point foreign keys
ALTER TABLE public.loan_applications DROP CONSTRAINT IF EXISTS loan_applications_borrower_id_fkey;
ALTER TABLE public.loan_applications ADD CONSTRAINT loan_applications_borrower_id_fkey FOREIGN KEY (borrower_id) REFERENCES public.borrower_profiles(id) ON DELETE CASCADE;

ALTER TABLE public.borrower_documents DROP CONSTRAINT IF EXISTS borrower_documents_uploaded_by_fkey;
ALTER TABLE public.borrower_documents ADD CONSTRAINT borrower_documents_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.borrower_profiles(id) ON DELETE CASCADE;

ALTER TABLE public.lender_organization_members DROP CONSTRAINT IF EXISTS lender_organization_members_lender_id_fkey;
ALTER TABLE public.lender_organization_members ADD CONSTRAINT lender_organization_members_lender_id_fkey FOREIGN KEY (lender_id) REFERENCES public.lender_profiles(id) ON DELETE CASCADE;

ALTER TABLE public.loan_reviews DROP CONSTRAINT IF EXISTS loan_reviews_lender_id_fkey;
ALTER TABLE public.loan_reviews ADD CONSTRAINT loan_reviews_lender_id_fkey FOREIGN KEY (lender_id) REFERENCES public.lender_profiles(id) ON DELETE CASCADE;

ALTER TABLE public.notifications DROP CONSTRAINT IF EXISTS notifications_user_id_fkey;
ALTER TABLE public.notifications ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

-- 4. Update helper functions
CREATE OR REPLACE FUNCTION public.get_auth_role()
RETURNS TEXT
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.borrower_profiles WHERE id = auth.uid()) THEN
    RETURN 'borrower';
  ELSIF EXISTS (SELECT 1 FROM public.lender_profiles WHERE id = auth.uid()) THEN
    RETURN 'lender';
  END IF;
  RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.is_active_borrower(p_user_id UUID DEFAULT auth.uid())
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.borrower_profiles
    WHERE id = p_user_id
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
    SELECT 1 FROM public.lender_profiles
    WHERE id = p_user_id
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
    FROM public.lender_profiles lp
    JOIN public.lender_organization_members lom ON lom.lender_id = lp.id
    WHERE lp.id = p_user_id
      AND lom.organization_id = p_org_id
      AND lp.is_active = TRUE
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
    FROM public.lender_profiles lp
    JOIN public.lender_organization_members lom ON lom.lender_id = lp.id
    JOIN public.lender_organizations lo ON lo.id = lom.organization_id
    WHERE lp.id = p_user_id
      AND lp.is_active = TRUE
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
  FROM public.lender_profiles
  WHERE UPPER(lender_code) = UPPER(TRIM(p_lender_code))
    AND is_active = TRUE;
$$;

CREATE OR REPLACE FUNCTION public.check_lender_member_role()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.lender_profiles
    WHERE id = NEW.lender_id
  ) THEN
    RAISE EXCEPTION 'Linked profile % must exist in lender_profiles', NEW.lender_id;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.protect_loan_application_fields()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF public.is_active_borrower(auth.uid()) THEN
    IF NEW.borrower_id IS DISTINCT FROM OLD.borrower_id THEN
      RAISE EXCEPTION 'Cannot modify loan application ownership.';
    END IF;
    IF NEW.status IN ('under_review', 'assessment_ready', 'approved', 'rejected') AND NEW.status IS DISTINCT FROM OLD.status THEN
      RAISE EXCEPTION 'Borrowers are not permitted to change status to %.', NEW.status;
    END IF;
    IF OLD.status IN ('under_review', 'assessment_ready', 'approved', 'rejected') THEN
      RAISE EXCEPTION 'Application cannot be modified once in review or decided.';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.protect_borrower_doc_status()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF public.is_active_borrower(auth.uid()) THEN
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

CREATE OR REPLACE FUNCTION public.admin_provision_lender(
  p_user_id UUID,
  p_full_name TEXT,
  p_email TEXT,
  p_phone TEXT DEFAULT NULL,
  p_organization_id UUID DEFAULT NULL,
  p_member_role TEXT DEFAULT 'lender',
  p_custom_lender_code TEXT DEFAULT NULL,
  p_state TEXT DEFAULT NULL,
  p_city TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_code TEXT;
  v_profile_id UUID;
BEGIN
  IF (auth.jwt() ->> 'role') = 'authenticated' OR (auth.jwt() ->> 'role') = 'anon' THEN
    RAISE EXCEPTION 'Only administrative service role can provision lenders.';
  END IF;

  v_code := COALESCE(p_custom_lender_code, public.generate_lender_code());

  INSERT INTO public.lender_profiles (
    id,
    lender_code,
    full_name,
    email,
    phone,
    state,
    city,
    is_active
  ) VALUES (
    p_user_id,
    v_code,
    p_full_name,
    p_email,
    p_phone,
    p_state,
    p_city,
    TRUE
  )
  ON CONFLICT (id) DO UPDATE
  SET
    lender_code = v_code,
    full_name = EXCLUDED.full_name,
    email = EXCLUDED.email,
    phone = COALESCE(EXCLUDED.phone, public.lender_profiles.phone),
    state = COALESCE(EXCLUDED.state, public.lender_profiles.state),
    city = COALESCE(EXCLUDED.city, public.lender_profiles.city),
    is_active = TRUE,
    updated_at = NOW()
  RETURNING id INTO v_profile_id;

  IF p_organization_id IS NOT NULL THEN
    INSERT INTO public.lender_organization_members (
      organization_id,
      lender_id,
      member_role
    ) VALUES (
      p_organization_id,
      v_profile_id,
      p_member_role
    )
    ON CONFLICT (organization_id, lender_id) DO UPDATE
    SET member_role = EXCLUDED.member_role;
  END IF;

  RETURN jsonb_build_object(
    'lender_id', v_profile_id,
    'lender_code', v_code,
    'email', p_email,
    'organization_id', p_organization_id
  );
END;
$$;

-- 5. Automatic Auth User sync trigger
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_role TEXT;
  v_lender_code TEXT;
  v_full_name TEXT;
  v_mobile TEXT;
  v_city TEXT;
  v_state TEXT;
  v_aadhaar TEXT;
  v_is_admin_call BOOLEAN;
BEGIN
  v_is_admin_call := (COALESCE(current_setting('request.jwt.claim.role', true), '') = 'service_role')
                     OR (COALESCE(current_setting('role', true), '') = 'service_role')
                     OR (auth.role() = 'service_role');

  v_role := COALESCE(NEW.raw_user_meta_data->>'role', 'borrower');

  IF v_is_admin_call AND (v_role = 'lender') THEN
    v_lender_code := COALESCE(NEW.raw_user_meta_data->>'lender_code', public.generate_lender_code());
    v_full_name := COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', split_part(NEW.email, '@', 1));
    
    INSERT INTO public.lender_profiles (
      id,
      lender_code,
      full_name,
      email,
      phone,
      state,
      city,
      is_active
    ) VALUES (
      NEW.id,
      v_lender_code,
      v_full_name,
      NEW.email,
      NEW.raw_user_meta_data->>'phone',
      NEW.raw_user_meta_data->>'state',
      NEW.raw_user_meta_data->>'city',
      TRUE
    )
    ON CONFLICT (id) DO UPDATE
    SET
      email = EXCLUDED.email,
      full_name = COALESCE(public.lender_profiles.full_name, EXCLUDED.full_name),
      phone = COALESCE(public.lender_profiles.phone, EXCLUDED.phone),
      updated_at = NOW();

  ELSE
    v_full_name := COALESCE(
      NEW.raw_user_meta_data->>'full_name',
      NEW.raw_user_meta_data->>'name',
      split_part(NEW.email, '@', 1)
    );
    v_mobile := COALESCE(
      NEW.raw_user_meta_data->>'mobile_number',
      NEW.raw_user_meta_data->>'phone',
      NEW.phone,
      ''
    );
    v_city := COALESCE(NEW.raw_user_meta_data->>'city', '');
    v_state := COALESCE(NEW.raw_user_meta_data->>'state', '');
    v_aadhaar := NEW.raw_user_meta_data->>'aadhaar_number';

    INSERT INTO public.borrower_profiles (
      id,
      full_name,
      email,
      mobile_number,
      state,
      city,
      aadhaar_number,
      is_active
    ) VALUES (
      NEW.id,
      v_full_name,
      NEW.email,
      v_mobile,
      v_state,
      v_city,
      v_aadhaar,
      TRUE
    )
    ON CONFLICT (id) DO UPDATE
    SET
      email = EXCLUDED.email,
      full_name = COALESCE(public.borrower_profiles.full_name, EXCLUDED.full_name),
      mobile_number = CASE WHEN EXCLUDED.mobile_number <> '' THEN EXCLUDED.mobile_number ELSE public.borrower_profiles.mobile_number END,
      state = CASE WHEN EXCLUDED.state <> '' THEN EXCLUDED.state ELSE public.borrower_profiles.state END,
      city = CASE WHEN EXCLUDED.city <> '' THEN EXCLUDED.city ELSE public.borrower_profiles.city END,
      aadhaar_number = COALESCE(EXCLUDED.aadhaar_number, public.borrower_profiles.aadhaar_number),
      updated_at = NOW();
  END IF;

  RETURN NEW;
END;
$$;

-- 6. Row Level Security Policies
ALTER TABLE public.borrower_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lender_profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "borrower_profiles_select_own"
ON public.borrower_profiles FOR SELECT TO authenticated
USING (id = (select auth.uid()));

CREATE POLICY "borrower_profiles_insert_own"
ON public.borrower_profiles FOR INSERT TO authenticated
WITH CHECK (id = (select auth.uid()));

CREATE POLICY "borrower_profiles_update_own"
ON public.borrower_profiles FOR UPDATE TO authenticated
USING (id = (select auth.uid()))
WITH CHECK (id = (select auth.uid()));

CREATE POLICY "lender_profiles_select_own"
ON public.lender_profiles FOR SELECT TO authenticated
USING (
  id = (select auth.uid()) OR
  EXISTS (
    SELECT 1 FROM public.lender_organization_members m1
    JOIN public.lender_organization_members m2 ON m1.organization_id = m2.organization_id
    WHERE m1.lender_id = (select auth.uid()) AND m2.lender_id = lender_profiles.id
  )
);

CREATE POLICY "lender_profiles_update_own"
ON public.lender_profiles FOR UPDATE TO authenticated
USING (id = (select auth.uid()))
WITH CHECK (id = (select auth.uid()));

-- 7. Secure Aadhaar Protection RPC for verified lenders
CREATE OR REPLACE FUNCTION public.get_borrower_summary_for_lender(p_application_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_res JSONB;
BEGIN
  IF NOT public.is_verified_lender(auth.uid()) THEN
    RAISE EXCEPTION 'Access denied. Only verified lenders may access borrower application data.';
  END IF;

  SELECT jsonb_build_object(
    'id', bp.id,
    'full_name', bp.full_name,
    'email', bp.email,
    'mobile_number', bp.mobile_number,
    'state', bp.state,
    'city', bp.city,
    'masked_aadhaar', CASE 
      WHEN bp.aadhaar_number IS NOT NULL AND LENGTH(bp.aadhaar_number) >= 4 THEN
        'XXXX-XXXX-' || RIGHT(REGEXP_REPLACE(bp.aadhaar_number, '\s+', '', 'g'), 4)
      ELSE NULL
    END,
    'is_active', bp.is_active,
    'created_at', bp.created_at
  ) INTO v_res
  FROM public.loan_applications la
  JOIN public.borrower_profiles bp ON bp.id = la.borrower_id
  WHERE la.id = p_application_id
    AND la.status <> 'draft';

  RETURN v_res;
END;
$$;

-- 8. Permissions
REVOKE ALL ON public.borrower_profiles FROM anon;
REVOKE ALL ON public.lender_profiles FROM anon;
GRANT SELECT, INSERT, UPDATE ON public.borrower_profiles TO authenticated;
GRANT SELECT, UPDATE ON public.lender_profiles TO authenticated;
GRANT EXECUTE ON FUNCTION public.get_borrower_summary_for_lender(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION public.lookup_lender_email(text) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION public.is_active_borrower(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_active_lender(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_org_member(uuid, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_verified_lender(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.get_auth_role() TO authenticated;
