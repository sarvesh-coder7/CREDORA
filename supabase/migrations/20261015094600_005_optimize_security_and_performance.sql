-- 005_optimize_security_and_performance.sql
-- Add covering index, tighten function permissions, fix search paths, and optimize RLS policies with (select auth.uid())

-- 1. Covering index for foreign key on notifications(application_id)
CREATE INDEX IF NOT EXISTS idx_notifications_application_id ON public.notifications(application_id);

-- 2. Ensure search_path is set on all functions
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

-- 3. Restrict function execution privileges
-- Revoke execute from PUBLIC, anon, authenticated for sensitive/administrative and trigger functions
REVOKE EXECUTE ON FUNCTION public.admin_provision_lender(uuid, text, text, text, uuid, text, text, text, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_provision_lender(uuid, text, text, text, uuid, text, text, text, text) TO service_role;

REVOKE EXECUTE ON FUNCTION public.admin_verify_organization(uuid, text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_verify_organization(uuid, text) TO service_role;

REVOKE EXECUTE ON FUNCTION public.generate_lender_code() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.generate_lender_code() TO service_role;

REVOKE EXECUTE ON FUNCTION public.protect_profile_fields() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.protect_loan_application_fields() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.protect_borrower_doc_status() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.handle_loan_review_submission() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.protect_notification_fields() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.handle_new_auth_user() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.check_lender_member_role() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.handle_updated_at() FROM PUBLIC, anon, authenticated;

-- Ensure public lender lookup is accessible to anon and authenticated
GRANT EXECUTE ON FUNCTION public.lookup_lender_email(text) TO anon, authenticated;

-- Grant RLS helper functions to authenticated
GRANT EXECUTE ON FUNCTION public.get_auth_role() TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_active_borrower(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_active_lender(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_org_member(uuid, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.is_verified_lender(uuid) TO authenticated;

-- 4. Optimize RLS policies using (select auth.uid()) to leverage InitPlan caching
ALTER POLICY "profiles_select_own" ON public.profiles
USING (id = (select auth.uid()));

ALTER POLICY "profiles_select_borrowers_by_verified_lender" ON public.profiles
USING (public.is_verified_lender((select auth.uid())) AND role = 'borrower');

ALTER POLICY "profiles_select_lender_colleagues" ON public.profiles
USING (
  role = 'lender' AND EXISTS (
    SELECT 1 FROM public.lender_organization_members m1
    JOIN public.lender_organization_members m2 ON m1.organization_id = m2.organization_id
    WHERE m1.lender_id = (select auth.uid()) AND m2.lender_id = profiles.id
  )
);

ALTER POLICY "profiles_insert_own_borrower" ON public.profiles
WITH CHECK (
  id = (select auth.uid()) AND role = 'borrower' AND lender_code IS NULL AND is_active = TRUE
);

ALTER POLICY "profiles_update_own" ON public.profiles
USING (id = (select auth.uid()))
WITH CHECK (id = (select auth.uid()));

ALTER POLICY "lender_org_select_members" ON public.lender_organizations
USING (
  public.is_org_member((select auth.uid()), id) OR
  (public.is_verified_lender((select auth.uid())) AND verification_status = 'verified')
);

ALTER POLICY "lender_org_members_select" ON public.lender_organization_members
USING (
  lender_id = (select auth.uid()) OR public.is_org_member((select auth.uid()), organization_id)
);

ALTER POLICY "loan_applications_select_borrower" ON public.loan_applications
USING (borrower_id = (select auth.uid()));

ALTER POLICY "loan_applications_select_verified_lenders" ON public.loan_applications
USING (public.is_verified_lender((select auth.uid())) AND status <> 'draft');

ALTER POLICY "loan_applications_insert_borrower" ON public.loan_applications
WITH CHECK (
  borrower_id = (select auth.uid())
  AND public.is_active_borrower((select auth.uid()))
  AND status IN ('draft', 'submitted')
);

ALTER POLICY "loan_applications_update_borrower" ON public.loan_applications
USING (
  borrower_id = (select auth.uid()) AND status IN ('draft', 'more_information_required')
)
WITH CHECK (
  borrower_id = (select auth.uid()) AND status IN ('draft', 'submitted')
);

ALTER POLICY "loan_applications_update_lender" ON public.loan_applications
USING (
  public.is_verified_lender((select auth.uid())) AND status <> 'draft'
)
WITH CHECK (
  public.is_verified_lender((select auth.uid())) AND status IN ('under_review', 'assessment_ready', 'approved', 'rejected', 'more_information_required')
);

ALTER POLICY "borrower_docs_select_owner" ON public.borrower_documents
USING (
  uploaded_by = (select auth.uid()) OR EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = borrower_documents.application_id AND borrower_id = (select auth.uid())
  )
);

ALTER POLICY "borrower_docs_select_verified_lenders" ON public.borrower_documents
USING (public.is_verified_lender((select auth.uid())));

ALTER POLICY "borrower_docs_insert_borrower" ON public.borrower_documents
WITH CHECK (
  uploaded_by = (select auth.uid())
  AND verification_status = 'pending'
  AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = application_id
      AND borrower_id = (select auth.uid())
      AND status IN ('draft', 'submitted', 'more_information_required')
  )
);

ALTER POLICY "borrower_docs_update_lender" ON public.borrower_documents
USING (public.is_verified_lender((select auth.uid())))
WITH CHECK (public.is_verified_lender((select auth.uid())));

ALTER POLICY "borrower_docs_delete_borrower" ON public.borrower_documents
USING (
  uploaded_by = (select auth.uid()) AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = borrower_documents.application_id
      AND borrower_id = (select auth.uid())
      AND status = 'draft'
  )
);

ALTER POLICY "lender_docs_select_org_members" ON public.lender_documents
USING (public.is_org_member((select auth.uid()), organization_id));

ALTER POLICY "lender_docs_insert_org_members" ON public.lender_documents
WITH CHECK (
  public.is_org_member((select auth.uid()), organization_id)
  AND verification_status = 'pending'
);

ALTER POLICY "loan_assessments_select_borrower" ON public.loan_assessments
USING (
  EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = loan_assessments.application_id AND borrower_id = (select auth.uid())
  )
);

ALTER POLICY "loan_assessments_select_lenders" ON public.loan_assessments
USING (public.is_verified_lender((select auth.uid())));

ALTER POLICY "loan_assessments_insert_lenders" ON public.loan_assessments
WITH CHECK (public.is_verified_lender((select auth.uid())));

ALTER POLICY "loan_reviews_select_borrower" ON public.loan_reviews
USING (
  EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id = loan_reviews.application_id AND borrower_id = (select auth.uid())
  )
);

