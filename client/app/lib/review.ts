export type ImportStatus =
  | 'uploaded'
  | 'cleaning'
  | 'ocr_queued'
  | 'ocr_running'
  | 'ready_for_review'
  | 'ocr_failed'
  | 'review_ready'
  | 'checked';

export type TextSegment = {
  id: string;
  text: string;
  page_number: number;
  bbox: [number, number, number, number];
  bboxes?: [number, number, number, number][];
  raw_text?: string | null;
  corrected_text?: string | null;
  source_line_index?: number | null;
  source_row_index?: number | null;
};

export type ReviewFieldKey =
  | 'caseRedNo'
  | 'caseBlackNo'
  | 'courtName'
  | 'payAmount'
  | 'interestRate'
  | 'principalAmount'
  | 'filingDate'
  | 'attorneyFee'
  | 'tableName'
  | 'personId'
  | 'houseCode'
  | 'personName'
  | 'gender'
  | 'nationality'
  | 'birthDate'
  | 'age'
  | 'status'
  | 'motherName'
  | 'motherId'
  | 'motherNationality'
  | 'fatherName'
  | 'fatherId'
  | 'fatherNationality'
  | 'address'
  | 'postalCode'
  | 'moveInDate'
  | 'deceasedDate'
  | 'remark'
  | 'updateDate';

export type ReviewField = {
  value: string | null;
  pageNumber: number | null;
  bbox: [number, number, number, number] | null;
  source?: string | null;
  score?: number | null;
  confidence?: number | null;
  reviewStatus?: string | null;
  reviewNote?: string | null;
  alternatives?: Array<{
    value?: string | null;
    source?: string | null;
    sources?: string[];
    score?: number | null;
    confidence?: number | null;
    reason?: string | null;
  }>;
  appliedCorrections?: Array<{
    type?: string | null;
    source?: string | null;
    replacement?: string | null;
  }>;
};

export type ReviewKeywordHit = {
  id: string;
  pageNumber: number;
  text: string;
  displayText?: string | null;
  bbox: [number, number, number, number] | null;
};

export type ReviewData = {
  version: number;
  documentType?: string | null;
  visionModel?: string | null;
  visionErrors?: string[];
  flags?: {
    deceased?: boolean;
    lifeStatus?: string | null;
    deceasedDate?: string | null;
    matchedKeywords?: string[];
  } | null;
  fields: Partial<Record<ReviewFieldKey, ReviewField>>;
  keywordHits: ReviewKeywordHit[];
  qualityIssues?: Array<Record<string, unknown>>;
  visionFieldVerification?: {
    verifiedFields?: Array<Record<string, unknown>>;
    errors?: string[];
  };
};

export type ImportPageAsset = {
  page_number: number;
  original_preview_path: string;
  cleaned_preview_path: string;
  watermark_detected?: boolean | null;
  watermark_score?: number | null;
  cleaning_mode?: string | null;
  markdown: string | null;
  raw_markdown: string | null;
  corrected_markdown: string | null;
  original_markdown: string | null;
  cleaned_markdown: string | null;
  selected_markdown_source: 'original' | 'cleaned' | 'manual' | null;
  selected_markdown_score: number | null;
  selected_ocr_model: string | null;
  selected_candidate_source: string | null;
  ocr_candidate_scores: Array<{
    label: string;
    source: string;
    model: string;
    score: number;
  }>;
  original_markdown_score: number | null;
  cleaned_markdown_score: number | null;
  correction_model: string | null;
  correction_error: string | null;
  correction_similarity: number | null;
  original_ocr_error: string | null;
  cleaned_ocr_error: string | null;
  diff_similarity: number | null;
  suspicious_reasons: string[];
  processing_timing?: {
    unit?: string;
    stages_ms?: Record<string, number>;
    total_before_persistence_ms?: number;
  } | null;
  ocr_current_stage?: {
    key?: string;
    field?: string | null;
    started_at?: string;
  } | null;
  segments: TextSegment[];
};

export type ImportRecord = {
  id: string;
  source_filename: string;
  document_category: string | null;
  source_path: string;
  cleaned_file_path: string;
  source_fingerprint: string;
  status: ImportStatus;
  total_pages: number;
  created_at: string;
  updated_at: string;
  checked_at: string | null;
  checked_by: string | null;
  save_btn?: string | null;
  review_status?: string | null;
  assigned_to_user_id?: string | null;
  assigned_to_username?: string | null;
  assigned_at?: string | null;
  note: string | null;
  ocr_markdown: string | null;
  raw_ocr_markdown: string | null;
  corrected_ocr_markdown: string | null;
  original_ocr_markdown: string | null;
  cleaned_ocr_markdown: string | null;
  correction_model: string | null;
  ocr_error_message: string | null;
  ocr_completed_at: string | null;
  ocr_quality?: string | null;
  field_validation_issues?: Array<{
    field?: ReviewFieldKey | string | null;
    issue?: string | null;
    message?: string | null;
  }>;
  review_data?: ReviewData | null;
  pages: ImportPageAsset[];
};

export type AppConfig = {
  imports_source_dir: string;
  ocr_ready: boolean;
  extraction_ready: boolean;
  vision_ready?: boolean;
  ocr_model: string;
  ocr_compare_models?: string[];
  vision_model?: string;
  max_upload_mb: number;
  text_model: string;
};

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '') ??
  'http://localhost:8000';

export const importStatusLabels: Record<ImportStatus, string> = {
  uploaded: 'Uploaded',
  cleaning: 'Cleaning',
  ocr_queued: 'OCR queued',
  ocr_running: 'OCR running',
  ready_for_review: 'Ready for review',
  ocr_failed: 'OCR failed',
  review_ready: 'Review ready',
  checked: 'Checked',
};

export function formatDate(value: string | null) {
  if (!value) {
    return 'Not available';
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat('th-TH', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}
