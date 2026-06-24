'use client';

import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Box,
  Flex,
  Grid,
  Heading,
  Image,
  Spinner,
  Stack,
  Text,
} from '@chakra-ui/react';

import { getAuthHeaders } from '../../lib/auth';
import {
  API_BASE_URL,
  type ImportPageAsset,
  type ImportRecord,
  type ReviewData,
  type ReviewField,
  type ReviewFieldKey,
  type ReviewKeywordHit,
  type TextSegment,
} from '../../lib/review';

type KeywordHit = {
  id: string;
  pageNumber: number;
  text: string;
  displayText?: string;
  bbox: [number, number, number, number] | null;
};

type HighlightTarget = {
  pageNumber: number;
  bbox: [number, number, number, number];
};

type ParsedFieldKey = Extract<
  ReviewFieldKey,
  | 'caseRedNo'
  | 'caseBlackNo'
  | 'courtName'
  | 'payAmount'
  | 'interestRate'
  | 'principalAmount'
  | 'filingDate'
  | 'attorneyFee'
>;

type ParsedClause = {
  caseRedNo: string | null;
  caseBlackNo: string | null;
  courtName: string | null;
  payAmount: string | null;
  interestRate: string | null;
  principalAmount: string | null;
  filingDate: string | null;
  attorneyFee: string | null;
  fieldAnchors: Partial<Record<ParsedFieldKey, HighlightTarget>>;
  keywordHits: KeywordHit[];
};

type CaseHeaderInfo = {
  redNo: string | null;
  blackNo: string | null;
  redAnchor: HighlightTarget | null;
  blackAnchor: HighlightTarget | null;
};

type CourtInfo = {
  name: string;
  anchor: HighlightTarget | null;
};

type ResultFieldConfig = {
  key: ParsedFieldKey;
  label: string;
};

const KEYWORD = 'พิพากษาให้จำเลย';
const CASE_RED_PATTERNS = [
  /คดีหมายเลขแดงที่\s*([^\s,]+(?:\s+[0-9๐-๙\/\.\-]+){0,2})/,
  /คดีแดงเลขที่\s*([^\s,]+(?:\s+[0-9๐-๙\/\.\-]+){0,2})/,
  /คดี(?:หมายเลข)?แดงที่\s*([^\s,]+(?:\s+[0-9๐-๙\/\.\-]+){0,2})/,
];
const CASE_RED_MARKERS = ['คดีหมายเลขแดงที่', 'คดีแดงเลขที่'];
const CASE_BLACK_PATTERNS = [
  /คดีหมายเลขดำที่\s*([^\s,]+(?:\s+[0-9๐-๙\/\.\-]+){0,2})/,
  /คดีดำเลขที่\s*([^\s,]+(?:\s+[0-9๐-๙\/\.\-]+){0,2})/,
  /คดี(?:หมายเลข)?ดำที่\s*([^\s,]+(?:\s+[0-9๐-๙\/\.\-]+){0,2})/,
];
const CASE_BLACK_MARKERS = ['คดีหมายเลขดำที่', 'คดีดำเลขที่'];
const THAI_RED_MARKER = '\u0e41\u0e14\u0e07';
const THAI_BLACK_MARKER = '\u0e14\u0e33';
const THAI_MONTHS = [
  'มกราคม',
  'กุมภาพันธ์',
  'มีนาคม',
  'เมษายน',
  'พฤษภาคม',
  'มิถุนายน',
  'กรกฎาคม',
  'สิงหาคม',
  'กันยายน',
  'ตุลาคม',
  'พฤศจิกายน',
  'ธันวาคม',
];

const THAI_MONTH_LOOKUP = new Map<string, number>([
  ['มกราคม', 0],
  ['มกรา', 0],
  ['ม.ค.', 0],
  ['มค', 0],
  ['กุมภาพันธ์', 1],
  ['กุมภา', 1],
  ['ก.พ.', 1],
  ['กพ', 1],
  ['มีนาคม', 2],
  ['มีนา', 2],
  ['มี.ค.', 2],
  ['มีค', 2],
  ['เมษายน', 3],
  ['เมษา', 3],
  ['เม.ย.', 3],
  ['เมย', 3],
  ['พฤษภาคม', 4],
  ['พฤษภา', 4],
  ['พ.ค.', 4],
  ['พค', 4],
  ['มิถุนายน', 5],
  ['มิถุนา', 5],
  ['มิ.ย.', 5],
  ['มิย', 5],
  ['กรกฎาคม', 6],
  ['กรกฎา', 6],
  ['ก.ค.', 6],
  ['กค', 6],
  ['สิงหาคม', 7],
  ['สิงหา', 7],
  ['ส.ค.', 7],
  ['สค', 7],
  ['กันยายน', 8],
  ['กันยา', 8],
  ['ก.ย.', 8],
  ['กย', 8],
  ['ตุลาคม', 9],
  ['ตุลา', 9],
  ['ต.ค.', 9],
  ['ตค', 9],
  ['พฤศจิกายน', 10],
  ['พฤศจิกา', 10],
  ['พ.ย.', 10],
  ['พย', 10],
  ['ธันวาคม', 11],
  ['ธันวา', 11],
  ['ธ.ค.', 11],
  ['ธค', 11],
]);

const RESULT_FIELDS: ResultFieldConfig[] = [
  { key: 'caseRedNo', label: 'คดีแดงเลขที่' },
  { key: 'payAmount', label: 'พพย.ให้ชำระ' },
  { key: 'interestRate', label: 'พร้อมดอกเบี้ย (%/ปี)' },
  { key: 'principalAmount', label: 'ของคงเหลือเงิน' },
  { key: 'filingDate', label: 'ตั้งต้นวันที่' },
  { key: 'attorneyFee', label: 'ค่าทนายความ' },
  { key: 'caseBlackNo', label: 'คดีดำเลขที่' },
  { key: 'courtName', label: 'ศาล' },
];
const PARSED_FIELD_KEYS: ParsedFieldKey[] = [
  'caseRedNo',
  'caseBlackNo',
  'courtName',
  'payAmount',
  'interestRate',
  'principalAmount',
  'filingDate',
  'attorneyFee',
];

const HIT_OVERRIDE_STORAGE_VERSION = 'v2';
const JUDGMENT_SOURCE_FIELDS = new Set<ParsedFieldKey>([
  'payAmount',
  'interestRate',
  'principalAmount',
  'filingDate',
  'attorneyFee',
]);
const HEADER_NAVIGATION_FIELDS = new Set<ParsedFieldKey>([
  'caseRedNo',
  'caseBlackNo',
  'courtName',
]);
const FIRST_PAGE_FALLBACK_ANCHORS: Partial<
  Record<ParsedFieldKey, [number, number, number, number]>
> = {
  caseBlackNo: [0.603, 0.13, 0.887, 0.145],
  caseRedNo: [0.603, 0.17, 0.887, 0.186],
  courtName: [0.459, 0.279, 0.59, 0.295],
};

const panelStyles = {
  borderWidth: '1px',
  borderColor: 'rgba(73, 59, 36, 0.12)',
  borderRadius: '28px',
  bg: 'rgba(255, 255, 255, 0.9)',
  boxShadow: '0 18px 42px rgba(31, 26, 20, 0.08)',
};

