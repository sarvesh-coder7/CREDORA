-- 003_create_storage_buckets_and_policies.sql
-- Create private storage buckets and Row Level Security policies on storage.objects

-- 1. Create private storage buckets
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES 
  (
    'borrower-documents',
    'borrower-documents',
    FALSE,
    26214400, -- 25MB limit
    ARRAY['application/pdf', 'image/jpeg', 'image/png', 'image/webp']
  ),
  (
    'lender-documents',
    'lender-documents',
    FALSE,
    26214400, -- 25MB limit
    ARRAY['application/pdf', 'image/jpeg', 'image/png', 'image/webp']
  )
ON CONFLICT (id) DO UPDATE
SET
  public = FALSE,
  file_size_limit = 26214400,
  allowed_mime_types = ARRAY['application/pdf', 'image/jpeg', 'image/png', 'image/webp'];

-- 2. Storage RLS Policies for borrower-documents
CREATE POLICY "storage_borrower_docs_select"
ON storage.objects FOR SELECT TO authenticated
USING (
  bucket_id = 'borrower-documents'
  AND (
    (string_to_array(name, '/'))[1] = auth.uid()::text
    OR (
      public.is_verified_lender(auth.uid())
      AND EXISTS (
        SELECT 1 FROM public.loan_applications
        WHERE id::text = (string_to_array(name, '/'))[2]
          AND status <> 'draft'
      )
    )
  )
);

CREATE POLICY "storage_borrower_docs_insert"
ON storage.objects FOR INSERT TO authenticated
WITH CHECK (
  bucket_id = 'borrower-documents'
  AND (string_to_array(name, '/'))[1] = auth.uid()::text
  AND public.is_active_borrower(auth.uid())
  AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id::text = (string_to_array(name, '/'))[2]
      AND borrower_id = auth.uid()
      AND status IN ('draft', 'submitted', 'more_information_required')
  )
);

CREATE POLICY "storage_borrower_docs_update"
ON storage.objects FOR UPDATE TO authenticated
USING (
  bucket_id = 'borrower-documents'
  AND (string_to_array(name, '/'))[1] = auth.uid()::text
  AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id::text = (string_to_array(name, '/'))[2]
      AND borrower_id = auth.uid()
      AND status = 'draft'
  )
)
WITH CHECK (
  bucket_id = 'borrower-documents'
  AND (string_to_array(name, '/'))[1] = auth.uid()::text
);

CREATE POLICY "storage_borrower_docs_delete"
ON storage.objects FOR DELETE TO authenticated
USING (
  bucket_id = 'borrower-documents'
  AND (string_to_array(name, '/'))[1] = auth.uid()::text
  AND EXISTS (
    SELECT 1 FROM public.loan_applications
    WHERE id::text = (string_to_array(name, '/'))[2]
      AND borrower_id = auth.uid()
      AND status = 'draft'
  )
);

-- 3. Storage RLS Policies for lender-documents
CREATE POLICY "storage_lender_docs_select"
ON storage.objects FOR SELECT TO authenticated
USING (
  bucket_id = 'lender-documents'
  AND public.is_org_member(auth.uid(), ((string_to_array(name, '/'))[1])::uuid)
);

CREATE POLICY "storage_lender_docs_insert"
ON storage.objects FOR INSERT TO authenticated
WITH CHECK (
  bucket_id = 'lender-documents'
  AND public.is_org_member(auth.uid(), ((string_to_array(name, '/'))[1])::uuid)
);

CREATE POLICY "storage_lender_docs_update"
ON storage.objects FOR UPDATE TO authenticated
USING (
  bucket_id = 'lender-documents'
  AND public.is_org_member(auth.uid(), ((string_to_array(name, '/'))[1])::uuid)
)
WITH CHECK (
  bucket_id = 'lender-documents'
  AND public.is_org_member(auth.uid(), ((string_to_array(name, '/'))[1])::uuid)
);

CREATE POLICY "storage_lender_docs_delete"
ON storage.objects FOR DELETE TO authenticated
USING (
  bucket_id = 'lender-documents'
  AND public.is_org_member(auth.uid(), ((string_to_array(name, '/'))[1])::uuid)
);
