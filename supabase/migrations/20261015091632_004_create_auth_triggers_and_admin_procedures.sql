-- 004_create_auth_triggers_and_admin_procedures.sql
-- Auth event triggers, borrower self-registration profile synchronization, and admin provisioning procedures

-- 1. Automatic profile synchronization trigger for Supabase Auth
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
  v_phone TEXT;
  v_city TEXT;
  v_state TEXT;
  v_is_admin_call BOOLEAN;
BEGIN
  -- Determine if the creation was performed by administrative service_role
  v_is_admin_call := (COALESCE(current_setting('request.jwt.claim.role', true), '') = 'service_role')
                     OR (COALESCE(current_setting('role', true), '') = 'service_role')
                     OR (auth.role() = 'service_role');

  -- Borrowers can self-register; Lenders CANNOT self-register (only service_role can create lender accounts)
  IF v_is_admin_call AND (NEW.raw_user_meta_data->>'role' = 'lender') THEN
    v_role := 'lender';
    v_lender_code := COALESCE(NEW.raw_user_meta_data->>'lender_code', public.generate_lender_code());
  ELSE
    v_role := 'borrower';
    v_lender_code := NULL;
  END IF;

  v_full_name := COALESCE(
    NEW.raw_user_meta_data->>'full_name',
    NEW.raw_user_meta_data->>'name',
    split_part(NEW.email, '@', 1)
  );
  v_phone := NEW.raw_user_meta_data->>'phone';
  v_city := NEW.raw_user_meta_data->>'city';
  v_state := NEW.raw_user_meta_data->>'state';

  INSERT INTO public.profiles (
    id,
    lender_code,
    full_name,
    email,
    phone,
    role,
    state,
    city,
    is_active
  ) VALUES (
    NEW.id,
    v_lender_code,
    v_full_name,
    NEW.email,
    v_phone,
    v_role,
    v_state,
    v_city,
    TRUE
  )
  ON CONFLICT (id) DO UPDATE
  SET
    email = EXCLUDED.email,
    full_name = COALESCE(public.profiles.full_name, EXCLUDED.full_name),
    phone = COALESCE(public.profiles.phone, EXCLUDED.phone),
    updated_at = NOW();

  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER on_auth_user_created
AFTER INSERT ON auth.users
FOR EACH ROW
EXECUTE FUNCTION public.handle_new_auth_user();

-- 2. Administrative Procedure to safely provision a lender account and link to organization
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

  INSERT INTO public.profiles (
    id,
    lender_code,
    full_name,
    email,
    phone,
    role,
    state,
    city,
    is_active
  ) VALUES (
    p_user_id,
    v_code,
    p_full_name,
    p_email,
    p_phone,
    'lender',
    p_state,
    p_city,
    TRUE
  )
  ON CONFLICT (id) DO UPDATE
  SET
    lender_code = v_code,
    role = 'lender',
    full_name = EXCLUDED.full_name,
    email = EXCLUDED.email,
    phone = COALESCE(EXCLUDED.phone, public.profiles.phone),
    state = COALESCE(EXCLUDED.state, public.profiles.state),
    city = COALESCE(EXCLUDED.city, public.profiles.city),
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

-- 3. Administrative Procedure to verify a lender organization
CREATE OR REPLACE FUNCTION public.admin_verify_organization(
  p_org_id UUID,
  p_status TEXT -- 'verified', 'rejected', 'pending'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  IF (auth.jwt() ->> 'role') = 'authenticated' OR (auth.jwt() ->> 'role') = 'anon' THEN
    RAISE EXCEPTION 'Only administrative personnel can verify lender organizations.';
  END IF;

  IF p_status NOT IN ('pending', 'verified', 'rejected') THEN
    RAISE EXCEPTION 'Invalid verification status: %', p_status;
  END IF;

  UPDATE public.lender_organizations
  SET
    verification_status = p_status,
    verified_at = CASE WHEN p_status = 'verified' THEN NOW() ELSE NULL END,
    updated_at = NOW()
  WHERE id = p_org_id;

  RETURN jsonb_build_object(
    'organization_id', p_org_id,
    'status', p_status,
    'updated_at', NOW()
  );
END;
$$;