export default function ImportNewInspectPage() {
  const params = useParams<{ importId: string }>();
  const searchParams = useSearchParams();
  const importId = Array.isArray(params?.importId)
    ? params.importId[0]
    : params?.importId;
  const isStaffSource = searchParams.get('source') === 'staff';

  const [record, setRecord] = useState<ImportRecord | null>(null);
  const [activePageNumber, setActivePageNumber] = useState(1);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [activeFieldKey, setActiveFieldKey] = useState<ParsedFieldKey | null>(
    null,
  );
  const [activeHitId, setActiveHitId] = useState<string | null>(null);
  const [activeHighlight, setActiveHighlight] =
    useState<HighlightTarget | null>(null);

  const [editingFieldKey, setEditingFieldKey] = useState<ParsedFieldKey | null>(
    null,
  );
  const [editingFieldValue, setEditingFieldValue] = useState('');
  const [fieldOverrides, setFieldOverrides] = useState<
    Partial<Record<ParsedFieldKey, string>>
  >({});

  const [editingHitId, setEditingHitId] = useState<string | null>(null);
  const [editingHitValue, setEditingHitValue] = useState('');
  const [hitOverrides, setHitOverrides] = useState<Record<string, string>>({});
  const [loadedPreviewUrl, setLoadedPreviewUrl] = useState('');
  const [isCompletingStaffRecord, setIsCompletingStaffRecord] = useState(false);
  const [staffActionMessage, setStaffActionMessage] = useState<string | null>(
    null,
  );
  const [detectedHeaderAnchors, setDetectedHeaderAnchors] = useState<
    Partial<Record<ParsedFieldKey, HighlightTarget>>
  >({});
  const previewScrollRef = useRef<HTMLDivElement | null>(null);
  const previewContentRef = useRef<HTMLDivElement | null>(null);
  const resultPanelRef = useRef<HTMLDivElement | null>(null);
  const fieldCardRefs = useRef<
    Partial<Record<ParsedFieldKey, HTMLDivElement | null>>
  >({});
  const hitCardRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    if (!importId) {
      return;
    }
    void loadImport(importId);
  }, [importId]);

  useEffect(() => {
    if (!record?.pages?.length) {
      return;
    }
    const exists = record.pages.some(
      (page) => page.page_number === activePageNumber,
    );
    if (!exists) {
      setActivePageNumber(record.pages[0].page_number);
    }
  }, [record, activePageNumber]);

  useEffect(() => {
    if (!importId) {
      return;
    }

    try {
      const raw = window.localStorage.getItem(
        `imports-new-overrides:${importId}`,
      );
      if (!raw) {
        setFieldOverrides({});
        return;
      }
      const parsedRaw = JSON.parse(raw) as Partial<
        Record<ParsedFieldKey, string>
      >;
      setFieldOverrides(parsedRaw);
    } catch {
      setFieldOverrides({});
    }
  }, [importId]);

  useEffect(() => {
    if (!importId) {
      return;
    }
    window.localStorage.setItem(
      `imports-new-overrides:${importId}`,
      JSON.stringify(fieldOverrides),
    );
  }, [fieldOverrides, importId]);

  useEffect(() => {
    if (!importId) {
      return;
    }

    try {
      const raw = window.localStorage.getItem(
        `imports-new-hit-overrides:${HIT_OVERRIDE_STORAGE_VERSION}:${importId}`,
      );
      if (!raw) {
        setHitOverrides({});
        return;
      }
      const parsedRaw = JSON.parse(raw) as Record<string, string>;
      setHitOverrides(parsedRaw);
    } catch {
      setHitOverrides({});
    }
  }, [importId]);

  useEffect(() => {
    if (!importId) {
      return;
    }
    window.localStorage.setItem(
      `imports-new-hit-overrides:${HIT_OVERRIDE_STORAGE_VERSION}:${importId}`,
      JSON.stringify(hitOverrides),
    );
  }, [hitOverrides, importId]);

  const selectedPage =
    record?.pages.find((page) => page.page_number === activePageNumber) ?? null;
  const previewUrl =
    record && selectedPage
      ? isStaffSource
        ? `${API_BASE_URL}/api/staff/records/${record.id}/preview`
        : `${API_BASE_URL}/api/imports/${record.id}/pages/${selectedPage.page_number}/cleaned`
      : '';

  const parsed = useMemo(() => parseImportByKeyword(record, KEYWORD), [record]);
  const activeHit =
    parsed?.keywordHits.find((hit) => hit.id === activeHitId) ?? null;

  useEffect(() => {
    const hits = parsed?.keywordHits ?? [];
    if (hits.length === 0) {
      if (activeHitId !== null) {
        setActiveHitId(null);
      }
      return;
    }
    if (!activeHitId) {
      return;
    }
    const exists = hits.some((hit) => hit.id === activeHitId);
    if (!exists) {
      setActiveHitId(null);
    }
  }, [activeHitId, parsed]);

  useEffect(() => {
    if (!activeFieldKey && !activeHitId) {
      setActiveHighlight(null);
    }
  }, [activeFieldKey, activeHitId]);

  useEffect(() => {
    if (!activeHit) {
      return;
    }

    setActivePageNumber(activeHit.pageNumber);
    if (activeHit.bbox) {
      setActiveHighlight({
        pageNumber: activeHit.pageNumber,
        bbox: activeHit.bbox,
      });
    }
  }, [activeHit]);

  useEffect(() => {
    setLoadedPreviewUrl('');
  }, [previewUrl]);

  useEffect(() => {
    if (!record) {
      setDetectedHeaderAnchors({});
      return;
    }

    const firstPage = record.pages.find((page) => page.page_number === 1);
    if (!firstPage) {
      setDetectedHeaderAnchors({});
      return;
    }

    let cancelled = false;
    const firstPagePreviewUrl = isStaffSource
      ? `${API_BASE_URL}/api/staff/records/${record.id}/preview`
      : `${API_BASE_URL}/api/imports/${record.id}/pages/${firstPage.page_number}/cleaned`;
    setDetectedHeaderAnchors({});

    detectFirstPageHeaderAnchors(firstPagePreviewUrl, firstPage.page_number)
      .then((anchors) => {
        if (!cancelled) {
          setDetectedHeaderAnchors(anchors);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDetectedHeaderAnchors({});
        }
      });

    return () => {
      cancelled = true;
    };
  }, [isStaffSource, record]);

  useEffect(() => {
    if (!activeHighlight || activeHighlight.pageNumber !== activePageNumber) {
      return;
    }
    if (loadedPreviewUrl !== previewUrl) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      scrollPreviewToHighlight('smooth');
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeHighlight, activePageNumber, loadedPreviewUrl, previewUrl]);

  async function loadImport(currentImportId: string) {
    setIsLoading(true);
    setErrorMessage(null);
    try {
      const response = await fetch(
        isStaffSource
          ? `${API_BASE_URL}/api/staff/records/${currentImportId}/import`
          : `${API_BASE_URL}/api/imports/${currentImportId}`,
        {
          cache: 'no-store',
          headers: isStaffSource ? getAuthHeaders() : undefined,
        },
      );
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(
          payload?.detail || 'Unable to load this review record.',
        );
      }

      const data = (await response.json()) as ImportRecord;
      setRecord(data);
      setActivePageNumber(data.pages[0]?.page_number ?? 1);
      setActiveFieldKey(null);
      setActiveHitId(null);
      setActiveHighlight(null);
      setEditingFieldKey(null);
      setEditingFieldValue('');
      setEditingHitId(null);
      setEditingHitValue('');
      setStaffActionMessage(null);
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Unable to load this review record.',
      );
    } finally {
      setIsLoading(false);
    }
  }

  function getHitText(hit: KeywordHit): string {
    return hitOverrides[hit.id] ?? hit.displayText ?? hit.text;
  }

  function getFieldValue(key: ParsedFieldKey): string | null {
    if (!parsed) {
      return null;
    }
    const overridden = fieldOverrides[key];
    if (typeof overridden === 'string') {
      return overridden;
    }
    return parsed[key];
  }

  function scrollResultNodeIntoView(
    node: HTMLElement | null,
    behavior: ScrollBehavior = 'smooth',
  ) {
    const container = resultPanelRef.current;
    if (!container || !node) {
      return;
    }

    const containerRect = container.getBoundingClientRect();
    const nodeRect = node.getBoundingClientRect();
    const targetTop =
      container.scrollTop +
      (nodeRect.top - containerRect.top) -
      container.clientHeight / 2 +
      nodeRect.height / 2;

    container.scrollTo({
      top: Math.max(0, targetTop),
      behavior,
    });
  }

  function scrollToFieldCard(
    key: ParsedFieldKey,
    behavior: ScrollBehavior = 'smooth',
  ) {
    scrollResultNodeIntoView(fieldCardRefs.current[key] ?? null, behavior);
  }

  function scrollToHitCard(hitId: string, behavior: ScrollBehavior = 'smooth') {
    scrollResultNodeIntoView(hitCardRefs.current[hitId] ?? null, behavior);
  }

  function getFirstPageFallbackAnchor(
    key: ParsedFieldKey,
  ): HighlightTarget | null {
    const bbox = FIRST_PAGE_FALLBACK_ANCHORS[key];
    const firstPage = record?.pages.find((page) => page.page_number === 1);
    if (!bbox || !firstPage) {
      return null;
    }

    return {
      pageNumber: firstPage.page_number,
      bbox,
    };
  }

  function isReasonableHeaderAnchor(
    key: ParsedFieldKey,
    anchor: HighlightTarget,
  ): boolean {
    if (anchor.pageNumber !== 1) {
      return true;
    }

    const [, top, , bottom] = anchor.bbox;
    if (key === 'caseBlackNo') {
      return top <= 0.18 && bottom <= 0.26;
    }
    if (key === 'caseRedNo') {
      return top <= 0.26 && bottom <= 0.34;
    }
    if (key === 'courtName') {
      return top >= 0.14 && top <= 0.46 && bottom <= 0.54;
    }
    return true;
  }

  async function completeStaffRecord() {
    if (!record || !isStaffSource) {
      return;
    }

    setIsCompletingStaffRecord(true);
    setErrorMessage(null);
    setStaffActionMessage(null);
    try {
      const fields = Object.fromEntries(
        RESULT_FIELDS.map((field) => [field.key, getFieldValue(field.key)]),
      );
      const response = await fetch(
        `${API_BASE_URL}/api/staff/records/${record.id}/complete`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...getAuthHeaders(),
          },
          body: JSON.stringify({
            corrected_result: {
              fields,
              keyword_hits:
                parsed?.keywordHits.map((hit) => ({
                  id: hit.id,
                  pageNumber: hit.pageNumber,
                  text: getHitText(hit),
                  bbox: hit.bbox,
                })) ?? [],
            },
          }),
        },
      );
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(payload?.detail || 'Unable to complete this record.');
      }
      setStaffActionMessage('ตรวจเช็คเรียบร้อยแล้ว');
      await loadImport(record.id);
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Unable to complete this record.',
      );
    } finally {
      setIsCompletingStaffRecord(false);
    }
  }

  function getDerivedHeaderAnchor(key: ParsedFieldKey): HighlightTarget | null {
    const firstPage =
      record?.pages.find((page) => page.page_number === 1) ?? null;
    if (!firstPage) {
      return null;
    }

    const fieldValue = getFieldValue(key);
    const exactAnchor = findHeaderAnchorByFieldValue(
      firstPage,
      key,
      fieldValue,
    );
    if (exactAnchor) {
      return exactAnchor;
    }

    if (key === 'caseBlackNo' || key === 'caseRedNo') {
      const caseInfo = findCaseHeaderInfo(firstPage);
      return key === 'caseBlackNo' ? caseInfo.blackAnchor : caseInfo.redAnchor;
    }

    if (key === 'courtName') {
      return findCourtInfo(firstPage)?.anchor ?? null;
    }

    return null;
  }

  function resolveFieldAnchor(key: ParsedFieldKey): HighlightTarget | null {
    if (HEADER_NAVIGATION_FIELDS.has(key)) {
      const directAnchor = parsed?.fieldAnchors[key];
      if (directAnchor && isReasonableHeaderAnchor(key, directAnchor)) {
        return directAnchor;
      }

      const detectedAnchor = detectedHeaderAnchors[key];
      if (detectedAnchor && isReasonableHeaderAnchor(key, detectedAnchor)) {
        return detectedAnchor;
      }
    }

    const derivedHeaderAnchor = getDerivedHeaderAnchor(key);
    if (
      derivedHeaderAnchor &&
      isReasonableHeaderAnchor(key, derivedHeaderAnchor)
    ) {
      return derivedHeaderAnchor;
    }

    const directAnchor = parsed?.fieldAnchors[key];
    if (directAnchor && isReasonableHeaderAnchor(key, directAnchor)) {
      return directAnchor;
    }

    if (JUDGMENT_SOURCE_FIELDS.has(key)) {
      const primaryHit =
        parsed?.keywordHits.find((hit) => Boolean(hit.bbox)) ?? null;
      if (primaryHit?.bbox) {
        return {
          pageNumber: primaryHit.pageNumber,
          bbox: primaryHit.bbox,
        };
      }
    }

    return getFirstPageFallbackAnchor(key);
  }

  function getFieldSourceHit(key: ParsedFieldKey): KeywordHit | null {
    if (!JUDGMENT_SOURCE_FIELDS.has(key)) {
      return null;
    }

    return parsed?.keywordHits.find((hit) => Boolean(hit.bbox)) ?? null;
  }

  function activateField(key: ParsedFieldKey) {
    setActiveFieldKey(key);
    setActiveHitId(null);
    setEditingHitId(null);
    setEditingHitValue('');
    const anchor = resolveFieldAnchor(key);
    if (anchor) {
      setActiveHighlight(anchor);
      setActivePageNumber(anchor.pageNumber);
      return;
    }

    if (key === 'caseRedNo') {
      const firstPage = record?.pages.find((page) => page.page_number === 1);
      if (firstPage) {
        setActivePageNumber(firstPage.page_number);
      }
    }
    setActiveHighlight(null);
  }

  function startEditField(key: ParsedFieldKey) {
    if (HEADER_NAVIGATION_FIELDS.has(key)) {
      activateField(key);
    } else {
      setActiveFieldKey(null);
      setActiveHitId(null);
      setActiveHighlight(null);
    }
    setEditingFieldKey(key);
    setEditingFieldValue(getFieldValue(key) || '');
  }

  function cancelEditField() {
    setEditingFieldKey(null);
    setEditingFieldValue('');
  }

  function saveEditField() {
    if (!editingFieldKey) {
      return;
    }

    const normalized = editingFieldValue.trim();
    setFieldOverrides((prev) => {
      const next = { ...prev };
      if (!normalized) {
        delete next[editingFieldKey];
      } else {
        next[editingFieldKey] = normalized;
      }
      return next;
    });
    setEditingFieldKey(null);
    setEditingFieldValue('');
  }

  function activateHit(hit: KeywordHit) {
    setActiveFieldKey(null);
    setActiveHitId(hit.id);
    setActivePageNumber(hit.pageNumber);
    if (hit.bbox) {
      setActiveHighlight({
        pageNumber: hit.pageNumber,
        bbox: hit.bbox,
      });
      return;
    }
    setActiveHighlight(null);
  }

  function startEditHit(hit: KeywordHit) {
    activateHit(hit);
    setEditingHitId(hit.id);
    setEditingHitValue(getHitText(hit));
  }

  function cancelEditHit() {
    setEditingHitId(null);
    setEditingHitValue('');
  }

  function saveEditHit() {
    if (!editingHitId) {
      return;
    }

    const normalized = editingHitValue.trim();
    setHitOverrides((prev) => {
      const next = { ...prev };
      if (!normalized) {
        delete next[editingHitId];
      } else {
        next[editingHitId] = normalized;
      }
      return next;
    });

    setEditingHitId(null);
    setEditingHitValue('');
  }

  function handlePreviewHighlightClick() {
    if (activeHitId) {
      scrollToHitCard(activeHitId);
      return;
    }

    if (!activeFieldKey) {
      return;
    }

    const sourceHit = getFieldSourceHit(activeFieldKey);
    if (sourceHit) {
      scrollToHitCard(sourceHit.id);
      return;
    }

    scrollToFieldCard(activeFieldKey);
  }

  function scrollPreviewToHighlight(behavior: ScrollBehavior) {
    if (!activeHighlight || activeHighlight.pageNumber !== activePageNumber) {
      return;
    }
    const scrollContainer = previewScrollRef.current;
    const content = previewContentRef.current;
    if (!scrollContainer || !content) {
      return;
    }

    const contentHeight = content.getBoundingClientRect().height;
    if (!contentHeight) {
      return;
    }

    const highlightMidpoint =
      ((activeHighlight.bbox[1] + activeHighlight.bbox[3]) / 2) * contentHeight;
    const targetTop = Math.max(
      0,
      Math.min(
        contentHeight - scrollContainer.clientHeight,
        highlightMidpoint - scrollContainer.clientHeight / 2,
      ),
    );

    scrollContainer.scrollTo({
      top: targetTop,
      behavior,
    });
  }

  return (
    <Box
      as="main"
      maxW="1880px"
      mx="auto"
      px={{ base: 2, md: 3, xl: 4 }}
      py={{ base: 3, md: 4 }}
    >
      <Grid
        gap={{ base: 3, xl: 4 }}
        templateColumns={{ base: '1fr', xl: 'repeat(2, minmax(0, 1fr))' }}
      >
        <Box
          {...panelStyles}
          p={{ base: 3, md: 4 }}
          display="flex"
          flexDirection="column"
          gap={4}
          h={{ xl: 'calc(100vh - 24px)' }}
          overflow={{ xl: 'auto' }}
          position={{ xl: 'sticky' }}
          top={{ xl: '12px' }}
          ref={resultPanelRef}
        >
          <Flex align="start" justify="space-between">
            <Link
              href={isStaffSource ? '/staff' : '/'}
              style={{ textDecoration: 'none' }}
            >
              <Box
                alignItems="center"
                bg="rgba(15, 118, 110, 0.06)"
                borderWidth="1px"
                borderColor="rgba(15, 118, 110, 0.18)"
                borderRadius="full"
                color="#115e59"
                display="inline-flex"
                fontWeight="700"
                justifyContent="center"
                minH="48px"
                px={6}
                _hover={{ bg: 'rgba(15, 118, 110, 0.12)' }}
              >
                กลับหน้าแรก
              </Box>
            </Link>
          </Flex>

          {record ? (
            <Flex gap={3} wrap="wrap">
              {record.pages.map((page) => (
                <PillButton
                  key={`${record.id}-page-${page.page_number}`}
                  active={activePageNumber === page.page_number}
                  onClick={() => setActivePageNumber(page.page_number)}
                >
                  Page {page.page_number}
                </PillButton>
              ))}
            </Flex>
          ) : null}

          <Box
            borderWidth="1px"
            borderColor="rgba(73, 59, 36, 0.08)"
            borderRadius="24px"
            bg="white"
            p={3}
            minH="72vh"
            maxH="72vh"
            overflow="auto"
            ref={previewScrollRef}
          >
            {previewUrl ? (
              <Box position="relative" ref={previewContentRef}>
                <Image
                  alt={`Preview page ${activePageNumber}`}
                  src={previewUrl}
                  w="100%"
                  display="block"
                  onLoad={() => {
                    setLoadedPreviewUrl(previewUrl);
                  }}
                />
                {activeHighlight &&
                activeHighlight.pageNumber === activePageNumber ? (
                  <Box
                    as="button"
                    onClick={handlePreviewHighlightClick}
                    position="absolute"
                    zIndex={2}
                    border="0"
                    outline="3px solid rgba(231, 111, 45, 0.95)"
                    outlineOffset="0"
                    borderRadius="16px"
                    boxShadow="none"
                    cursor="pointer"
                    aria-label="Scroll to the matching result content"
                    bg="transparent"
                    _hover={{
                      borderColor: 'rgba(194, 65, 12, 0.98)',
                      outlineColor: 'rgba(194, 65, 12, 0.78)',
                    }}
                    style={getHighlightStyle(activeHighlight.bbox)}
                  />
                ) : null}
              </Box>
            ) : (
              <Text color="#6a5a45">ยังไม่มี preview ให้แสดง</Text>
            )}
          </Box>
        </Box>

        <Box
          {...panelStyles}
          p={{ base: 3, md: 4 }}
          display="flex"
          flexDirection="column"
          gap={4}
          h={{ xl: 'calc(100vh - 24px)' }}
          overflow={{ xl: 'auto' }}
          position={{ xl: 'sticky' }}
          top={{ xl: '12px' }}
        >
          <Text
            alignSelf="start"
            bg="rgba(15, 118, 110, 0.08)"
            color="#115e59"
            borderRadius="full"
            px={3}
            py={1}
            fontSize="0.84rem"
            fontWeight="700"
          >
            ผลลัพธ์ใหม่
          </Text>

          {isLoading ? (
            <Flex align="center" gap={3}>
              <Spinner color="#115e59" size="sm" />
              <Text color="#6a5a45">กำลังโหลดข้อมูล...</Text>
            </Flex>
          ) : null}

          {errorMessage ? (
            <Box
              borderWidth="1px"
              borderColor="rgba(185, 28, 28, 0.24)"
              borderRadius="18px"
              bg="rgba(254, 242, 242, 0.95)"
              p={4}
            >
              <Text color="#991b1b" fontWeight="700">
                {errorMessage}
              </Text>
            </Box>
          ) : null}

          {staffActionMessage ? (
            <Box
              borderWidth="1px"
              borderColor="rgba(22, 101, 52, 0.22)"
              borderRadius="18px"
              bg="rgba(240, 253, 244, 0.95)"
              p={4}
            >
              <Text color="#166534" fontWeight="700">
                {staffActionMessage}
              </Text>
            </Box>
          ) : null}

          {record && parsed ? (
            <>
              {isStaffSource ? (
                <Flex justify="flex-end">
                  <Box
                    as="button"
                    aria-disabled={
                      isCompletingStaffRecord || record.status === 'checked'
                    }
                    onClick={() => {
                      if (
                        isCompletingStaffRecord ||
                        record.status === 'checked'
                      ) {
                        return;
                      }
                      void completeStaffRecord();
                    }}
                    borderRadius="14px"
                    borderWidth="1px"
                    borderColor="rgba(15, 118, 110, 0.22)"
                    bg={
                      record.status === 'checked'
                        ? 'rgba(229, 231, 235, 0.9)'
                        : 'linear-gradient(135deg, #0f766e, #115e59)'
                    }
                    color={record.status === 'checked' ? '#64748b' : 'white'}
                    cursor={
                      isCompletingStaffRecord || record.status === 'checked'
                        ? 'not-allowed'
                        : 'pointer'
                    }
                    fontWeight="800"
                    minH="44px"
                    px={5}
                    opacity={isCompletingStaffRecord ? 0.7 : 1}
                  >
                    {record.status === 'checked'
                      ? 'ตรวจเช็คแล้ว'
                      : isCompletingStaffRecord
                        ? 'กำลังบันทึก...'
                        : 'ตรวจเช็ค'}
                  </Box>
                </Flex>
              ) : null}

              <Grid
                gap={3}
                templateColumns={{
                  base: '1fr',
                  md: 'repeat(2, minmax(0, 1fr))',
                }}
              >
                {RESULT_FIELDS.map((field) => {
                  const isHeaderNavigationField = HEADER_NAVIGATION_FIELDS.has(
                    field.key,
                  );
                  return (
                    <EditableResultCard
                      key={field.key}
                      active={
                        isHeaderNavigationField && activeFieldKey === field.key
                      }
                      containerRef={(node: HTMLDivElement | null) => {
                        fieldCardRefs.current[field.key] = node;
                      }}
                      editing={editingFieldKey === field.key}
                      editingValue={editingFieldValue}
                      interactive={isHeaderNavigationField}
                      label={field.label}
                      onActivate={
                        isHeaderNavigationField
                          ? () => activateField(field.key)
                          : undefined
                      }
                      onCancelEdit={cancelEditField}
                      onChangeEditingValue={setEditingFieldValue}
                      onEdit={() => startEditField(field.key)}
                      onSaveEdit={saveEditField}
                      value={getFieldValue(field.key)}
                    />
                  );
                })}
              </Grid>

              <Box
                borderWidth="1px"
                borderColor="rgba(73, 59, 36, 0.08)"
                borderRadius="20px"
                bg="rgba(251, 248, 242, 0.92)"
                p={4}
              >
                <Text
                  color="#6a5a45"
                  fontSize="0.85rem"
                  fontWeight="700"
                  mb={2}
                >
                  ข้อความ {KEYWORD}
                </Text>
                {parsed.keywordHits.length === 0 ? (
                  <Text color="#6a5a45">ยังไม่พบช่วงคำพิพากษาใน OCR text</Text>
                ) : (
                  <Stack gap={3}>
                    {parsed.keywordHits.map((hit) => (
                      <Box
                        key={hit.id}
                        ref={(node: HTMLDivElement | null) => {
                          hitCardRefs.current[hit.id] = node;
                        }}
                        borderWidth="1px"
                        borderColor={
                          activeHitId === hit.id
                            ? 'rgba(231, 111, 45, 0.85)'
                            : 'rgba(15, 118, 110, 0.16)'
                        }
                        borderRadius="16px"
                        bg={
                          activeHitId === hit.id
                            ? 'rgba(255, 247, 237, 0.9)'
                            : 'rgba(236, 253, 245, 0.65)'
                        }
                        p={3}
                        cursor="pointer"
                        onClick={() => activateHit(hit)}
                      >
                        <Flex justify="space-between" align="start" gap={3}>
                          <Text
                            display="inline-flex"
                            borderRadius="999px"
                            bg={
                              activeHitId === hit.id
                                ? 'rgba(231, 111, 45, 0.14)'
                                : 'rgba(15, 118, 110, 0.12)'
                            }
                            color={
                              activeHitId === hit.id ? '#b54708' : '#115e59'
                            }
                            px={2.5}
                            py={1}
                            fontSize="0.78rem"
                            fontWeight="700"
                          >
                            Page {hit.pageNumber}
                          </Text>

                          {editingHitId !== hit.id ? (
                            <Box
                              as="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                startEditHit(hit);
                              }}
                              color="#115e59"
                              borderWidth="1px"
                              borderColor="rgba(15, 118, 110, 0.18)"
                              borderRadius="12px"
                              bg="rgba(15, 118, 110, 0.06)"
                              minW="42px"
                              h="34px"
                              display="inline-flex"
                              alignItems="center"
                              justifyContent="center"
                              fontWeight="700"
                              fontSize="0.76rem"
                            >
                              EDIT
                            </Box>
                          ) : null}
                        </Flex>

                        {editingHitId === hit.id ? (
                          <Stack mt={2} gap={2}>
                            <textarea
                              value={editingHitValue}
                              onChange={(event) =>
                                setEditingHitValue(event.target.value)
                              }
                              onClick={(event) => event.stopPropagation()}
                              style={{
                                width: '100%',
                                minHeight: '110px',
                                borderRadius: '12px',
                                border: '1px solid rgba(73, 59, 36, 0.18)',
                                padding: '10px 12px',
                                fontSize: '1rem',
                                fontFamily: 'inherit',
                                lineHeight: '1.6',
                                resize: 'vertical',
                                outline: 'none',
                                background: 'white',
                              }}
                            />
                            <Flex gap={2}>
                              <Box
                                as="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  saveEditHit();
                                }}
                                borderRadius="10px"
                                borderWidth="1px"
                                borderColor="rgba(194, 65, 12, 0.28)"
                                bg="rgba(255, 237, 213, 0.92)"
                                color="#9a3412"
                                fontWeight="700"
                                px={3}
                                py={1.5}
                              >
                                Update
                              </Box>
                              <Box
                                as="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  cancelEditHit();
                                }}
                                borderRadius="10px"
                                borderWidth="1px"
                                borderColor="rgba(73, 59, 36, 0.18)"
                                bg="white"
                                color="#6a5a45"
                                fontWeight="700"
                                px={3}
                                py={1.5}
                              >
                                Cancel
                              </Box>
                            </Flex>
                          </Stack>
                        ) : (
                          <Text mt={2} lineHeight="1.7" whiteSpace="pre-wrap">
                            {getHitText(hit)}
                          </Text>
                        )}
                      </Box>
                    ))}
                  </Stack>
                )}
              </Box>
            </>
          ) : null}
        </Box>
      </Grid>
    </Box>
  );
}

function PillButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <Box
      as="button"
      onClick={onClick}
      borderWidth="1px"
      borderColor={active ? 'transparent' : 'rgba(73, 59, 36, 0.12)'}
      borderRadius="full"
      bg={
        active
          ? 'linear-gradient(135deg, #0f766e, #115e59)'
          : 'rgba(255, 255, 255, 0.96)'
      }
      color={active ? 'white' : '#1f1a14'}
      fontWeight="700"
      minH="50px"
      px={7}
      _hover={{
        borderColor: active ? 'transparent' : 'rgba(15, 118, 110, 0.26)',
      }}
    >
      {children}
    </Box>
  );
}

function EditableResultCard({
  active,
  containerRef,
  editing,
  editingValue,
  interactive,
  label,
  onActivate,
  onCancelEdit,
  onChangeEditingValue,
  onEdit,
  onSaveEdit,
  value,
}: {
  active: boolean;
  containerRef?: (node: HTMLDivElement | null) => void;
  editing: boolean;
  editingValue: string;
  interactive: boolean;
  label: string;
  onActivate?: () => void;
  onCancelEdit: () => void;
  onChangeEditingValue: (value: string) => void;
  onEdit?: () => void;
  onSaveEdit: () => void;
  value: string | null;
}) {
  return (
    <Box
      ref={containerRef}
      borderWidth="1px"
      borderColor={
        active ? 'rgba(231, 111, 45, 0.85)' : 'rgba(73, 59, 36, 0.08)'
      }
      borderRadius="18px"
      bg={active ? 'rgba(255, 247, 237, 0.94)' : 'rgba(251, 248, 242, 0.92)'}
      p={4}
      cursor={interactive ? 'pointer' : 'default'}
      onClick={interactive ? onActivate : undefined}
    >
      <Flex justify="space-between" align="start" gap={3}>
        <Text color="#6a5a45" fontSize="0.84rem" fontWeight="700" mb={1}>
          {label}
        </Text>
        {onEdit && !editing ? (
          <Box
            as="button"
            onClick={(event) => {
              event.stopPropagation();
              onEdit();
            }}
            color="#115e59"
            borderWidth="1px"
            borderColor="rgba(15, 118, 110, 0.18)"
            borderRadius="12px"
            bg="rgba(15, 118, 110, 0.06)"
            w="38px"
            h="38px"
            display="inline-flex"
            alignItems="center"
            justifyContent="center"
            fontWeight="700"
            fontSize="0.76rem"
          >
            EDIT
          </Box>
        ) : null}
      </Flex>

      {editing ? (
        <Stack gap={2}>
          <input
            type="text"
            value={editingValue}
            onChange={(event: React.ChangeEvent<HTMLInputElement>) =>
              onChangeEditingValue(event.target.value)
            }
            onClick={(event) => event.stopPropagation()}
            style={{
              background: 'white',
              borderColor: 'rgba(73, 59, 36, 0.18)',
              borderWidth: '1px',
              borderStyle: 'solid',
              borderRadius: '12px',
              height: '40px',
              padding: '0 12px',
              width: '100%',
              outline: 'none',
            }}
          />
          <Flex gap={2}>
            <Box
              as="button"
              onClick={(event) => {
                event.stopPropagation();
                onSaveEdit();
              }}
              borderRadius="10px"
              borderWidth="1px"
              borderColor="rgba(194, 65, 12, 0.28)"
              bg="rgba(255, 237, 213, 0.92)"
              color="#9a3412"
              fontWeight="700"
              px={3}
              py={1.5}
            >
              Update
            </Box>
            <Box
              as="button"
              onClick={(event) => {
                event.stopPropagation();
                onCancelEdit();
              }}
              borderRadius="10px"
              borderWidth="1px"
              borderColor="rgba(73, 59, 36, 0.18)"
              bg="white"
              color="#6a5a45"
              fontWeight="700"
              px={3}
              py={1.5}
            >
              Cancel
            </Box>
          </Flex>
        </Stack>
      ) : (
        <Text fontSize="1.06rem" fontWeight="700" wordBreak="break-word">
          {value || '-'}
        </Text>
      )}
    </Box>
  );
}

