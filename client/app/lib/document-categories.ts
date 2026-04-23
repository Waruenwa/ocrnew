import type { ImportRecord } from './review';

export const DOCUMENT_CATEGORIES = [
  { value: 'judgment', label: 'Judgment' },
  { value: 'contract', label: 'Contract' },
  { value: 'invoice', label: 'Invoice' },
  { value: 'evidence', label: 'Evidence' },
  { value: 'other', label: 'Other' },
] as const;

export const CATEGORY_FILTER_OPTIONS = [
  { value: 'all', label: 'All categories' },
  ...DOCUMENT_CATEGORIES,
  { value: 'uncategorized', label: 'Uncategorized' },
] as const;

export function formatDocumentCategoryLabel(value: ImportRecord['document_category']) {
  if (!value) {
    return 'Uncategorized';
  }

  const matched = DOCUMENT_CATEGORIES.find((category) => category.value === value);
  if (matched) {
    return matched.label;
  }

  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((token) => token.charAt(0).toUpperCase() + token.slice(1))
    .join(' ');
}