ALTER POLICY "loan_reviews_select_lenders" ON public.loan_reviews
USING (public.is_verified_lender((select auth.uid())));

ALTER POLICY "loan_reviews_insert_lenders" ON public.loan_reviews
WITH CHECK (
  lender_id = (select auth.uid()) AND public.is_verified_lender((select auth.uid()))
);

ALTER POLICY "notifications_select_own" ON public.notifications
USING (user_id = (select auth.uid()));

ALTER POLICY "notifications_update_own" ON public.notifications
USING (user_id = (select auth.uid()))
WITH CHECK (user_id = (select auth.uid()));

ALTER POLICY "notifications_insert_own" ON public.notifications
WITH CHECK (user_id = (select auth.uid()));

-- Storage policies optimization
ALTER POLICY "storage_borrower_docs_select" ON storage.objects
USING (
  bucket_id = 'borrower-documents'
  AND (
    (string_to_array(name, '/'))[1] = (select auth.uid())::text
    OR (
      public.is_verified_lender((select auth.uid()))
      AND EXISTS (
        SELECT 1 FROM public.loan_applications
        WHERE id::text = (string_to_array(name, '/'))[2]
          AND status <> 'draft'
      )
    )
  )
);

ALTER POLICY "storage_borrower_docs_insert" ON storage.objects
WITH CHECK (
  bucket_id = 'borrower-documents'
  AND (string_to_array(name, '/'))[1] = (select auth.uid())::text
  AND public.is_active_borrower((select auth.uid()))
  AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id::text = (string_to_array(name, '/'))[2]
      AND borrower_id = (select auth.uid())
      AND status IN ('draft', 'submitted', 'more_information_required')
  )
);

ALTER POLICY "storage_borrower_docs_update" ON storage.objects
USING (
  bucket_id = 'borrower-documents'
  AND (string_to_array(name, '/'))[1] = (select auth.uid())::text
  AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id::text = (string_to_array(name, '/'))[2]
      AND borrower_id = (select auth.uid())
      AND status = 'draft'
  )
)
WITH CHECK (
  bucket_id = 'borrower-documents'
  AND (string_to_array(name, '/'))[1] = (select auth.uid())::text
);

ALTER POLICY "storage_borrower_docs_delete" ON storage.objects
USING (
  bucket_id = 'borrower-documents'
  AND (string_to_array(name, '/'))[1] = (select auth.uid())::text
  AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id::text = (string_to_array(name, '/'))[2]
      AND borrower_id = (select auth.uid())
      AND status = 'draft'
  )
);

ALTER POLICY "storage_lender_docs_select" ON storage.objects
USING (
  bucket_id = 'lender-documents'
  AND public.is_org_member((select auth.uid()), ((string_to_array(name, '/'))[1])::uuid)
);

ALTER POLICY "storage_lender_docs_insert" ON storage.objects
WITH CHECK (
  bucket_id = 'lender-documents'
  AND public.is_org_member((select auth.uid()), ((string_to_array(name, '/'))[1])::uuid)
);

ALTER POLICY "storage_lender_docs_update" ON storage.objects
USING (
  bucket_id = 'lender-documents'
  AND public.is_org_member((select auth.uid()), ((string_to_array(name, '/'))[1])::uuid)
)
WITH CHECK (
  bucket_id = 'lender-documents'
  AND public.is_org_member((select auth.uid()), ((string_to_array(name, '/'))[1])::uuid)
);

ALTER POLICY "storage_lender_docs_delete" ON storage.objects
USING (
  bucket_id = 'lender-documents'
  AND public.is_org_member((select auth.uid()), ((string_to_array(name, '/'))[1])::uuid)
);