function parseImportByKeyword(
  record: ImportRecord | null,
  keyword: string,
): ParsedClause | null {
  const fallback = parseImportByKeywordHeuristic(record, keyword);
  const reviewDataParsed = parseImportReviewData(record);
  if (!reviewDataParsed) {
    return finalizeParsedClause(record, fallback, keyword);
  }
  if (!fallback) {
    return finalizeParsedClause(record, reviewDataParsed, keyword);
  }

  const merged: ParsedClause = {
    ...fallback,
    ...reviewDataParsed,
    fieldAnchors: {
      ...fallback.fieldAnchors,
      ...reviewDataParsed.fieldAnchors,
    },
    keywordHits:
      reviewDataParsed.keywordHits.length > 0
        ? reviewDataParsed.keywordHits
        : fallback.keywordHits,
  };

  for (const key of PARSED_FIELD_KEYS) {
    if (!reviewDataParsed[key] && fallback[key]) {
      merged[key] = fallback[key];
    }
  }

  return finalizeParsedClause(record, merged, keyword);
}

function finalizeParsedClause(
  record: ImportRecord | null,
  parsed: ParsedClause | null,
  keyword: string,
): ParsedClause | null {
  if (!parsed) {
    return null;
  }

  return {
    ...parsed,
    keywordHits: enrichKeywordHits(record, parsed.keywordHits, keyword),
  };
}

function parseImportByKeywordHeuristic(
  record: ImportRecord | null,
  keyword: string,
): ParsedClause | null {
  if (!record) {
    return null;
  }

  const firstPage = record.pages.find((page) => page.page_number === 1) || null;
  const compactPageOneText = normalizeText(
    firstPage ? getPageText(firstPage) : '',
  );
  const normalizedKeyword = normalizeText(keyword);
  const normalizedCategory = (record.document_category || '')
    .trim()
    .toLowerCase();
  const shouldCaptureJudgmentClause =
    normalizedCategory === 'judgment' ||
    normalizedKeyword === normalizeText(KEYWORD);
  const keywordOptions: KeywordWindowOptions = shouldCaptureJudgmentClause
    ? {
        maxChars: 4000,
        maxBlocks: 24,
        minHighlightRows: 3,
        captureToPageEnd: true,
      }
    : {
        maxChars: 420,
        maxBlocks: 2,
        minHighlightRows: 1,
        captureToPageEnd: false,
      };
  const keywordHits: KeywordHit[] = [];

  for (const page of record.pages) {
    const pageText = getPageText(page);
    if (!pageText.trim()) {
      continue;
    }

    const windows = extractKeywordWindows(pageText, keyword, keywordOptions);
    const keywordSegments = page.segments.filter((segment) =>
      normalizeText(getSegmentSearchText(segment)).includes(normalizedKeyword),
    );

    windows.forEach((windowText, index) => {
      const hitSegment = keywordSegments[index] || keywordSegments[0] || null;
      const mergedBbox = buildKeywordWindowBbox(
        page,
        windowText,
        hitSegment,
        keywordOptions,
      );
      const hitBbox = mergedBbox || hitSegment?.bbox || null;
      const alignedDisplayText = hitBbox
        ? extractDisplayTextFromBbox(page, hitBbox)
        : '';
      const displayText = trimTextBeforeKeyword(
        alignedDisplayText || windowText,
        keyword,
      );
      keywordHits.push({
        id: `page-${page.page_number}-hit-${index + 1}`,
        pageNumber: page.page_number,
        text: windowText,
        displayText,
        bbox: hitBbox,
      });
    });
  }

  const compactScope = normalizeText(
    keywordHits.map((hit) => hit.text).join(' '),
  );

  const caseHeader = firstPage ? findCaseHeaderInfo(firstPage) : null;
  const courtInfo = firstPage ? findCourtInfo(firstPage) : null;

  let primaryJudgmentAnchor: HighlightTarget | null = null;
  for (const hit of keywordHits) {
    if (!hit.bbox) {
      continue;
    }
    primaryJudgmentAnchor = {
      pageNumber: hit.pageNumber,
      bbox: hit.bbox,
    };
    break;
  }

  return {
    caseRedNo:
      caseHeader?.redNo || pickFirst(compactPageOneText, CASE_RED_PATTERNS),
    caseBlackNo:
      caseHeader?.blackNo || pickFirst(compactPageOneText, CASE_BLACK_PATTERNS),
    courtName: courtInfo?.name || null,
    payAmount: pickFirst(compactScope, [
      /ให้จำเลย(?:ที่\s*[0-9๐-๙]+)?\s*ชำระเงิน(?:เป็น)?\s*จำนวน\s*([0-9๐-๙,\.]+)/,
      /ให้จำเลย(?:ที่\s*[0-9๐-๙]+)?\s*ชำระเงิน\s*([0-9๐-๙,\.]+)/,
      /ชำระเงิน(?:เป็น)?\s*จำนวน\s*([0-9๐-๙,\.]+)/,
      /ชำระเงิน\s*([0-9๐-๙,\.]+)/,
    ]),
    interestRate: pickFirst(compactScope, [
      /อัตราร้อยละ\s*([0-9๐-๙,\.]+)/,
      /(?:รายละเอียด|รายละเอี[ยี]ด)\s*([0-9๐-๙,\.]+)\s*ต่อปี/,
      /([0-9๐-๙,\.]+)\s*ต่อปี/,
    ]),
    principalAmount: pickFirst(compactScope, [
      /(?:ของต้นเงิน|ของเงินต้น)\s*([0-9๐-๙,\.]+)/,
    ]),
    filingDate: resolveFilingStartDate(compactScope),
    attorneyFee: pickFirst(compactScope, [
      /ค่าทนายความ\s*[\/\\|]?\s*([0-9๐-๙,\.]+)/,
    ]),
    fieldAnchors: {
      caseRedNo: caseHeader?.redAnchor || undefined,
      caseBlackNo: caseHeader?.blackAnchor || undefined,
      courtName: courtInfo?.anchor || undefined,
      payAmount: primaryJudgmentAnchor || undefined,
      interestRate: primaryJudgmentAnchor || undefined,
      principalAmount: primaryJudgmentAnchor || undefined,
      filingDate: primaryJudgmentAnchor || undefined,
      attorneyFee: primaryJudgmentAnchor || undefined,
    },
    keywordHits,
  };
}

function parseImportReviewData(
  record: ImportRecord | null,
): ParsedClause | null {
  const reviewData = record?.review_data;
  if (!reviewData) {
    return null;
  }

  const normalizedKeywordHits = normalizeReviewKeywordHits(reviewData);
  const parsed: ParsedClause = {
    caseRedNo: null,
    caseBlackNo: null,
    courtName: null,
    payAmount: null,
    interestRate: null,
    principalAmount: null,
    filingDate: null,
    attorneyFee: null,
    fieldAnchors: {},
    keywordHits: normalizedKeywordHits,
  };

  let hasContent = normalizedKeywordHits.length > 0;
  for (const key of PARSED_FIELD_KEYS) {
    const field = reviewData.fields?.[key];
    if (!field) {
      continue;
    }

    const normalizedValue =
      typeof field.value === 'string' && field.value.trim()
        ? field.value.trim()
        : null;
    if (normalizedValue) {
      parsed[key] = normalizedValue;
      hasContent = true;
    }

    const anchor = toHighlightTarget(field);
    if (anchor) {
      parsed.fieldAnchors[key] = anchor;
      hasContent = true;
    }
  }

  return hasContent ? parsed : null;
}

function normalizeReviewKeywordHits(reviewData: ReviewData): KeywordHit[] {
  if (!Array.isArray(reviewData.keywordHits)) {
    return [];
  }

  return reviewData.keywordHits
    .map((hit, index) => normalizeReviewKeywordHit(hit, index))
    .filter((hit): hit is KeywordHit => hit !== null);
}

function normalizeReviewKeywordHit(
  hit: ReviewKeywordHit,
  index: number,
): KeywordHit | null {
  if (!hit || typeof hit !== 'object') {
    return null;
  }

  const pageNumber =
    typeof hit.pageNumber === 'number' && Number.isFinite(hit.pageNumber)
      ? hit.pageNumber
      : null;
  const text = typeof hit.text === 'string' ? hit.text.trim() : '';
  if (!pageNumber || !text) {
    return null;
  }

  const bbox = normalizeBbox(hit.bbox);
  const displayText =
    typeof hit.displayText === 'string' && hit.displayText.trim()
      ? hit.displayText.trim()
      : text;

  return {
    id:
      typeof hit.id === 'string' && hit.id.trim()
        ? hit.id
        : `review-hit-${pageNumber}-${index + 1}`,
    pageNumber,
    text,
    displayText,
    bbox,
  };
}

