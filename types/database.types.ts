export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  __InternalSupabase: {
    PostgrestVersion: "14.18"
  }
  public: {
    Tables: {
      borrower_documents: {
        Row: {
          application_id: string
          document_type: string
          file_name: string
          file_size: number | null
          id: string
          mime_type: string | null
          storage_path: string
          uploaded_at: string
          uploaded_by: string
          verification_status: string
        }
        Insert: {
          application_id: string
          document_type: string
          file_name: string
          file_size?: number | null
          id?: string
          mime_type?: string | null
          storage_path: string
          uploaded_at?: string
          uploaded_by: string
          verification_status?: string
        }
        Update: {
          application_id?: string
          document_type?: string
          file_name?: string
          file_size?: number | null
          id?: string
          mime_type?: string | null
          storage_path?: string
          uploaded_at?: string
          uploaded_by?: string
          verification_status?: string
        }
        Relationships: [
          {
            foreignKeyName: "borrower_documents_application_id_fkey"
            columns: ["application_id"]
            isOneToOne: false
            referencedRelation: "loan_applications"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "borrower_documents_uploaded_by_fkey"
            columns: ["uploaded_by"]
            isOneToOne: false
            referencedRelation: "borrower_profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      borrower_profiles: {
        Row: {
          aadhaar_number: string | null
          city: string
          created_at: string
          email: string
          full_name: string
          id: string
          is_active: boolean
          mobile_number: string
          state: string
          updated_at: string
        }
        Insert: {
          aadhaar_number?: string | null
          city: string
          created_at?: string
          email: string
          full_name: string
          id: string
          is_active?: boolean
          mobile_number: string
          state: string
          updated_at?: string
        }
        Update: {
          aadhaar_number?: string | null
          city?: string
          created_at?: string
          email?: string
          full_name?: string
          id?: string
          is_active?: boolean
          mobile_number?: string
          state?: string
          updated_at?: string
        }
        Relationships: []
      }
      lender_documents: {
        Row: {
          document_type: string
          file_name: string
          file_size: number | null
          id: string
          mime_type: string | null
          organization_id: string
          storage_path: string
          uploaded_at: string
          verification_status: string
        }
        Insert: {
          document_type: string
          file_name: string
          file_size?: number | null
          id?: string
          mime_type?: string | null
          organization_id: string
          storage_path: string
          uploaded_at?: string
          verification_status?: string
        }
        Update: {
          document_type?: string
          file_name?: string
          file_size?: number | null
          id?: string
          mime_type?: string | null
          organization_id?: string
          storage_path?: string
          uploaded_at?: string
          verification_status?: string
        }
        Relationships: [
          {
            foreignKeyName: "lender_documents_organization_id_fkey"
            columns: ["organization_id"]
            isOneToOne: false
            referencedRelation: "lender_organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      lender_organization_members: {
        Row: {
          created_at: string
          id: string
          lender_id: string
          member_role: string
          organization_id: string
        }
        Insert: {
          created_at?: string
          id?: string
          lender_id: string
          member_role?: string
          organization_id: string
        }
        Update: {
          created_at?: string
          id?: string
          lender_id?: string
          member_role?: string
          organization_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "lender_organization_members_lender_id_fkey"
            columns: ["lender_id"]
            isOneToOne: false
            referencedRelation: "lender_profiles"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "lender_organization_members_organization_id_fkey"
            columns: ["organization_id"]
            isOneToOne: false
            referencedRelation: "lender_organizations"
            referencedColumns: ["id"]
          },
        ]
      }
      lender_organizations: {
        Row: {
          city: string | null
          created_at: string
          id: string
          official_email: string
          official_phone: string | null
          organization_name: string
          organization_type: string
          registered_address: string | null
          registration_number: string
          state: string | null
          updated_at: string
          verification_status: string
          verified_at: string | null
        }
        Insert: {
          city?: string | null
          created_at?: string
          id?: string
          official_email: string
          official_phone?: string | null
          organization_name: string
          organization_type: string
          registered_address?: string | null
          registration_number: string
          state?: string | null
          updated_at?: string
          verification_status?: string
          verified_at?: string | null
        }
        Update: {
          city?: string | null
          created_at?: string
          id?: string
          official_email?: string
          official_phone?: string | null
          organization_name?: string
          organization_type?: string
          registered_address?: string | null
          registration_number?: string
          state?: string | null
          updated_at?: string
          verification_status?: string
          verified_at?: string | null
        }
        Relationships: []
      }
      lender_profiles: {
        Row: {
          city: string | null
          created_at: string
          email: string
          full_name: string
          id: string
          is_active: boolean
          lender_code: string
          phone: string | null
          state: string | null
          updated_at: string
        }
        Insert: {
          city?: string | null
          created_at?: string
          email: string
          full_name: string
          id: string
          is_active?: boolean
          lender_code: string
          phone?: string | null
          state?: string | null
          updated_at?: string
        }
        Update: {
          city?: string | null
          created_at?: string
          email?: string
          full_name?: string
          id?: string
          is_active?: boolean
          lender_code?: string
          phone?: string | null
          state?: string | null
          updated_at?: string
        }
        Relationships: []
      }
      loan_applications: {
        Row: {
          borrower_id: string
          created_at: string
          employment_type: string | null
          existing_monthly_debt: number | null
          id: string
          loan_amount: number
          loan_purpose: string
          monthly_income: number | null
          status: string
          updated_at: string
        }
        Insert: {
          borrower_id: string
          created_at?: string
          employment_type?: string | null
          existing_monthly_debt?: number | null
          id?: string
          loan_amount: number
          loan_purpose: string
          monthly_income?: number | null
          status?: string
          updated_at?: string
        }
        Update: {
          borrower_id?: string
          created_at?: string
          employment_type?: string | null
          existing_monthly_debt?: number | null
          id?: string
          loan_amount?: number
          loan_purpose?: string
          monthly_income?: number | null
          status?: string
          updated_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "loan_applications_borrower_id_fkey"
            columns: ["borrower_id"]
            isOneToOne: false
            referencedRelation: "borrower_profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      loan_assessments: {
        Row: {
          application_id: string
          assessment_summary: string | null
          created_at: string
          extracted_data: Json
          id: string
          model_version: string | null
          risk_level: string | null
          risk_score: number | null
        }
        Insert: {
          application_id: string
          assessment_summary?: string | null
          created_at?: string
          extracted_data?: Json
          id?: string
          model_version?: string | null
          risk_level?: string | null
          risk_score?: number | null
        }
        Update: {
          application_id?: string
          assessment_summary?: string | null
          created_at?: string
          extracted_data?: Json
          id?: string
          model_version?: string | null
          risk_level?: string | null
          risk_score?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "loan_assessments_application_id_fkey"
            columns: ["application_id"]
            isOneToOne: false
            referencedRelation: "loan_applications"
            referencedColumns: ["id"]
          },
        ]
      }
      loan_reviews: {
        Row: {
          application_id: string
          created_at: string
          decision: string
          id: string
          lender_id: string
          reason: string
        }
        Insert: {
          application_id: string
          created_at?: string
          decision: string
          id?: string
          lender_id: string
          reason: string
        }
        Update: {
          application_id?: string
          created_at?: string
          decision?: string
          id?: string
          lender_id?: string
          reason?: string
        }
        Relationships: [
          {
            foreignKeyName: "loan_reviews_application_id_fkey"
            columns: ["application_id"]
            isOneToOne: false
            referencedRelation: "loan_applications"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "loan_reviews_lender_id_fkey"
            columns: ["lender_id"]
            isOneToOne: false
            referencedRelation: "lender_profiles"
            referencedColumns: ["id"]
          },
        ]
      }
      notifications: {
        Row: {
          application_id: string | null
          created_at: string
          id: string
          is_read: boolean
          message: string
          title: string
          user_id: string
        }
        Insert: {
          application_id?: string | null
          created_at?: string
          id?: string
          is_read?: boolean
          message: string
          title: string
          user_id: string
        }
        Update: {
          application_id?: string | null
          created_at?: string
          id?: string
          is_read?: boolean
          message?: string
          title?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "notifications_application_id_fkey"
            columns: ["application_id"]
            isOneToOne: false
            referencedRelation: "loan_applications"
            referencedColumns: ["id"]
          },
        ]
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      admin_provision_lender: {
        Args: {
          p_city?: string
          p_custom_lender_code?: string
          p_email: string
          p_full_name: string
          p_member_role?: string
          p_organization_id?: string
          p_phone?: string
          p_state?: string
          p_user_id: string
        }
        Returns: Json
      }
      admin_verify_organization: {
        Args: { p_org_id: string; p_status: string }
        Returns: Json
      }
      generate_lender_code: { Args: never; Returns: string }
      get_auth_role: { Args: never; Returns: string }
      get_borrower_summary_for_lender: {
        Args: { p_application_id: string }
        Returns: Json
      }
      is_active_borrower: { Args: { p_user_id?: string }; Returns: boolean }
      is_active_lender: { Args: { p_user_id?: string }; Returns: boolean }
      is_org_member: {
        Args: { p_org_id: string; p_user_id: string }
        Returns: boolean
      }
      is_verified_lender: { Args: { p_user_id?: string }; Returns: boolean }
      lookup_lender_email: { Args: { p_lender_code: string }; Returns: string }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never