function enrichKeywordHits(
  record: ImportRecord | null,
  hits: KeywordHit[],
  keyword: string,
): KeywordHit[] {
  if (!record || hits.length === 0) {
    return hits;
  }

  return hits.map((hit) => enrichKeywordHit(record, hit, keyword));
}

function enrichKeywordHit(
  record: ImportRecord,
  hit: KeywordHit,
  keyword: string,
): KeywordHit {
  const page =
    record.pages.find((item) => item.page_number === hit.pageNumber) ?? null;
  if (!page) {
    return hit;
  }

  const shouldCaptureJudgmentClause =
    (record.document_category || '').trim().toLowerCase() === 'judgment' ||
    normalizeText(keyword) === normalizeText(KEYWORD);
  const keywordOptions: KeywordWindowOptions = shouldCaptureJudgmentClause
    ? {
        maxChars: 4000,
        maxBlocks: 24,
        minHighlightRows: 3,
        captureToPageEnd: true,
      }
    : {
        maxChars: 420,
        maxBlocks: 2,
        minHighlightRows: 1,
        captureToPageEnd: false,
      };

  const bbox =
    hit.bbox ||
    buildKeywordWindowBbox(
      page,
      hit.text,
      findKeywordSeedSegment(page, keyword, hit.text),
      keywordOptions,
    );
  if (!bbox) {
    return hit;
  }

  const displayText =
    hit.displayText?.trim() ||
    extractDisplayTextFromBbox(page, bbox) ||
    hit.text;

  return {
    ...hit,
    bbox,
    displayText,
  };
}

function findKeywordSeedSegment(
  page: ImportPageAsset,
  keyword: string,
  windowText: string,
): TextSegment | null {
  const compactKeyword = compactForCompare(normalizeText(keyword));
  const compactWindow = compactForCompare(normalizeText(windowText));

  return (
    page.segments.find((segment) => {
      const text = compactForCompare(
        normalizeText(getSegmentSearchText(segment)),
      );
      return Boolean(text && compactKeyword && text.includes(compactKeyword));
    }) ||
    page.segments.find((segment) => {
      const text = compactForCompare(
        normalizeText(getSegmentSearchText(segment)),
      );
      return Boolean(
        text &&
        compactWindow &&
        (compactWindow.includes(text) || text.includes(compactWindow)),
      );
    }) ||
    null
  );
}

function toHighlightTarget(
  field: ReviewField | null | undefined,
): HighlightTarget | null {
  if (!field) {
    return null;
  }

  const pageNumber =
    typeof field.pageNumber === 'number' && Number.isFinite(field.pageNumber)
      ? field.pageNumber
      : null;
  const bbox = normalizeBbox(field.bbox);
  if (!pageNumber || !bbox) {
    return null;
  }

  return {
    pageNumber,
    bbox,
  };
}

function normalizeBbox(
  bbox: [number, number, number, number] | null | undefined,
): [number, number, number, number] | null {
  if (!bbox || bbox.length !== 4) {
    return null;
  }

  const normalized = bbox.map((value) =>
    typeof value === 'number' && Number.isFinite(value) ? value : NaN,
  ) as [number, number, number, number];
  if (normalized.some((value) => Number.isNaN(value))) {
    return null;
  }

  const [left, top, right, bottom] = normalized;
  if (right <= left || bottom <= top) {
    return null;
  }

  return [
    Math.max(0, Math.min(1, left)),
    Math.max(0, Math.min(1, top)),
    Math.max(0, Math.min(1, right)),
    Math.max(0, Math.min(1, bottom)),
  ];
}

type InkGroup = {
  bbox: [number, number, number, number];
  area: number;
};

async function detectFirstPageHeaderAnchors(
  imageUrl: string,
  pageNumber: number,
): Promise<Partial<Record<ParsedFieldKey, HighlightTarget>>> {
  const image = await loadPreviewImage(imageUrl);
  const width = image.naturalWidth || image.width;
  const height = image.naturalHeight || image.height;
  if (!width || !height) {
    return {};
  }

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) {
    return {};
  }

  context.drawImage(image, 0, 0, width, height);
  const imageData = context.getImageData(0, 0, width, height);

  const anchors: Partial<Record<ParsedFieldKey, HighlightTarget>> = {};
  const caseGroups = mergeCloseInkGroups(
    findInkRowGroups(imageData, {
      left: 0.6,
      top: 0.04,
      right: 0.95,
      bottom: 0.25,
    }),
    0.006,
  )
    .filter((group) => {
      const [left, top, right, bottom] = group.bbox;
      return right - left >= 0.24 && top >= 0.075 && bottom <= 0.2;
    })
    .sort((a, b) => a.bbox[1] - b.bbox[1]);

  const blackCaseGroup = caseGroups[0] ?? null;
  const redCaseGroup = caseGroups[1] ?? null;

  if (blackCaseGroup) {
    anchors.caseBlackNo = {
      pageNumber,
      bbox: blackCaseGroup.bbox,
    };
  }
  if (redCaseGroup) {
    anchors.caseRedNo = {
      pageNumber,
      bbox: redCaseGroup.bbox,
    };
  }

  const redBottom =
    redCaseGroup?.bbox[3] ?? blackCaseGroup?.bbox[3] ?? 0.14;
  const centerGroups = mergeCloseInkGroups(
    findInkRowGroups(imageData, {
      left: 0.25,
      top: 0.14,
      right: 0.75,
      bottom: 0.36,
    }),
    0.008,
  ).sort((a, b) => a.bbox[1] - b.bbox[1]);
  const postHeaderGroups = centerGroups.filter(
    (group) => group.bbox[1] > redBottom + 0.02,
  );
  const titleGroup = postHeaderGroups[0] ?? null;
  const courtGroup = titleGroup
    ? postHeaderGroups.find((group) => {
        const [left, top, right] = group.bbox;
        const widthRatio = right - left;
        const centerX = (left + right) / 2;
        return (
          top > titleGroup.bbox[3] + 0.008 &&
          widthRatio >= 0.08 &&
          widthRatio <= 0.45 &&
          centerX >= 0.32 &&
          centerX <= 0.68
        );
      })
    : null;

  if (courtGroup) {
    anchors.courtName = {
      pageNumber,
      bbox: courtGroup.bbox,
    };
  }

  return anchors;
}

function loadPreviewImage(imageUrl: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new window.Image();
    image.crossOrigin = 'anonymous';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('Unable to load preview image.'));
    image.src = imageUrl;
  });
}

function findInkRowGroups(
  imageData: ImageData,
  region: { left: number; top: number; right: number; bottom: number },
): InkGroup[] {
  const { data, width, height } = imageData;
  const left = Math.max(0, Math.floor(width * region.left));
  const right = Math.min(width, Math.ceil(width * region.right));
  const top = Math.max(0, Math.floor(height * region.top));
  const bottom = Math.min(height, Math.ceil(height * region.bottom));
  const rowCount = Math.max(0, bottom - top);
  if (rowCount === 0 || right <= left) {
    return [];
  }

  const rowInk = new Uint32Array(rowCount);
  for (let y = top; y < bottom; y += 1) {
    let count = 0;
    for (let x = left; x < right; x += 1) {
      const offset = (y * width + x) * 4;
      if (
        data[offset + 3] > 0 &&
        data[offset] < 90 &&
        data[offset + 1] < 90 &&
        data[offset + 2] < 90
      ) {
        count += 1;
      }
    }
    rowInk[y - top] = count;
  }

  const minInkPerRow = Math.max(8, Math.floor((right - left) * 0.008));
  const rawGroups: Array<[number, number]> = [];
  let start: number | null = null;
  for (let row = 0; row < rowInk.length; row += 1) {
    if (rowInk[row] >= minInkPerRow) {
      if (start === null) {
        start = row;
      }
      continue;
    }

    if (start !== null) {
      if (row - start >= 4) {
        rawGroups.push([start, row - 1]);
      }
      start = null;
    }
  }
  if (start !== null && rowInk.length - start >= 4) {
    rawGroups.push([start, rowInk.length - 1]);
  }

  return rawGroups
    .map(([rowStart, rowEnd]) =>
      buildInkGroup(imageData, left, right, top + rowStart, top + rowEnd),
    )
    .filter((group): group is InkGroup => Boolean(group));
}

function buildInkGroup(
  imageData: ImageData,
  leftBound: number,
  rightBound: number,
  top: number,
  bottom: number,
): InkGroup | null {
  const { data, width, height } = imageData;
  let left = rightBound;
  let right = leftBound;
  let area = 0;

  for (let y = top; y <= bottom && y < height; y += 1) {
    for (let x = leftBound; x < rightBound; x += 1) {
      const offset = (y * width + x) * 4;
      if (
        data[offset + 3] > 0 &&
        data[offset] < 90 &&
        data[offset + 1] < 90 &&
        data[offset + 2] < 90
      ) {
        left = Math.min(left, x);
        right = Math.max(right, x);
        area += 1;
      }
    }
  }

  if (area === 0 || right <= left) {
    return null;
  }

  return {
    bbox: [left / width, top / height, right / width, bottom / height],
    area,
  };
}

function mergeCloseInkGroups(groups: InkGroup[], maxGap: number): InkGroup[] {
  const sorted = [...groups].sort((a, b) => a.bbox[1] - b.bbox[1]);
  const merged: InkGroup[] = [];
  for (const group of sorted) {
    const previous = merged[merged.length - 1];
    if (!previous || group.bbox[1] - previous.bbox[3] > maxGap) {
      merged.push({ ...group });
      continue;
    }

    previous.bbox = [
      Math.min(previous.bbox[0], group.bbox[0]),
      Math.min(previous.bbox[1], group.bbox[1]),
      Math.max(previous.bbox[2], group.bbox[2]),
      Math.max(previous.bbox[3], group.bbox[3]),
    ];
    previous.area += group.area;
  }

  return merged;
}

function getPageText(page: ImportPageAsset): string {
  return page.corrected_markdown || page.markdown || page.raw_markdown || '';
}

function normalizeText(value: string): string {
  return value
    .replace(/\r/g, '\n')
    .replace(/\n+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function trimTextBeforeKeyword(value: string, keyword: string): string {
  const normalized = normalizeText(value);
  const normalizedKeyword = normalizeText(keyword);
  if (!normalized || !normalizedKeyword) {
    return normalized;
  }

  const exactIndex = normalized.indexOf(normalizedKeyword);
  if (exactIndex >= 0) {
    return normalized.slice(exactIndex).trim();
  }

  const compactValue = compactForCompare(normalized);
  const compactKeyword = compactForCompare(normalizedKeyword);
  const compactIndex = compactValue.indexOf(compactKeyword);
  if (compactIndex < 0) {
    return normalized;
  }

  let nonSpaceCount = 0;
  for (let index = 0; index < normalized.length; index += 1) {
    if (/\s/u.test(normalized[index])) {
      continue;
    }
    if (nonSpaceCount >= compactIndex) {
      return normalized.slice(index).trim();
    }
    nonSpaceCount += 1;
  }

  return normalized;
}

type ParsedThaiDate = {
  ceYear: number;
  isBuddhistYear: boolean;
  monthIndex: number;
  useThaiDigits: boolean;
  day: number;
};

type KeywordWindowOptions = {
  maxChars: number;
  maxBlocks: number;
  minHighlightRows?: number;
  captureToPageEnd?: boolean;
};

function resolveFilingStartDate(scopeText: string): string | null {
  const nextDayDateText = pickFirst(scopeText, [
    /นับถัดจากวันฟ้อง\s*\(\s*ฟ้องวันที่\s*([^)]{4,80})\s*\)/,
    /นับถัดจากวันฟ้อง\s*ฟ้องวันที่\s*([^()]{4,80})/,
    /นับถัดจากวันฟ้อง\s*\(?([^()]{4,80})\)?\s*เป็นต้นไป/,
  ]);

  if (nextDayDateText) {
    return (
      shiftThaiDateText(nextDayDateText, 1) ||
      normalizeThaiDateDisplay(nextDayDateText)
    );
  }

  const filingDateText = pickFirst(scopeText, [
    /วันฟ้อง\s*\(\s*ฟ้องวันที่\s*([^)]{4,80})\s*\)/,
    /ฟ้องวันที่\s*([^()]{4,80})\s*(?:เป็นต้นไป|$)/,
    /วันฟ้อง\s*\(?([^()]{4,80})\)?\s*เป็นต้นไป/,
  ]);

  if (!filingDateText) {
    return null;
  }

  return normalizeThaiDateDisplay(filingDateText);
}

function normalizeThaiDateDisplay(value: string): string {
  const parsed = parseThaiDateText(value);
  if (!parsed) {
    return cleanupDateText(value);
  }
  return formatThaiDateText(
    parsed.day,
    parsed.monthIndex,
    parsed.ceYear,
    parsed,
  );
}

function shiftThaiDateText(value: string, days: number): string | null {
  const parsed = parseThaiDateText(value);
  if (!parsed) {
    return null;
  }

  const shifted = new Date(
    Date.UTC(parsed.ceYear, parsed.monthIndex, parsed.day),
  );
  shifted.setUTCDate(shifted.getUTCDate() + days);

  return formatThaiDateText(
    shifted.getUTCDate(),
    shifted.getUTCMonth(),
    shifted.getUTCFullYear(),
    parsed,
  );
}

function parseThaiDateText(value: string): ParsedThaiDate | null {
  const cleaned = cleanupDateText(value);
  const match = cleaned.match(
    /([0-9๐-๙]{1,2})\s*([^\d๐-๙\s]+)\s*([0-9๐-๙]{4})/,
  );
  if (!match) {
    return null;
  }

  const dayValue = Number(thaiDigitsToArabic(match[1]));
  const yearValue = Number(thaiDigitsToArabic(match[3]));
  const monthIndex = THAI_MONTH_LOOKUP.get(normalizeThaiMonthToken(match[2]));

  if (
    !Number.isFinite(dayValue) ||
    !Number.isFinite(yearValue) ||
    monthIndex == null
  ) {
    return null;
  }

  const isBuddhistYear = yearValue >= 2400;

  return {
    ceYear: isBuddhistYear ? yearValue - 543 : yearValue,
    isBuddhistYear,
    monthIndex,
    useThaiDigits: /[๐-๙]/.test(match[0]),
    day: dayValue,
  };
}

function cleanupDateText(value: string): string {
  return normalizeText(value)
    .replace(/^ฟ้องวันที่\s*/u, '')
    .replace(/^วันที่\s*/u, '')
    .replace(/\s*เป็นต้นไป$/u, '')
    .replace(/[()]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function normalizeThaiMonthToken(value: string): string {
  return value.toLowerCase().replace(/\s+/g, '').replace(/\.+$/g, '');
}

function thaiDigitsToArabic(value: string): string {
  return value.replace(/[๐-๙]/g, (digit) =>
    String('๐๑๒๓๔๕๖๗๘๙'.indexOf(digit)),
  );
}

function arabicDigitsToThai(value: string): string {
  return value.replace(/\d/g, (digit) => '๐๑๒๓๔๕๖๗๘๙'[Number(digit)] || digit);
}

function formatThaiDateText(
  day: number,
  monthIndex: number,
  ceYear: number,
  parsed: Pick<ParsedThaiDate, 'isBuddhistYear' | 'useThaiDigits'>,
): string {
  const yearValue = parsed.isBuddhistYear ? ceYear + 543 : ceYear;
  let text = `${day} ${THAI_MONTHS[monthIndex]} ${yearValue}`;

  if (parsed.useThaiDigits) {
    text = arabicDigitsToThai(text);
  }

  return text;
}

function extractKeywordWindows(
  markdown: string,
  keyword: string,
  options?: Partial<KeywordWindowOptions>,
): string[] {
  const maxChars = Math.max(120, options?.maxChars ?? 420);
  const maxBlocks = Math.max(1, options?.maxBlocks ?? 2);
  const captureToPageEnd = Boolean(options?.captureToPageEnd);
  const blocks = splitMarkdownIntoBlocks(markdown);
  const windows: string[] = [];
  const seen = new Set<string>();

  for (let blockIndex = 0; blockIndex < blocks.length; blockIndex += 1) {
    const block = blocks[blockIndex];
    const hitIndex = block.indexOf(keyword);
    if (hitIndex < 0) {
      continue;
    }

    const grouped: string[] = [];
    let collectedLength = 0;
    for (
      let cursor = blockIndex;
      cursor < blocks.length &&
      (captureToPageEnd || grouped.length < maxBlocks);
      cursor += 1
    ) {
      const candidate = blocks[cursor];
      const startAt = cursor === blockIndex ? hitIndex : 0;
      const sliced = normalizeText(candidate.slice(startAt));
      if (!sliced) {
        continue;
      }

      const nextLength =
        collectedLength + (grouped.length > 0 ? 1 : 0) + sliced.length;
      if (!captureToPageEnd && grouped.length > 0 && nextLength > maxChars) {
        break;
      }

      grouped.push(sliced);
      collectedLength = nextLength;

      if (!captureToPageEnd && collectedLength >= maxChars) {
        break;
      }
    }

    const snippet = normalizeText(grouped.join(' '));
    if (snippet && !seen.has(snippet)) {
      seen.add(snippet);
      windows.push(snippet);
    }
  }

  if (windows.length > 0) {
    return windows;
  }

  const normalized = normalizeText(markdown);
  if (!normalized) {
    return [];
  }

  let fromIndex = 0;
  while (fromIndex < normalized.length) {
    const hitIndex = normalized.indexOf(keyword, fromIndex);
    if (hitIndex < 0) {
      break;
    }

    const end = captureToPageEnd
      ? normalized.length
      : Math.min(normalized.length, hitIndex + keyword.length + maxChars);
    const snippet = normalized.slice(hitIndex, end).trim();
    if (snippet && !seen.has(snippet)) {
      seen.add(snippet);
      windows.push(snippet);
    }

    fromIndex = hitIndex + keyword.length;
  }

  return windows;
}

function splitMarkdownIntoBlocks(markdown: string): string[] {
  const source = markdown.replace(/\r/g, '\n');
  if (!source.trim()) {
    return [];
  }

  const fromParagraphBreaks = source
    .split(/\n\s*\n+/)
    .map((rawBlock) =>
      normalizeText(
        rawBlock
          .split('\n')
          .map((line) => line.trim())
          .filter((line) => line && !isMarkdownPageMarker(line))
          .join(' '),
      ),
    )
    .filter((block) => Boolean(block));

  if (fromParagraphBreaks.length > 1) {
    return fromParagraphBreaks;
  }

  const lines = source
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !isMarkdownPageMarker(line));

  if (lines.length === 0) {
    return [];
  }

  const grouped: string[] = [];
  let buffer: string[] = [];
  for (const line of lines) {
    buffer.push(line);
    if (buffer.length >= 3) {
      grouped.push(normalizeText(buffer.join(' ')));
      buffer = [];
    }
  }

  if (buffer.length > 0) {
    grouped.push(normalizeText(buffer.join(' ')));
  }

  return grouped.filter((block) => Boolean(block));
}

function isMarkdownPageMarker(line: string): boolean {
  return (
    /^#{1,6}\s*page\s+\d+\s*$/i.test(line) || /^page\s+\d+\s*$/i.test(line)
  );
}

function buildKeywordWindowBbox(
  page: ImportPageAsset,
  windowText: string,
  seedSegment: TextSegment | null,
  options?: Partial<KeywordWindowOptions>,
): [number, number, number, number] | null {
  const normalizedWindow = normalizeText(windowText);
  const compactWindow = compactForCompare(normalizedWindow);
  const minHighlightRows = Math.max(1, options?.minHighlightRows ?? 1);
  const captureToPageEnd = Boolean(options?.captureToPageEnd);
  if (!compactWindow) {
    return seedSegment?.bbox || null;
  }

  const sorted = [...page.segments].sort((a, b) => {
    if (Math.abs(a.bbox[1] - b.bbox[1]) > 0.003) {
      return a.bbox[1] - b.bbox[1];
    }
    return a.bbox[0] - b.bbox[0];
  });

  if (sorted.length === 0) {
    return seedSegment?.bbox || null;
  }

  let seedIndex = -1;
  if (seedSegment) {
    seedIndex = sorted.findIndex((segment) => segment === seedSegment);
    if (seedIndex < 0) {
      const [sx1, sy1, sx2, sy2] = seedSegment.bbox;
      seedIndex = sorted.findIndex((segment) => {
        const [x1, y1, x2, y2] = segment.bbox;
        return x1 === sx1 && y1 === sy1 && x2 === sx2 && y2 === sy2;
      });
    }
  }

  if (seedIndex >= 0) {
    if (captureToPageEnd) {
      const grouped: TextSegment[] = [sorted[seedIndex]];
      const seedBbox = sorted[seedIndex].bbox;
      const seedTop = seedBbox[1];
      const seedBottom = seedBbox[3];

      for (
        let i = seedIndex + 1;
        i < sorted.length && grouped.length < 96;
        i += 1
      ) {
        const candidate = sorted[i];
        const candidateCompact = compactForCompare(
          normalizeText(getSegmentSearchText(candidate)),
        );
        if (!candidateCompact) {
          continue;
        }

        const [x1, y1, x2] = candidate.bbox;
        const startsBelowKeyword = y1 >= seedTop - 0.006;
        const isNearKeywordRow = Math.abs(y1 - seedTop) <= 0.02;
        const isAfterKeywordLine = y1 >= seedBottom - 0.012;
        if (!startsBelowKeyword || (isNearKeywordRow && !isAfterKeywordLine)) {
          continue;
        }

        const overlapsKeywordBand =
          x2 >= seedBbox[0] - 0.12 && x1 <= seedBbox[2] + 0.12;
        const overlapsGroupedText =
          horizontalOverlapRatio(
            mergeBboxes(grouped.map((segment) => segment.bbox)) || seedBbox,
            candidate.bbox,
          ) >= 0.02;
        if (!overlapsKeywordBand && !overlapsGroupedText) {
          continue;
        }

        grouped.push(candidate);
      }

      return mergeBboxes(grouped.map((segment) => segment.bbox));
    }

    const grouped: TextSegment[] = [sorted[seedIndex]];
    let consumedLength = compactForCompare(
      normalizeText(getSegmentSearchText(sorted[seedIndex])),
    ).length;
    const targetLength = Math.max(36, compactWindow.length);
    const desiredRows = Math.max(
      minHighlightRows,
      estimateDesiredRows(sorted, seedIndex, targetLength),
    );

    for (
      let i = seedIndex + 1;
      i < sorted.length && grouped.length < 8;
      i += 1
    ) {
      const candidate = sorted[i];
      const previous = grouped[grouped.length - 1];
      const currentRows = estimateRowCount(grouped);
      const needMoreRows = currentRows < desiredRows;

      const verticalGap = candidate.bbox[1] - previous.bbox[3];
      if (verticalGap > 0.13) {
        break;
      }

      const horizontalScore = horizontalOverlapRatio(
        previous.bbox,
        candidate.bbox,
      );
      const candidateText = normalizeText(getSegmentSearchText(candidate));
      const candidateCompact = compactForCompare(candidateText);
      if (!candidateCompact) {
        continue;
      }

      const likelyByContent = isLikelyLineInWindow(
        compactWindow,
        candidateCompact,
      );
      const likelyByFlow = verticalGap <= 0.08 && horizontalScore >= 0.12;
      const forceContinueForCoverage = consumedLength < targetLength * 0.98;
      const forceContinueForRows =
        needMoreRows &&
        verticalGap <= 0.1 &&
        horizontalScore >= 0.08 &&
        candidateCompact.length >= 10;

      if (
        !likelyByContent &&
        !likelyByFlow &&
        !forceContinueForCoverage &&
        !forceContinueForRows
      ) {
        break;
      }
      if (
        !likelyByContent &&
        !likelyByFlow &&
        !forceContinueForRows &&
        forceContinueForCoverage &&
        verticalGap > 0.09
      ) {
        break;
      }

      grouped.push(candidate);
      consumedLength += candidateCompact.length;

      if (
        consumedLength >= targetLength &&
        estimateRowCount(grouped) >= desiredRows
      ) {
        break;
      }
    }

    if (estimateRowCount(grouped) < minHighlightRows) {
      for (
        let i = seedIndex + 1;
        i < sorted.length && estimateRowCount(grouped) < minHighlightRows;
        i += 1
      ) {
        const candidate = sorted[i];
        if (grouped.includes(candidate)) {
          continue;
        }

        const previous = grouped[grouped.length - 1];
        const verticalGap = candidate.bbox[1] - previous.bbox[3];
        if (verticalGap > 0.16) {
          break;
        }

        const horizontalScore = horizontalOverlapRatio(
          previous.bbox,
          candidate.bbox,
        );
        const candidateCompact = compactForCompare(
          normalizeText(getSegmentSearchText(candidate)),
        );
        if (!candidateCompact) {
          continue;
        }

        const isReasonablyNear =
          verticalGap <= 0.12 &&
          (horizontalScore >= 0.04 || candidateCompact.length >= 6);
        if (!isReasonablyNear) {
          continue;
        }

        grouped.push(candidate);
      }
    }

    return mergeBboxes(grouped.map((segment) => segment.bbox));
  }

  const matchedSegments = sorted.filter((segment) => {
    const line = normalizeText(getSegmentSearchText(segment));
    const compactLine = compactForCompare(line);
    if (!compactLine || compactLine.length < 4) {
      return false;
    }
    return isLikelyLineInWindow(compactWindow, compactLine);
  });

  if (matchedSegments.length > 0) {
    return mergeBboxes(matchedSegments.map((segment) => segment.bbox));
  }

  return seedSegment?.bbox || null;
}

function mergeBboxes(
  bboxes: [number, number, number, number][],
): [number, number, number, number] | null {
  if (bboxes.length === 0) {
    return null;
  }

  let left = bboxes[0][0];
  let top = bboxes[0][1];
  let right = bboxes[0][2];
  let bottom = bboxes[0][3];

  for (let i = 1; i < bboxes.length; i += 1) {
    const [x1, y1, x2, y2] = bboxes[i];
    left = Math.min(left, x1);
    top = Math.min(top, y1);
    right = Math.max(right, x2);
    bottom = Math.max(bottom, y2);
  }

  return [left, top, right, bottom];
}

function compactForCompare(value: string): string {
  return value
    .toLowerCase()
    .replace(/[\s\u200b.,/#!$%^&*;:{}=\-_`~()'"“”‘’\[\]<>?|\\…]/g, '');
}

function isLikelyLineInWindow(
  windowCompact: string,
  lineCompact: string,
): boolean {
  if (!windowCompact || !lineCompact) {
    return false;
  }
  if (windowCompact.includes(lineCompact)) {
    return true;
  }

  const head = lineCompact.slice(0, Math.min(lineCompact.length, 14));
  if (head.length >= 6 && windowCompact.includes(head)) {
    return true;
  }

  const tail = lineCompact.slice(-Math.min(lineCompact.length, 12));
  if (tail.length >= 6 && windowCompact.includes(tail)) {
    return true;
  }

  if (lineCompact.length < 10) {
    return false;
  }

  let found = 0;
  let total = 0;
  const gram = 5;
  for (let i = 0; i <= lineCompact.length - gram; i += 2) {
    const chunk = lineCompact.slice(i, i + gram);
    total += 1;
    if (windowCompact.includes(chunk)) {
      found += 1;
    }
  }

  if (total === 0) {
    return false;
  }
  return found / total >= 0.18;
}

function horizontalOverlapRatio(
  a: [number, number, number, number],
  b: [number, number, number, number],
): number {
  const left = Math.max(a[0], b[0]);
  const right = Math.min(a[2], b[2]);
  const overlap = Math.max(0, right - left);
  const widthA = Math.max(0.0001, a[2] - a[0]);
  const widthB = Math.max(0.0001, b[2] - b[0]);
  return overlap / Math.min(widthA, widthB);
}

function estimateDesiredRows(
  sortedSegments: TextSegment[],
  seedIndex: number,
  targetLength: number,
): number {
  const sampleLengths: number[] = [];
  let previous: TextSegment | null = null;

  for (
    let i = seedIndex;
    i < sortedSegments.length && sampleLengths.length < 3;
    i += 1
  ) {
    const candidate = sortedSegments[i];
    if (previous) {
      const verticalGap = candidate.bbox[1] - previous.bbox[3];
      if (verticalGap > 0.13) {
        break;
      }
    }

    const compact = compactForCompare(
      normalizeText(getSegmentSearchText(candidate)),
    );
    if (compact.length >= 10) {
      sampleLengths.push(compact.length);
      previous = candidate;
    }
  }

  if (sampleLengths.length === 0) {
    return 1;
  }

  const averageLength =
    sampleLengths.reduce((sum, length) => sum + length, 0) /
    sampleLengths.length;

  return Math.max(
    1,
    Math.min(8, Math.ceil(targetLength / Math.max(28, averageLength))),
  );
}

function estimateRowCount(segments: TextSegment[]): number {
  if (segments.length === 0) {
    return 0;
  }

  const sorted = [...segments].sort((a, b) => {
    if (Math.abs(a.bbox[1] - b.bbox[1]) > 0.003) {
      return a.bbox[1] - b.bbox[1];
    }
    return a.bbox[0] - b.bbox[0];
  });

  const rows: { center: number; height: number }[] = [];
  for (const segment of sorted) {
    const center = (segment.bbox[1] + segment.bbox[3]) / 2;
    const height = Math.max(0.001, segment.bbox[3] - segment.bbox[1]);
    const matchIndex = rows.findIndex((row) => {
      const threshold = Math.max(0.012, Math.min(0.05, row.height * 0.6));
      return Math.abs(center - row.center) <= threshold;
    });

    if (matchIndex < 0) {
      rows.push({ center, height });
      continue;
    }

    const row = rows[matchIndex];
    row.center = (row.center + center) / 2;
    row.height = Math.max(row.height, height);
  }

  return rows.length;
}

function pickFirst(text: string, patterns: RegExp[]): string | null {
  for (const pattern of patterns) {
    const match = text.match(pattern);
    const value = match?.[1]?.trim();
    if (value) {
      return value;
    }
  }
  return null;
}

function getSegmentSearchText(segment: TextSegment): string {
  return [
    segment.corrected_text || '',
    segment.text || '',
    segment.raw_text || '',
  ]
    .join(' ')
    .trim();
}

function getSegmentDisplayText(segment: TextSegment): string {
  return normalizeText(
    segment.corrected_text || segment.text || segment.raw_text || '',
  );
}

function stripOcrLineDecorators(value: string): string {
  return value.replace(/^\s{0,3}(?:#{1,6}|[-*+]|>\s*)\s+/u, '').trim();
}

function extractDisplayTextFromBbox(
  page: ImportPageAsset,
  bbox: [number, number, number, number],
): string {
  const expanded = expandBbox(bbox, 0.005, 0.008);
  const overlapping = page.segments
    .filter((segment) => {
      const overlapArea = calculateBboxOverlapArea(segment.bbox, expanded);
      if (overlapArea <= 0) {
        return false;
      }
      const width = Math.max(0, segment.bbox[2] - segment.bbox[0]);
      const height = Math.max(0, segment.bbox[3] - segment.bbox[1]);
      const segmentArea = Math.max(0.000001, width * height);
      return overlapArea / segmentArea >= 0.12;
    })
    .sort((a, b) => {
      if (Math.abs(a.bbox[1] - b.bbox[1]) > 0.003) {
        return a.bbox[1] - b.bbox[1];
      }
      return a.bbox[0] - b.bbox[0];
    });

  if (overlapping.length === 0) {
    return '';
  }

  const dedupedLines: string[] = [];
  for (const segment of overlapping) {
    const line = getSegmentDisplayText(segment);
    if (!line) {
      continue;
    }
    if (dedupedLines[dedupedLines.length - 1] === line) {
      continue;
    }
    dedupedLines.push(line);
  }

  return normalizeText(dedupedLines.join(' '));
}

function expandBbox(
  bbox: [number, number, number, number],
  padX: number,
  padY: number,
): [number, number, number, number] {
  const left = Math.max(0, bbox[0] - padX);
  const top = Math.max(0, bbox[1] - padY);
  const right = Math.min(1, bbox[2] + padX);
  const bottom = Math.min(1, bbox[3] + padY);
  return [left, top, right, bottom];
}

function calculateBboxOverlapArea(
  a: [number, number, number, number],
  b: [number, number, number, number],
): number {
  const left = Math.max(a[0], b[0]);
  const top = Math.max(a[1], b[1]);
  const right = Math.min(a[2], b[2]);
  const bottom = Math.min(a[3], b[3]);
  const width = Math.max(0, right - left);
  const height = Math.max(0, bottom - top);
  return width * height;
}

function findAnchorByPatterns(
  page: ImportPageAsset,
  patterns: RegExp[],
): HighlightTarget | null {
  for (const segment of page.segments) {
    const line = normalizeText(getSegmentSearchText(segment));
    if (!line) {
      continue;
    }
    const matched = patterns.some((pattern) => pattern.test(line));
    if (!matched) {
      continue;
    }
    return {
      pageNumber: page.page_number,
      bbox: getSegmentAnchorBbox(segment),
    };
  }
  return null;
}

function findAnchorByMarkers(
  page: ImportPageAsset,
  markers: string[],
): HighlightTarget | null {
  for (const segment of page.segments) {
    const line = normalizeText(getSegmentSearchText(segment));
    if (!line) {
      continue;
    }
    const matched = markers.some((marker) => line.includes(marker));
    if (!matched) {
      continue;
    }
    return {
      pageNumber: page.page_number,
      bbox: getSegmentAnchorBbox(segment),
    };
  }
  return null;
}

function findCaseHeaderInfo(page: ImportPageAsset): CaseHeaderInfo {
  const redSegment = findCaseSegment(page, CASE_RED_PATTERNS, CASE_RED_MARKERS);
  const blackSegment = findCaseSegment(
    page,
    CASE_BLACK_PATTERNS,
    CASE_BLACK_MARKERS,
  );

  const redLine = redSegment
    ? normalizeText(getSegmentSearchText(redSegment))
    : '';
  const blackLine = blackSegment
    ? normalizeText(getSegmentSearchText(blackSegment))
    : '';
  const redNo = redLine
    ? pickFirst(redLine, CASE_RED_PATTERNS)
    : pickFirst(getPageText(page), CASE_RED_PATTERNS);
  const blackNo = blackLine
    ? pickFirst(blackLine, CASE_BLACK_PATTERNS)
    : pickFirst(getPageText(page), CASE_BLACK_PATTERNS);
  const inferredAnchors = inferStackedCaseAnchors(
    page,
    redSegment,
    blackSegment,
  );

  return {
    redNo,
    blackNo,
    redAnchor:
      inferredAnchors.redAnchor ||
      (redSegment
        ? {
            pageNumber: page.page_number,
            bbox: getCaseAnchorBbox(redSegment, 'red'),
          }
        : null),
    blackAnchor:
      inferredAnchors.blackAnchor ||
      (blackSegment
        ? {
            pageNumber: page.page_number,
            bbox: getCaseAnchorBbox(blackSegment, 'black'),
          }
        : null),
  };
}

function findCaseSegment(
  page: ImportPageAsset,
  patterns: RegExp[],
  markers: string[],
): TextSegment | null {
  const exact = page.segments.find((segment) => {
    const line = normalizeText(getSegmentSearchText(segment));
    return line && patterns.some((pattern) => pattern.test(line));
  });
  if (exact) {
    return exact;
  }

  return (
    page.segments.find((segment) => {
      const line = normalizeText(getSegmentSearchText(segment));
      return line && markers.some((marker) => line.includes(marker));
    }) || null
  );
}

function inferStackedCaseAnchors(
  page: ImportPageAsset,
  redSegment: TextSegment | null,
  blackSegment: TextSegment | null,
): Pick<CaseHeaderInfo, 'redAnchor' | 'blackAnchor'> {
  if (!redSegment) {
    return { redAnchor: null, blackAnchor: null };
  }

  const redBox = getSegmentAnchorBbox(redSegment);
  const blackBox = blackSegment ? getSegmentAnchorBbox(blackSegment) : null;
  const redLine = normalizeText(getSegmentSearchText(redSegment));
  const isHeaderNumberBlock =
    redBox[0] > 0.48 &&
    redBox[1] < 0.18 &&
    Boolean(blackSegment) &&
    Boolean(redLine.includes(THAI_RED_MARKER));

  if (!isHeaderNumberBlock || !blackBox) {
    return { redAnchor: null, blackAnchor: null };
  }

  const blackLooksDetached = blackBox[2] < redBox[0] || blackBox[0] < 0.35;
  if (!blackLooksDetached) {
    return { redAnchor: null, blackAnchor: null };
  }
  const stackedBlackBox = getCaseAnchorBbox(redSegment, 'black');
  const stackedRedBox = getCaseAnchorBbox(redSegment, 'red');

  return {
    blackAnchor: {
      pageNumber: page.page_number,
      bbox: stackedBlackBox,
    },
    redAnchor: {
      pageNumber: page.page_number,
      bbox: stackedRedBox,
    },
  };
}

function findCourtInfo(page: ImportPageAsset): CourtInfo | null {
  for (const segment of page.segments) {
    const name = extractCourtName(
      stripOcrLineDecorators(normalizeText(getSegmentDisplayText(segment))),
    );
    if (!name) {
      continue;
    }
    return {
      name,
      anchor: {
        pageNumber: page.page_number,
        bbox: getCourtAnchorBbox(page, segment),
      },
    };
  }

  const pageLines = getPageText(page)
    .split(/\n+/)
    .map((line) => stripOcrLineDecorators(normalizeText(line)))
    .filter(Boolean);
  for (const line of pageLines) {
    const name = extractCourtName(line);
    if (name) {
      return { name, anchor: null };
    }
  }

  return null;
}

function extractCourtName(line: string): string | null {
  if (!line.startsWith('ศาล') || line.includes('สำหรับ')) {
    return null;
  }

  const match = line.match(
    /^(ศาล(?:จังหวัด|แขวง|แพ่ง|อาญา|เยาวชน|แรงงาน|ภาษี|ทรัพย์สิน|ล้มละลาย|อุทธรณ์|ฎีกา|สูงสุด)[^\s]*)/,
  );
  return match?.[1]?.trim() || null;
}

function getCourtAnchorBbox(
  page: ImportPageAsset,
  courtSegment: TextSegment,
): [number, number, number, number] {
  const directMatch = findBestSegmentBboxByText(
    page,
    getSegmentDisplayText(courtSegment),
  );
  if (directMatch) {
    return insetBbox(directMatch, 0.0025, 0.0015);
  }

  const lineBboxes = getSegmentLineBboxes(courtSegment);
  const centeredLine =
    lineBboxes.find((bbox) => {
      const centerX = (bbox[0] + bbox[2]) / 2;
      return centerX >= 0.28 && centerX <= 0.72;
    }) ||
    lineBboxes[0] ||
    getSegmentAnchorBbox(courtSegment);

  return insetBbox(centeredLine, 0.0025, 0.0015);
}

function shiftBbox(
  bbox: [number, number, number, number],
  xShift: number,
  yShift: number,
): [number, number, number, number] {
  const width = bbox[2] - bbox[0];
  const height = bbox[3] - bbox[1];
  const left = Math.min(Math.max(0, bbox[0] + xShift), 1 - width);
  const top = Math.min(Math.max(0, bbox[1] + yShift), 1 - height);

  return [left, top, left + width, top + height];
}

function getSegmentAnchorBbox(
  segment: TextSegment,
): [number, number, number, number] {
  if (segment.bboxes && segment.bboxes.length > 0) {
    return mergeBboxes(segment.bboxes) || segment.bbox;
  }

  return segment.bbox;
}

function getSegmentLineBboxes(
  segment: TextSegment,
): [number, number, number, number][] {
  const rawBboxes =
    Array.isArray(segment.bboxes) && segment.bboxes.length > 0
      ? segment.bboxes
      : [segment.bbox];

  return rawBboxes
    .map((bbox) => normalizeBbox(bbox))
    .filter((bbox): bbox is [number, number, number, number] => Boolean(bbox))
    .sort((left, right) => {
      if (Math.abs(left[1] - right[1]) > 0.003) {
        return left[1] - right[1];
      }
      return left[0] - right[0];
    });
}

function findHeaderAnchorByFieldValue(
  page: ImportPageAsset,
  key: ParsedFieldKey,
  value: string | null,
): HighlightTarget | null {
  if (!value) {
    return null;
  }

  if (key === 'caseBlackNo' || key === 'caseRedNo') {
    const bbox = findCaseAnchorByValue(page, key, value);
    return bbox
      ? {
          pageNumber: page.page_number,
          bbox,
        }
      : null;
  }

  if (key === 'courtName') {
    const bbox = findCourtAnchorByValue(page, value);
    return bbox
      ? {
          pageNumber: page.page_number,
          bbox,
        }
      : null;
  }

  return null;
}

function findCaseAnchorByValue(
  page: ImportPageAsset,
  key: 'caseBlackNo' | 'caseRedNo',
  value: string,
): [number, number, number, number] | null {
  const target = compactForCompare(normalizeText(value));
  if (!target) {
    return null;
  }

  const markerText =
    key === 'caseBlackNo' ? THAI_BLACK_MARKER : THAI_RED_MARKER;
  let bestMatch: {
    bbox: [number, number, number, number];
    score: number;
  } | null = null;

  for (const segment of page.segments) {
    const text = normalizeText(getSegmentSearchText(segment));
    const compactText = compactForCompare(text);
    if (!compactText) {
      continue;
    }

    const baseBox = getSegmentAnchorBbox(segment);
    if (baseBox[1] > 0.24 || baseBox[0] < 0.42) {
      continue;
    }

    const hasTarget =
      compactText.includes(target) || target.includes(compactText);
    const hasMarker = text.includes(markerText);
    const hasDigits = /[0-9๐-๙]/u.test(text);
    if (!hasTarget && !(hasMarker && hasDigits)) {
      continue;
    }

    const valueSimilarity = hasTarget
      ? Math.min(target.length, compactText.length) /
        Math.max(target.length, compactText.length)
      : 0;
    const noisyHeaderPenalty = text.includes('สำหรับศาลใช้') ? 2.5 : 0;
    const score =
      valueSimilarity * 8 +
      (hasTarget ? 3 : 0) +
      (hasMarker ? 2 : 0) +
      (hasDigits ? 1 : 0) -
      baseBox[1] * 2.5 -
      noisyHeaderPenalty;

    const bbox =
      text.includes(THAI_BLACK_MARKER) && text.includes(THAI_RED_MARKER)
        ? getCaseAnchorBbox(segment, key === 'caseBlackNo' ? 'black' : 'red')
        : insetBbox(baseBox, 0.0025, 0.0015);

    if (!bestMatch || score > bestMatch.score) {
      bestMatch = { bbox, score };
    }
  }

  return bestMatch?.bbox || null;
}

function findCourtAnchorByValue(
  page: ImportPageAsset,
  value: string,
): [number, number, number, number] | null {
  const target = compactForCompare(normalizeText(value));
  if (!target) {
    return null;
  }

  let bestMatch: {
    bbox: [number, number, number, number];
    score: number;
  } | null = null;

  for (const segment of page.segments) {
    const text = normalizeText(getSegmentSearchText(segment));
    const compactText = compactForCompare(text);
    if (!compactText) {
      continue;
    }

    const bbox = getSegmentAnchorBbox(segment);
    const centerX = (bbox[0] + bbox[2]) / 2;
    const hasTarget =
      compactText.includes(target) || target.includes(compactText);
    const looksLikeCourt = text.startsWith('ศาล');
    if (!hasTarget && !looksLikeCourt) {
      continue;
    }
    if (bbox[1] < 0.18 || bbox[1] > 0.5) {
      continue;
    }

    const valueSimilarity = hasTarget
      ? Math.min(target.length, compactText.length) /
        Math.max(target.length, compactText.length)
      : 0;
    const centerScore = 1 - Math.min(1, Math.abs(centerX - 0.5) / 0.25);
    const noisyHeaderPenalty = text.includes('สำหรับศาลใช้') ? 3 : 0;
    const score =
      valueSimilarity * 9 +
      (looksLikeCourt ? 2 : 0) +
      centerScore * 2 -
      noisyHeaderPenalty -
      Math.abs(bbox[1] - 0.34) * 3;

    const anchoredBbox = insetBbox(bbox, 0.0025, 0.0015);
    if (!bestMatch || score > bestMatch.score) {
      bestMatch = { bbox: anchoredBbox, score };
    }
  }

  return bestMatch?.bbox || null;
}

function getCaseAnchorBbox(
  segment: TextSegment,
  variant: 'black' | 'red',
): [number, number, number, number] {
  const text = normalizeText(getSegmentSearchText(segment));
  const lineBboxes = getSegmentLineBboxes(segment);
  const hasBothMarkers =
    text.includes(THAI_BLACK_MARKER) && text.includes(THAI_RED_MARKER);

  if (lineBboxes.length >= 2) {
    const selected =
      variant === 'black' ? lineBboxes[0] : lineBboxes[lineBboxes.length - 1];
    return insetBbox(selected, 0.0025, 0.0015);
  }

  const baseBox = getSegmentAnchorBbox(segment);
  if (hasBothMarkers) {
    return insetBbox(
      splitBboxVertically(baseBox, variant === 'black' ? 0 : 1, 2),
      0.0025,
      0.0015,
    );
  }

  return insetBbox(baseBox, 0.0025, 0.0015);
}

function splitBboxVertically(
  bbox: [number, number, number, number],
  index: number,
  totalParts: number,
): [number, number, number, number] {
  const clampedParts = Math.max(1, totalParts);
  const clampedIndex = Math.min(Math.max(0, index), clampedParts - 1);
  const height = (bbox[3] - bbox[1]) / clampedParts;
  const top = bbox[1] + height * clampedIndex;
  const bottom =
    clampedIndex === clampedParts - 1 ? bbox[3] : Math.min(1, top + height);

  return [bbox[0], top, bbox[2], bottom];
}

function insetBbox(
  bbox: [number, number, number, number],
  insetX: number,
  insetY: number,
): [number, number, number, number] {
  const maxInsetX = Math.max(0, Math.min(insetX, (bbox[2] - bbox[0]) * 0.22));
  const maxInsetY = Math.max(0, Math.min(insetY, (bbox[3] - bbox[1]) * 0.22));

  return [
    Math.max(0, bbox[0] + maxInsetX),
    Math.max(0, bbox[1] + maxInsetY),
    Math.min(1, bbox[2] - maxInsetX),
    Math.min(1, bbox[3] - maxInsetY),
  ];
}

function findBestSegmentBboxByText(
  page: ImportPageAsset,
  value: string,
): [number, number, number, number] | null {
  const target = compactForCompare(normalizeText(value));
  if (target.length < 3) {
    return null;
  }

  let bestMatch: {
    bbox: [number, number, number, number];
    score: number;
  } | null = null;

  for (const segment of page.segments) {
    const segmentText = compactForCompare(
      normalizeText(getSegmentSearchText(segment)),
    );
    if (!segmentText) {
      continue;
    }
    if (!target.includes(segmentText) && !segmentText.includes(target)) {
      continue;
    }

    const bbox = getSegmentAnchorBbox(segment);
    const overlapScore =
      Math.min(target.length, segmentText.length) /
      Math.max(target.length, segmentText.length);
    const width = bbox[2] - bbox[0];
    const score = overlapScore - bbox[1] * 0.15 - width * 0.04;

    if (!bestMatch || score > bestMatch.score) {
      bestMatch = { bbox, score };
    }
  }

  return bestMatch?.bbox || null;
}

function getHighlightStyle(bbox: [number, number, number, number]) {
  const xPadding = 0.06;
  const yPadding = 0.006;
  const left = Math.max(bbox[0] - xPadding, 0);
  const top = Math.max(bbox[1] - yPadding, 0);
  const right = Math.min(bbox[2] + xPadding, 1);
  const bottom = Math.min(bbox[3] + yPadding, 1);

  return {
    left: `${left * 100}%`,
    top: `${top * 100}%`,
    width: `${(right - left) * 100}%`,
    height: `${(bottom - top) * 100}%`,
  };
}
