'use client';

import Link from 'next/link';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Flex, Grid, Image, Spinner, Stack, Text } from '@chakra-ui/react';
import { MdEdit } from 'react-icons/md';

import { type AuthUser, getAuthHeaders, getCurrentUser } from '../../lib/auth';
import {
  API_BASE_URL,
  type ImportPageAsset,
  type ImportRecord,
  type ReviewField,
  type ReviewFieldKey,
} from '../../lib/review';

type TrFieldKey = Extract<
  ReviewFieldKey,
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
  | 'updateDate'
>;

type TrFieldConfig = {
  key: TrFieldKey;
  label: string;
  tone?: 'wide' | 'title';
  gridColumn?: { base: string; md: string };
};

type TrValidationIssue = NonNullable<
  ImportRecord['field_validation_issues']
>[number];

const TR_FIELDS: TrFieldConfig[] = [
  // { key: 'tableName', label: 'table name', tone: 'title' },
  {
    key: 'personId',
    label: 'ID',
    gridColumn: { base: '1 / -1', md: '1 / span 3' },
  },
  {
    key: 'houseCode',
    label: 'รหัสบ้าน',
    gridColumn: { base: '1 / -1', md: '4 / span 3' },
  },
  {
    key: 'personName',
    label: 'ชื่อ',
    gridColumn: { base: '1 / -1', md: '1 / span 3' },
  },
  {
    key: 'gender',
    label: 'เพศ',
    gridColumn: { base: '1 / -1', md: '4 / span 1' },
  },
  {
    key: 'nationality',
    label: 'สัญชาติ',
    gridColumn: { base: '1 / -1', md: '5 / span 2' },
  },
  {
    key: 'birthDate',
    label: 'วันเกิด',
    gridColumn: { base: '1 / -1', md: '1 / span 3' },
  },
  {
    key: 'age',
    label: 'อายุ',
    gridColumn: { base: '1 / -1', md: '4 / span 1' },
  },
  {
    key: 'status',
    label: 'สถานภาพที่อยู่อาศัย',
    gridColumn: { base: '1 / -1', md: '5 / span 2' },
  },
  {
    key: 'motherName',
    label: 'มารดา',
    gridColumn: { base: '1 / -1', md: '1 / span 3' },
  },
  {
    key: 'motherId',
    label: 'ID',
    gridColumn: { base: '1 / -1', md: '4 / span 2' },
  },
  {
    key: 'motherNationality',
    label: 'สัญชาติ',
    gridColumn: { base: '1 / -1', md: '6 / span 1' },
  },
  {
    key: 'fatherName',
    label: 'บิดา',
    gridColumn: { base: '1 / -1', md: '1 / span 3' },
  },
  {
    key: 'fatherId',
    label: 'ID',
    gridColumn: { base: '1 / -1', md: '4 / span 2' },
  },
  {
    key: 'fatherNationality',
    label: 'สัญชาติ',
    gridColumn: { base: '1 / -1', md: '6 / span 1' },
  },
  {
    key: 'address',
    label: 'ที่อยู่',
    tone: 'wide',
    gridColumn: { base: '1 / -1', md: '1 / -1' },
  },
  {
    key: 'postalCode',
    label: 'รหัสไปรษณีย์',
    gridColumn: { base: '1 / -1', md: '1 / span 1' },
  },
  {
    key: 'moveInDate',
    label: 'เข้ามาอยู่วันที่',
    gridColumn: { base: '1 / -1', md: '2 / span 2' },
  },
  {
    key: 'remark',
    label: 'Remark',
    tone: 'wide',
    gridColumn: { base: '1 / -1', md: '4 / span 2' },
  },
  {
    key: 'updateDate',
    label: 'Update Date',
    gridColumn: { base: '1 / -1', md: '6 / span 1' },
  },
];

const TR_DECEASED_DATE_FIELD: TrFieldConfig = {
  key: 'deceasedDate',
  label: 'วันที่เสียชีวิต',
  gridColumn: { base: '1 / -1', md: '1 / span 2' },
};

const ALL_TR_FIELD_KEYS = new Set<TrFieldKey>([
  ...TR_FIELDS.map((field) => field.key),
  TR_DECEASED_DATE_FIELD.key,
]);

const PERSON_ID_FIELDS = new Set<TrFieldKey>([
  'personId',
  'motherId',
  'fatherId',
]);

const HOUSE_CODE_FIELDS = new Set<TrFieldKey>(['houseCode']);
const POSTAL_CODE_FIELDS = new Set<TrFieldKey>(['postalCode']);

const panelStyles = {
  borderWidth: '1px',
  borderColor: 'rgba(73, 59, 36, 0.12)',
  borderRadius: '20px',
  bg: 'rgba(255, 255, 255, 0.92)',
  boxShadow: '0 12px 30px rgba(31, 26, 20, 0.07)',
};

export default function TrImportInspectPage() {
  const params = useParams<{ importId: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const importId = Array.isArray(params?.importId)
    ? params.importId[0]
    : params?.importId;
  const source = searchParams.get('source');
  const isStaffSource = source === 'staff';
  const isManagerSource = source === 'manager';
  const isRoleRecordSource = isStaffSource || isManagerSource;

  const [record, setRecord] = useState<ImportRecord | null>(null);
  const [activePageNumber, setActivePageNumber] = useState(1);
  const [editingFieldKey, setEditingFieldKey] = useState<TrFieldKey | null>(
    null,
  );
  const [editingValue, setEditingValue] = useState('');
  const [fieldOverrides, setFieldOverrides] = useState<
    Partial<Record<TrFieldKey, string>>
  >({});
  const [confirmedFieldKeys, setConfirmedFieldKeys] = useState<
    Partial<Record<TrFieldKey, boolean>>
  >({});
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [previewObjectUrl, setPreviewObjectUrl] = useState<string | null>(null);
  const [isCompletingStaffRecord, setIsCompletingStaffRecord] = useState(false);
  const [staffActionMessage, setStaffActionMessage] = useState<string | null>(
    null,
  );
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const previewScrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (importId) {
      void loadImport(importId);
    }
  }, [importId, isStaffSource, isManagerSource]);

  useEffect(() => {
    if (!importId || record?.status !== 'ocr_running') {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadImport(importId, { silent: true });
    }, 5_000);
    return () => window.clearInterval(intervalId);
  }, [importId, isStaffSource, isManagerSource, record?.status]);

  useEffect(() => {
    if (!isRoleRecordSource) {
      setCurrentUser(null);
      return;
    }

    let isMounted = true;
    async function loadCurrentUser() {
      const user = await getCurrentUser().catch(() => null);
      if (isMounted) {
        setCurrentUser(user);
      }
    }

    void loadCurrentUser();
    return () => {
      isMounted = false;
    };
  }, [isRoleRecordSource]);

  const selectedPage =
    record?.pages.find((page) => page.page_number === activePageNumber) ?? null;
  const previewUrl =
    record && selectedPage
      ? isStaffSource
        ? `${API_BASE_URL}/api/staff/records/${record.id}/preview`
        : isManagerSource
          ? `${API_BASE_URL}/api/manager/records/${record.id}/preview`
          : `${API_BASE_URL}/api/imports/${record.id}/pages/${selectedPage.page_number}/original`
      : '';
  const displayPreviewUrl = isRoleRecordSource
    ? previewObjectUrl || ''
    : previewUrl;

  const isCompleted =
    String(record?.save_btn || '')
      .trim()
      .toUpperCase() === 'Y';
  const isManagerSelfAssigned = useMemo(() => {
    if (!record || !currentUser || !isManagerSource) {
      return false;
    }
    const assigneeValues = [
      record.assigned_to_user_id,
      record.assigned_to_username,
    ]
      .map((value) => String(value || '').trim())
      .filter(Boolean);
    const currentUserValues = [currentUser.id, currentUser.username]
      .map((value) => String(value || '').trim())
      .filter(Boolean);
    return currentUserValues.some((value) => assigneeValues.includes(value));
  }, [currentUser, isManagerSource, record]);
  const canReviewRecord = isStaffSource || isManagerSelfAssigned;
  const isDeceased = useMemo(() => hasDeceasedMarker(record), [record]);
  const reviewFields = useMemo(
    () => (isDeceased ? [...TR_FIELDS, TR_DECEASED_DATE_FIELD] : TR_FIELDS),
    [isDeceased],
  );
  const deceasedDate = useMemo(
    () => getFieldValue('deceasedDate'),
    [fieldOverrides, record],
  );
  const validationIssuesByField = useMemo(
    () => groupTrValidationIssuesByField(record?.field_validation_issues),
    [record?.field_validation_issues],
  );
  const reviewBlockingFieldKeys = useMemo(
    () =>
      reviewFields
        .map((field) => field.key)
        .filter((key) =>
          fieldRequiresHumanReview(
            record?.review_data?.fields?.[key] ?? null,
            validationIssuesByField[key] ?? [],
          ),
        ),
    [record, reviewFields, validationIssuesByField],
  );
  const unresolvedReviewFieldKeys = useMemo(
    () =>
      reviewBlockingFieldKeys.filter(
        (key) =>
          !confirmedFieldKeys[key] && typeof fieldOverrides[key] !== 'string',
      ),
    [confirmedFieldKeys, fieldOverrides, reviewBlockingFieldKeys],
  );
  const isCompleteBlockedByReview = unresolvedReviewFieldKeys.length > 0;
  const isCompleteDisabled =
    isCompletingStaffRecord || isCompleted || isCompleteBlockedByReview;

  useEffect(() => {
    if (!previewUrl || !isRoleRecordSource) {
      setPreviewObjectUrl(null);
      return;
    }

    let cancelled = false;
    let objectUrl: string | null = null;

    async function loadRoleRecordPreview() {
      try {
        const response = await fetch(previewUrl, {
          cache: 'no-store',
          headers: getAuthHeaders(),
        });
        if (!response.ok) {
          throw new Error('Unable to load record preview.');
        }
        const blob = await response.blob();
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) {
          setPreviewObjectUrl(objectUrl);
        }
      } catch {
        if (!cancelled) {
          setPreviewObjectUrl(null);
        }
      }
    }

    setPreviewObjectUrl(null);
    void loadRoleRecordPreview();

    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [isRoleRecordSource, previewUrl]);

  async function loadImport(
    currentImportId: string,
    options: { silent?: boolean } = {},
  ) {
    if (!options.silent) {
      setIsLoading(true);
      setErrorMessage(null);
    }
    try {
      const response = await fetch(
        isStaffSource
          ? `${API_BASE_URL}/api/staff/records/${currentImportId}/import`
          : isManagerSource
            ? `${API_BASE_URL}/api/manager/records/${currentImportId}/import`
            : `${API_BASE_URL}/api/imports/${currentImportId}`,
        {
          cache: 'no-store',
          headers: isRoleRecordSource ? getAuthHeaders() : undefined,
        },
      );
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(payload?.detail || 'Unable to load this TR record.');
      }

      const data = (await response.json()) as ImportRecord;
      setRecord(data);
      setActivePageNumber(data.pages[0]?.page_number ?? 1);
      setEditingFieldKey(null);
      setEditingValue('');
      setFieldOverrides({});
      setConfirmedFieldKeys({});
      setStaffActionMessage(null);
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Unable to load this TR record.',
      );
    } finally {
      if (!options.silent) {
        setIsLoading(false);
      }
    }
  }

  function getFieldValue(key: TrFieldKey): string | null {
    if (typeof fieldOverrides[key] === 'string') {
      return fieldOverrides[key] || null;
    }
    if (key === 'deceasedDate') {
      const fieldValue = record?.review_data?.fields?.deceasedDate?.value;
      if (typeof fieldValue === 'string' && fieldValue.trim()) {
        return fieldValue.trim();
      }
      return getDeceasedDate(record);
    }
    const reviewField = record?.review_data?.fields?.[key] ?? null;
    const value = reviewField?.value;
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
    return getFirstReviewFieldAlternativeValue(reviewField);
  }

  function startEditField(key: TrFieldKey) {
    setEditingFieldKey(key);
    setEditingValue(getFieldValue(key) || '');
  }

  function saveEditField() {
    if (!editingFieldKey) {
      return;
    }
    const normalized = normalizeTrFieldEditValue(editingFieldKey, editingValue);
    setFieldOverrides((previous) => ({
      ...previous,
      [editingFieldKey]: normalized,
    }));
    setConfirmedFieldKeys((previous) => ({
      ...previous,
      [editingFieldKey]: true,
    }));
    setEditingFieldKey(null);
    setEditingValue('');
  }

  async function completeReviewRecord() {
    if (!record || !canReviewRecord || isCompleted) {
      return;
    }
    if (unresolvedReviewFieldKeys.length > 0) {
      setErrorMessage(
        `ยังมีช่องที่ต้องตรวจ/ยืนยัน ${unresolvedReviewFieldKeys.length} ช่อง: ${formatTrFieldList(
          unresolvedReviewFieldKeys,
        )}`,
      );
      return;
    }

    setIsCompletingStaffRecord(true);
    setErrorMessage(null);
    setStaffActionMessage(null);
    try {
      const fields = Object.fromEntries(
        reviewFields.map((field) => {
          const sourceField = record.review_data?.fields?.[field.key] ?? null;
          const staffVerified =
            typeof fieldOverrides[field.key] === 'string' ||
            confirmedFieldKeys[field.key];
          return [
            field.key,
            {
              value: getFieldValue(field.key),
              pageNumber: sourceField?.pageNumber ?? null,
              bbox: sourceField?.bbox ?? null,
              source: staffVerified
                ? 'staff_verified'
                : (sourceField?.source ?? 'staff_verified'),
              reviewStatus: staffVerified
                ? 'staff_verified'
                : (sourceField?.reviewStatus ?? null),
              reviewNote: sourceField?.reviewNote ?? null,
              appliedCorrections: sourceField?.appliedCorrections ?? [],
            },
          ];
        }),
      );
      const tableNameField = record.review_data?.fields?.tableName ?? null;
      const response = await fetch(
        `${API_BASE_URL}/api/${isManagerSource ? 'manager' : 'staff'}/records/${record.id}/complete`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...getAuthHeaders(),
          },
          body: JSON.stringify({
            corrected_result: {
              documentType: 'tr',
              targetTable: 'dbo.TT_UpTTR',
              verifiedAt: new Date().toISOString(),
              flags: {
                ...(record.review_data?.flags ?? {}),
                deceased: isDeceased,
                deceasedDate,
              },
              fields: {
                tableName: {
                  value: 'TT_UpTTR',
                  pageNumber: tableNameField?.pageNumber ?? null,
                  bbox: tableNameField?.bbox ?? null,
                  source: tableNameField?.source ?? 'constant',
                },
                ...fields,
              },
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
      setRecord((previous) =>
        previous
          ? {
              ...previous,
              status: 'checked',
              review_status: 'completed',
              save_btn: 'Y',
              checked_at: new Date().toISOString(),
            }
          : previous,
      );
      router.push(
        isStaffSource ? '/staff' : isManagerSource ? '/manager' : '/',
      );
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

  return (
    <Box as="main" maxW="1920px" mx="auto" px={{ base: 2, md: 2 }} py={2}>
      <Grid gap={3} templateColumns={{ base: '1fr', xl: '1.03fr 1fr' }}>
        <Box
          {...panelStyles}
          p={{ base: 2, md: 3 }}
          display="flex"
          flexDirection="column"
          gap={2}
          h={{ xl: 'calc(100vh - 16px)' }}
          overflow={{ xl: 'auto' }}
          position={{ xl: 'sticky' }}
          top={{ xl: '8px' }}
        >
          <Flex justify="space-between" align="center" gap={2} wrap="wrap">
            <Link
              href={
                isStaffSource ? '/staff' : isManagerSource ? '/manager' : '/'
              }
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
                minH="38px"
                px={3}
                fontSize="0.92rem"
              >
                กลับหน้าแรก
              </Box>
            </Link>

            {record ? (
              <Text color="#6a5a45" fontWeight="700">
                {record.source_filename}
              </Text>
            ) : null}
          </Flex>

          <Box
            borderWidth="1px"
            borderColor="rgba(73, 59, 36, 0.08)"
            borderRadius="16px"
            bg="white"
            minH={{ base: '58vh', xl: 'calc(100vh - 118px)' }}
            maxH={{ xl: 'calc(100vh - 118px)' }}
            overflow="auto"
            p={1.5}
            ref={previewScrollRef}
          >
            {displayPreviewUrl ? (
              <Box position="relative">
                <Image
                  alt={`TR original page ${activePageNumber}`}
                  src={displayPreviewUrl}
                  w="100%"
                  display="block"
                  borderRadius="10px"
                />
              </Box>
            ) : (
              <Text color="#6a5a45">ยังไม่มี preview ให้แสดง</Text>
            )}
          </Box>
        </Box>

        <Box
          {...panelStyles}
          p={{ base: 2, md: 3 }}
          h={{ xl: 'calc(100vh - 16px)' }}
          overflow={{ xl: 'auto' }}
        >
          {/* <Text
            alignSelf="start"
            bg="rgba(15, 118, 110, 0.08)"
            color="#115e59"
            borderRadius="full"
            display="inline-flex"
            px={3}
            py={1}
            fontSize="0.84rem"
            fontWeight="700"
            mb={4}
          >
            ผลลัพธ์ TR
          </Text> */}

          <Flex
            justify="space-between"
            align="center"
            gap={2}
            wrap="wrap"
            mb={2}
          >
            <Text
              alignSelf="start"
              bg="rgba(15, 118, 110, 0.08)"
              color="#115e59"
              borderRadius="full"
              display="inline-flex"
              px={3}
              py={0.5}
              fontSize="0.8rem"
              fontWeight="700"
            >
              ผลลัพธ์ TR
            </Text>

            <Flex align="center" justify="end" gap={2} wrap="wrap">
              {isDeceased ? (
                <Box
                  borderWidth="1px"
                  borderColor="rgba(185, 28, 28, 0.22)"
                  borderRadius="12px"
                  bg="rgba(254, 242, 242, 0.95)"
                  color="#991b1b"
                  fontWeight="800"
                  minH="38px"
                  px={4}
                  display="inline-flex"
                  alignItems="center"
                  gap={2}
                >
                  <Text as="span">เสียชีวิต</Text>
                  {deceasedDate ? (
                    <Text as="span" fontSize="0.8rem" fontWeight="700">
                      {deceasedDate}
                    </Text>
                  ) : null}
                </Box>
              ) : null}

              {canReviewRecord ? (
                <Box
                  as="button"
                  onClick={() => {
                    if (isCompletingStaffRecord || isCompleted) {
                      return;
                    }
                    void completeReviewRecord();
                  }}
                  aria-disabled={isCompleteDisabled}
                  borderWidth="1px"
                  borderColor={
                    isCompleted ? 'rgba(22, 101, 52, 0.22)' : 'transparent'
                  }
                  borderRadius="12px"
                  bg={isCompleted ? 'rgba(220, 252, 231, 0.95)' : '#0f766e'}
                  color={isCompleted ? '#166534' : 'white'}
                  fontWeight="800"
                  minH="38px"
                  px={4}
                  fontSize="0.9rem"
                  display="inline-flex"
                  alignItems="center"
                  justifyContent="center"
                  gap={2}
                  opacity={isCompletingStaffRecord ? 0.7 : 1}
                  cursor={isCompleteDisabled ? 'not-allowed' : 'pointer'}
                >
                  {isCompletingStaffRecord ? (
                    <Spinner color="currentColor" size="xs" />
                  ) : null}
                  <Text as="span">
                    {isCompleted
                      ? 'ตรวจสอบแล้ว'
                      : isCompletingStaffRecord
                        ? 'กำลังบันทึก...'
                        : isCompleteBlockedByReview
                          ? `ต้องยืนยัน ${unresolvedReviewFieldKeys.length} ช่อง`
                        : 'ยืนยันว่าตรวจครบแล้ว'}
                  </Text>
                </Box>
              ) : null}
            </Flex>
          </Flex>

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
              borderColor="rgba(22, 101, 52, 0.2)"
              borderRadius="18px"
              bg="rgba(240, 253, 244, 0.95)"
              p={3}
              mb={2}
            >
              <Text color="#166534" fontWeight="700">
                {staffActionMessage}
              </Text>
            </Box>
          ) : null}

          {record && reviewBlockingFieldKeys.length > 0 ? (
            <Box
              borderWidth="1px"
              borderColor={
                isCompleteBlockedByReview
                  ? 'rgba(185, 28, 28, 0.24)'
                  : 'rgba(22, 101, 52, 0.2)'
              }
              borderRadius="14px"
              bg={
                isCompleteBlockedByReview
                  ? 'rgba(254, 242, 242, 0.88)'
                  : 'rgba(240, 253, 244, 0.9)'
              }
              p={3}
              mb={2}
            >
              <Text
                color={isCompleteBlockedByReview ? '#991b1b' : '#166534'}
                fontSize="0.78rem"
                fontWeight="800"
              >
                {isCompleteBlockedByReview
                  ? `ต้องตรวจ/ยืนยันอีก ${unresolvedReviewFieldKeys.length} ช่องก่อนบันทึก`
                  : 'ช่องที่ระบบเตือนถูกยืนยันครบแล้ว'}
              </Text>
              {isCompleteBlockedByReview ? (
                <Text color="#7f1d1d" fontSize="0.68rem" mt={1}>
                  {formatTrFieldList(unresolvedReviewFieldKeys)}
                </Text>
              ) : null}
            </Box>
          ) : null}

          {selectedPage?.ocr_current_stage ? (
            <ProcessingLiveStatusPanel
              stage={selectedPage.ocr_current_stage}
            />
          ) : null}

          {selectedPage?.processing_timing ? (
            <ProcessingTimingPanel
              pageNumber={selectedPage.page_number}
              timing={selectedPage.processing_timing}
            />
          ) : null}

          {record ? (
            <Box
              // borderWidth="2px"
              // borderColor="#1f1a14"
              bg="white"
              p={{ base: 2, md: 2 }}
              minH="auto"
            >
              <Grid
                templateColumns={{
                  base: '1fr',
                  md: 'repeat(6, minmax(0, 1fr))',
                }}
                gap={2}
              >
                {reviewFields.map((field) => (
                  <TrFieldCard
                    key={field.key}
                    editing={editingFieldKey === field.key}
                    editingValue={editingValue}
                    field={field}
                    reviewField={
                      record.review_data?.fields?.[field.key] ?? null
                    }
                    confirmed={Boolean(confirmedFieldKeys[field.key])}
                    value={getFieldValue(field.key)}
                    issueMessages={(
                      validationIssuesByField[field.key] ?? []
                    ).map(formatTrValidationIssue)}
                    readOnly={isCompleted || !canReviewRecord}
                    requiresReview={reviewBlockingFieldKeys.includes(
                      field.key,
                    )}
                    onCancel={() => {
                      setEditingFieldKey(null);
                      setEditingValue('');
                    }}
                    onChange={setEditingValue}
                    onEdit={() => startEditField(field.key)}
                    onSave={saveEditField}
                    onConfirm={() => {
                      setConfirmedFieldKeys((previous) => ({
                        ...previous,
                        [field.key]: true,
                      }));
                    }}
                    onSelectValue={(selectedValue) => {
                      setFieldOverrides((previous) => ({
                        ...previous,
                        [field.key]: normalizeTrFieldEditValue(
                          field.key,
                          selectedValue,
                        ),
                      }));
                      setConfirmedFieldKeys((previous) => ({
                        ...previous,
                        [field.key]: true,
                      }));
                    }}
                  />
                ))}
              </Grid>
            </Box>
          ) : null}
        </Box>
      </Grid>
    </Box>
  );
}

function ProcessingLiveStatusPanel({
  stage,
}: {
  stage: ImportPageAsset['ocr_current_stage'];
}) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const intervalId = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(intervalId);
  }, []);

  if (!stage?.key) {
    return null;
  }

  const stageLabel = formatProcessingStage(stage.key);
  const fieldLabel = stage.field ? formatLiveField(stage.field) : null;
  const elapsed = formatLiveElapsed(stage.started_at, now);

  return (
    <Box
      borderWidth="1px"
      borderColor="rgba(234, 88, 12, 0.28)"
      borderRadius="12px"
      bg="rgba(255, 247, 237, 0.9)"
      p={3}
      mb={2}
    >
      <Flex align="center" gap={2} mb={1}>
        <Spinner color="#c2410c" size="xs" />
        <Text color="#9a3412" fontSize="0.78rem" fontWeight="800">
          กำลังประมวลผล
        </Text>
      </Flex>
      <Text color="#431407" fontSize="0.88rem" fontWeight="800">
        {stageLabel}{fieldLabel ? ` / ${fieldLabel}` : ''}
      </Text>
      <Text color="#9a3412" fontSize="0.68rem" mt={1}>
        เริ่มเมื่อ {formatLiveStartedAt(stage.started_at)}
        {elapsed ? ` · ผ่านไป ${elapsed}` : ''}
      </Text>
    </Box>
  );
}

function ProcessingTimingPanel({
  pageNumber,
  timing,
}: {
  pageNumber: number;
  timing: ImportPageAsset['processing_timing'];
}) {
  const stages = Object.entries(timing?.stages_ms ?? {}).filter(
    ([, duration]) => typeof duration === 'number' && Number.isFinite(duration),
  );
  const total = timing?.total_before_persistence_ms;

  if (stages.length === 0 && typeof total !== 'number') {
    return null;
  }

  return (
    <Box
      borderWidth="1px"
      borderColor="rgba(37, 99, 235, 0.2)"
      borderRadius="12px"
      bg="rgba(239, 246, 255, 0.72)"
      p={3}
      mb={2}
    >
      <Flex align="center" justify="space-between" gap={2} wrap="wrap" mb={2}>
        <Text color="#1d4ed8" fontSize="0.78rem" fontWeight="800">
          เวลาประมวลผลหน้า {pageNumber}
        </Text>
        {typeof total === 'number' ? (
          <Text color="#1e3a8a" fontSize="0.78rem" fontWeight="800">
            รวม {formatProcessingDuration(total)}
          </Text>
        ) : null}
      </Flex>
      <Grid templateColumns={{ base: 'repeat(2, minmax(0, 1fr))', md: 'repeat(3, minmax(0, 1fr))' }} gap={1.5}>
        {stages.map(([stage, duration]) => (
          <Box
            key={stage}
            borderRadius="8px"
            bg="whiteAlpha.800"
            px={2}
            py={1.5}
          >
            <Text color="#475569" fontSize="0.6rem" fontWeight="700" lineHeight="1.1">
              {formatProcessingStage(stage)}
            </Text>
            <Text color="#0f172a" fontSize="0.78rem" fontWeight="800">
              {formatProcessingDuration(duration)}
            </Text>
          </Box>
        ))}
      </Grid>
      <Text mt={2} color="#64748b" fontSize="0.6rem" lineHeight="1.25">
        เวลารวมยังไม่รวมการเขียนไฟล์ผลลัพธ์และบันทึกฐานข้อมูล
      </Text>
    </Box>
  );
}

function formatProcessingStage(stage: string) {
  const labels: Record<string, string> = {
    image_preparation: 'เตรียมภาพ',
    watermark_detection: 'ตรวจลายน้ำ',
    watermark_cleanup: 'ลบลายน้ำ',
    primary_ocr: 'OCR หลัก',
    parser: 'Parser',
    local_field_analysis: 'วิเคราะห์ช่อง',
    crop_ocr_rescue: 'Crop OCR',
    vision_field_rescue: 'Vision',
    local_validation: 'ตรวจข้อมูล',
    vision_age_rescue: 'Vision อายุ',
    field_decision_summary: 'สรุปผลช่อง',
  };
  return labels[stage] ?? stage;
}

function formatLiveField(field: string) {
  const labels: Record<string, string> = {
    personId: 'เลขบัตรประชาชน',
    houseCode: 'รหัสบ้าน',
    personName: 'ชื่อ',
    motherName: 'ชื่อมารดา',
    motherId: 'เลขบัตรมารดา',
    motherNationality: 'สัญชาติมารดา',
    fatherName: 'ชื่อบิดา',
    fatherId: 'เลขบัตรบิดา',
    fatherNationality: 'สัญชาติบิดา',
    address: 'ที่อยู่',
    age: 'อายุ',
    duplicate_parent_id: 'ตรวจเลขบัตรบิดา/มารดาซ้ำ',
  };
  return labels[field] ?? field;
}

function formatLiveStartedAt(value: string | undefined) {
  if (!value) {
    return 'ไม่ทราบเวลา';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return 'ไม่ทราบเวลา';
  }
  return new Intl.DateTimeFormat('th-TH', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date);
}

function formatLiveElapsed(value: string | undefined, now: number) {
  const startedAt = value ? Date.parse(value) : Number.NaN;
  if (Number.isNaN(startedAt)) {
    return null;
  }
  const elapsedSeconds = Math.max(0, Math.floor((now - startedAt) / 1_000));
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return minutes > 0 ? `${minutes} นาที ${seconds} วินาที` : `${seconds} วินาที`;
}

function formatProcessingDuration(durationMs: number) {
  const totalSeconds = Math.round(durationMs / 1000);
  if (totalSeconds >= 60) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return seconds > 0 ? `${minutes} นาที ${seconds} วินาที` : `${minutes} นาที`;
  }
  if (durationMs >= 1000) {
    return `${(durationMs / 1000).toFixed(durationMs >= 10_000 ? 0 : 1)} วินาที`;
  }
  return `${Math.round(durationMs)} ms`;
}

function TrFieldCard({
  confirmed,
  editing,
  editingValue,
  field,
  issueMessages,
  reviewField,
  requiresReview,
  value,
  onCancel,
  onChange,
  onConfirm,
  onEdit,
  onSave,
  onSelectValue,
  readOnly,
}: {
  confirmed?: boolean;
  editing: boolean;
  editingValue: string;
  field: TrFieldConfig;
  issueMessages: string[];
  reviewField: ReviewField | null;
  requiresReview?: boolean;
  value: string | null;
  onCancel: () => void;
  onChange: (value: string) => void;
  onConfirm: () => void;
  onEdit: () => void;
  onSave: () => void;
  onSelectValue: (value: string) => void;
  readOnly?: boolean;
}) {
  const statusTone = getFieldStatusTone(reviewField, value);
  const valueOptions = getFieldValueOptions(reviewField, value);
  return (
    <Box
      gridColumn={field.gridColumn}
      borderWidth="1px"
      borderColor={statusTone.borderColor}
      bg={statusTone.bg}
      borderRadius="8px"
      minH="54px"
      p={2}
    >
      <Flex justify="space-between" align="start" gap={2}>
        <Text
          color={field.tone === 'title' ? '#ec4899' : '#6a5a45'}
          fontSize="0.72rem"
          lineHeight="1.15"
        >
          {field.label}
        </Text>
        {field.tone !== 'title' && !readOnly ? (
          <Box
            as="button"
            onClick={(event) => {
              event.stopPropagation();
              onEdit();
            }}
            color="#115e59"
            borderWidth="1px"
            borderColor="rgba(15, 118, 110, 0.18)"
            borderRadius="7px"
            bg="rgba(15, 118, 110, 0.06)"
            h="24px"
            minW="24px"
            px={1}
            fontWeight="700"
            fontSize="0.64rem"
          >
            <MdEdit />
          </Box>
        ) : null}
      </Flex>
      {statusTone.label ? (
        <Text
          mt={1}
          color={statusTone.color}
          fontSize="0.62rem"
          fontWeight="700"
          lineHeight="1.15"
        >
          {statusTone.label}
        </Text>
      ) : null}
      {issueMessages.map((message, index) => (
        <Text
          key={`${message}-${index}`}
          mt={1}
          color="#991b1b"
          fontSize="0.62rem"
          fontWeight="700"
          lineHeight="1.15"
        >
          {message}
        </Text>
      ))}

      {editing ? (
        <Stack mt={1.5} gap={1.5}>
          <input
            value={editingValue}
            onChange={(event) => onChange(event.target.value)}
            onClick={(event) => event.stopPropagation()}
            style={{
              background: 'white',
              border: '1px solid rgba(73, 59, 36, 0.18)',
              borderRadius: '8px',
              height: 28,
              outline: 'none',
              padding: '0 10px',
              width: '100%',
            }}
          />
          <Flex gap={1.5}>
            <Box
              as="button"
              onClick={(event) => {
                event.stopPropagation();
                onSave();
              }}
              borderRadius="9px"
              borderWidth="1px"
              borderColor="rgba(194, 65, 12, 0.28)"
              bg="rgba(255, 237, 213, 0.92)"
              color="#9a3412"
              fontWeight="700"
              fontSize="0.72rem"
              px={2.5}
              py={0.5}
            >
              Update
            </Box>
            <Box
              as="button"
              onClick={(event) => {
                event.stopPropagation();
                onCancel();
              }}
              borderRadius="9px"
              borderWidth="1px"
              borderColor="rgba(73, 59, 36, 0.18)"
              bg="white"
              color="#6a5a45"
              fontWeight="700"
              fontSize="0.72rem"
              px={2.5}
              py={0.5}
            >
              Cancel
            </Box>
          </Flex>
        </Stack>
      ) : (
        <Text
          mt={0.5}
          color="#241d17"
          fontSize="0.86rem"
          fontWeight="700"
          lineHeight="1.25"
          wordBreak="break-word"
        >
          {value || '-'}
        </Text>
      )}
      {requiresReview && !readOnly ? (
        <Box
          as="button"
          onClick={(event) => {
            event.stopPropagation();
            onConfirm();
          }}
          borderRadius="9px"
          borderWidth="1px"
          borderColor={
            confirmed ? 'rgba(22, 101, 52, 0.24)' : 'rgba(185, 28, 28, 0.28)'
          }
          bg={confirmed ? 'rgba(240, 253, 244, 0.94)' : 'rgba(254, 242, 242, 0.94)'}
          color={confirmed ? '#166534' : '#991b1b'}
          fontWeight="800"
          fontSize="0.68rem"
          px={2.5}
          py={1}
          mt={1.5}
        >
          {confirmed ? 'ยืนยันช่องนี้แล้ว' : 'ยืนยันว่าตรวจช่องนี้แล้ว'}
        </Box>
      ) : null}
      {valueOptions.length > 1 ? (
        <Stack mt={1.5} gap={1}>
          <Text color="#92400e" fontSize="0.62rem" fontWeight="800">
            เลือกค่า
          </Text>
          {valueOptions.map((option) => (
            <Box
              key={`${option.value}-${option.sources.join('|')}`}
              as="label"
              borderWidth="1px"
              borderColor={
                option.selected
                  ? 'rgba(15, 118, 110, 0.36)'
                  : 'rgba(245, 158, 11, 0.32)'
              }
              borderRadius="7px"
              bg={
                option.selected
                  ? 'rgba(240, 253, 250, 0.96)'
                  : 'rgba(255, 251, 235, 0.92)'
              }
              cursor={readOnly ? 'default' : 'pointer'}
              display="grid"
              gridTemplateColumns="16px minmax(0, 1fr)"
              gap={1.5}
              alignItems="start"
              px={1.5}
              py={1}
            >
              <input
                type="radio"
                name={`tr-field-${field.key}`}
                checked={option.selected}
                disabled={readOnly}
                onChange={() => onSelectValue(option.value)}
                onClick={(event) => event.stopPropagation()}
                style={{
                  accentColor: '#0f766e',
                  marginTop: 2,
                }}
              />
              <Box minW={0}>
                <Text
                  color={option.selected ? '#115e59' : '#78350f'}
                  fontSize="0.76rem"
                  fontWeight="800"
                  lineHeight="1.15"
                >
                  {option.value}
                </Text>
                {formatOptionMeta(option) ? (
                  <Text
                    color={option.selected ? '#0f766e' : '#92400e'}
                    fontSize="0.58rem"
                    fontWeight="700"
                    lineHeight="1.1"
                  >
                    {formatOptionMeta(option)}
                  </Text>
                ) : null}
              </Box>
            </Box>
          ))}
        </Stack>
      ) : null}
    </Box>
  );
}

type FieldValueOption = {
  value: string;
  sources: string[];
  score: number | null;
  selected: boolean;
};

function groupTrValidationIssuesByField(
  issues: ImportRecord['field_validation_issues'] | undefined,
) {
  const grouped: Partial<Record<TrFieldKey, TrValidationIssue[]>> = {};
  for (const issue of Array.isArray(issues) ? issues : []) {
    const field = String(issue?.field || '') as TrFieldKey;
    if (!ALL_TR_FIELD_KEYS.has(field)) {
      continue;
    }
    grouped[field] = [...(grouped[field] ?? []), issue];
  }
  return grouped;
}

function getFirstReviewFieldAlternativeValue(reviewField: ReviewField | null) {
  const alternatives = Array.isArray(reviewField?.alternatives)
    ? reviewField.alternatives
    : [];
  for (const alternative of alternatives) {
    const value =
      typeof alternative.value === 'string' ? alternative.value.trim() : '';
    if (value) {
      return value;
    }
  }
  return null;
}

function fieldRequiresHumanReview(
  reviewField: ReviewField | null,
  issues: TrValidationIssue[],
) {
  const status = String(reviewField?.reviewStatus || '').trim();
  const alternatives = Array.isArray(reviewField?.alternatives)
    ? reviewField.alternatives
    : [];
  return status === 'needs_review' || alternatives.length > 0 || issues.length > 0;
}

function formatTrFieldList(keys: TrFieldKey[]) {
  const labels = new Map<TrFieldKey, string>(
    [...TR_FIELDS, TR_DECEASED_DATE_FIELD].map((field) => [
      field.key,
      field.label,
    ]),
  );
  return keys.map((key) => labels.get(key) || key).join(', ');
}

function formatTrValidationIssue(issue: TrValidationIssue) {
  const message = String(issue?.message || '').trim();
  if (message) {
    return message;
  }
  const issueType = String(issue?.issue || '').trim();
  const labels: Record<string, string> = {
    missing_or_invalid: 'ข้อมูลหายหรือรูปแบบไม่ถูกต้อง',
    needs_review: 'ระบบระบุว่าควรตรวจช่องนี้',
    parent_id_missing: 'มีชื่อพ่อ/แม่ แต่ไม่มีเลข ID',
    suspicious_id: 'เลข ID ดูผิดปกติ โปรดตรวจภาพ',
    duplicate_id: 'เลข ID ซ้ำกับช่องอื่น',
    duplicate_parent_name: 'ชื่อพ่อและแม่ซ้ำกัน',
    short_parent_name: 'ชื่อพ่อ/แม่สั้นมาก โปรดตรวจภาพ',
  };
  return labels[issueType] || issueType || 'ควรตรวจช่องนี้';
}

function getFieldValueOptions(
  reviewField: ReviewField | null,
  value: string | null,
): FieldValueOption[] {
  const alternatives = Array.isArray(reviewField?.alternatives)
    ? reviewField.alternatives
    : [];
  const selectedValue = compactTextForCompare(value);
  const seen = new Set<string>();
  const options: FieldValueOption[] = [];

  function addOption(
    optionValue: string | null | undefined,
    sources: string[],
    score: number | null,
  ) {
    const trimmedValue =
      typeof optionValue === 'string' ? optionValue.trim() : '';
    const compactValue = compactTextForCompare(trimmedValue);
    if (!trimmedValue || !compactValue) {
      return;
    }

    const normalizedSources = sources.filter(
      (source) => typeof source === 'string' && source.trim().length > 0,
    );
    if (seen.has(compactValue)) {
      const existing = options.find(
        (option) => compactTextForCompare(option.value) === compactValue,
      );
      if (existing) {
        existing.sources = Array.from(
          new Set([...existing.sources, ...normalizedSources]),
        );
        existing.score = maxNullableScore(existing.score, score);
      }
      return;
    }

    seen.add(compactValue);
    options.push({
      value: trimmedValue,
      sources: normalizedSources,
      score,
      selected: compactValue === selectedValue,
    });
  }

  addOption(
    reviewField?.value,
    typeof reviewField?.source === 'string' && reviewField.source.trim()
      ? [reviewField.source.trim()]
      : [],
    getCandidateScore(reviewField),
  );

  for (const alternative of alternatives) {
    addOption(
      alternative.value,
      Array.isArray(alternative.sources)
        ? alternative.sources
        : typeof alternative.source === 'string' && alternative.source.trim()
          ? [alternative.source.trim()]
          : [],
      getCandidateScore(alternative),
    );
  }

  addOption(value, [], null);

  if (selectedValue) {
    for (const option of options) {
      option.selected = compactTextForCompare(option.value) === selectedValue;
    }
  }

  return options;
}

function getHighestScoredFieldOverrides(record: ImportRecord) {
  const fields = record.review_data?.fields;
  const overrides: Partial<Record<TrFieldKey, string>> = {};
  if (!fields) {
    return overrides;
  }

  for (const [fieldKey, reviewField] of Object.entries(fields)) {
    if (!ALL_TR_FIELD_KEYS.has(fieldKey as TrFieldKey) || !reviewField) {
      continue;
    }
    const key = fieldKey as TrFieldKey;
    const currentValue =
      typeof reviewField.value === 'string' ? reviewField.value.trim() : '';
    let bestValue = currentValue;
    let bestScore = getCandidateScore(reviewField);

    const alternatives = Array.isArray(reviewField.alternatives)
      ? reviewField.alternatives
      : [];
    for (const alternative of alternatives) {
      const alternativeValue =
        typeof alternative.value === 'string' ? alternative.value.trim() : '';
      const alternativeScore = getCandidateScore(alternative);
      if (!alternativeValue || alternativeScore === null) {
        continue;
      }
      if (bestScore === null || alternativeScore > bestScore) {
        bestValue = alternativeValue;
        bestScore = alternativeScore;
      }
    }

    if (
      bestScore !== null &&
      bestValue &&
      compactTextForCompare(bestValue) !== compactTextForCompare(currentValue)
    ) {
      overrides[key] = normalizeTrFieldEditValue(key, bestValue);
    }
  }

  return overrides;
}

function getCandidateScore(candidate: {
  score?: number | null;
  confidence?: number | null;
} | null | undefined) {
  if (typeof candidate?.score === 'number' && Number.isFinite(candidate.score)) {
    return candidate.score;
  }
  if (
    typeof candidate?.confidence === 'number' &&
    Number.isFinite(candidate.confidence)
  ) {
    return candidate.confidence;
  }
  return null;
}

function maxNullableScore(left: number | null, right: number | null) {
  if (left === null) {
    return right;
  }
  if (right === null) {
    return left;
  }
  return Math.max(left, right);
}

function compactTextForCompare(value: string | null | undefined) {
  return String(value || '').replace(/\s+/g, '');
}

function formatAlternativeSources(sources: string[]) {
  const labels = sources
    .map((source) => {
      const normalized = source.toLowerCase();
      if (normalized.includes('crop_ocr')) {
        return 'crop OCR';
      }
      if (normalized.includes('vision')) {
        return 'Vision';
      }
      if (normalized.includes('parser')) {
        return 'parser';
      }
      if (normalized.includes('previous')) {
        return 'ค่าเดิม';
      }
      return '';
    })
    .filter(Boolean);
  return Array.from(new Set(labels)).join(', ');
}

function formatOptionMeta(option: FieldValueOption) {
  const parts = [];
  const sourceLabel = formatAlternativeSources(option.sources);
  if (sourceLabel) {
    parts.push(sourceLabel);
  }
  if (option.score !== null) {
    parts.push(`score ${formatScore(option.score)}`);
  }
  return parts.join(' · ');
}

function formatScore(score: number) {
  if (Number.isInteger(score)) {
    return String(score);
  }
  return score.toFixed(2);
}

function getNeedsReviewLabel(reviewField: ReviewField | null) {
  const note = String(reviewField?.reviewNote || '').trim();
  const alternatives = Array.isArray(reviewField?.alternatives)
    ? reviewField.alternatives
    : [];
  if (note.includes('สั้น')) {
    return 'ควรตรวจ: ชื่อสั้นมาก';
  }
  if (note.includes('ไม่ตรง') || alternatives.length > 0) {
    return 'ควรตรวจ: OCR อ่านไม่ตรงกัน';
  }
  if (note.includes('ติดข้อความ')) {
    return 'ควรตรวจ: อ่านติดข้อความข้างเคียง';
  }
  if (note.includes('ID') || note.includes('เลข')) {
    return 'ควรตรวจ: เลขอ้างอิงไม่ครบ';
  }
  if (note.includes('crop')) {
    return 'ควรตรวจ: crop อ่านไม่ชัด';
  }
  return note || 'ควรตรวจ';
}

function getFieldStatusTone(
  reviewField: ReviewField | null,
  value: string | null,
) {
  const status = String(reviewField?.reviewStatus || '').trim();
  const hasCorrections = Boolean(reviewField?.appliedCorrections?.length);
  if (status === 'needs_review') {
    return {
      bg: 'rgba(254, 242, 242, 0.94)',
      borderColor: 'rgba(220, 38, 38, 0.28)',
      color: '#991b1b',
      label: getNeedsReviewLabel(reviewField),
    };
  }
  if (status === 'rescued_by_crop' || status === 'rescued_by_crop_ocr') {
    return {
      bg: 'rgba(239, 246, 255, 0.94)',
      borderColor: 'rgba(37, 99, 235, 0.24)',
      color: '#1d4ed8',
      label:
        status === 'rescued_by_crop_ocr' ? 'crop OCR rescue' : 'crop rescue',
    };
  }
  if (status === 'corrected_by_vision') {
    return {
      bg: 'rgba(255, 247, 237, 0.96)',
      borderColor: 'rgba(234, 88, 12, 0.28)',
      color: '#9a3412',
      label: 'Vision correction',
    };
  }
  if (status === 'corrected_by_rule' || hasCorrections) {
    return {
      bg: 'rgba(250, 245, 255, 0.96)',
      borderColor: 'rgba(147, 51, 234, 0.24)',
      color: '#7e22ce',
      label: 'rule correction',
    };
  }
  if (!value && status === 'missing') {
    return {
      bg: 'rgba(248, 250, 252, 0.96)',
      borderColor: 'rgba(100, 116, 139, 0.18)',
      color: '#64748b',
      label: '',
    };
  }
  if (status === 'confirmed_by_vision') {
    return {
      bg: 'rgba(240, 253, 244, 0.94)',
      borderColor: 'rgba(22, 163, 74, 0.18)',
      color: '#166534',
      label: '',
    };
  }
  return {
    bg: 'rgba(251, 248, 242, 0.92)',
    borderColor: 'rgba(73, 59, 36, 0.08)',
    color: '#6a5a45',
    label: '',
  };
}

function normalizeTrFieldEditValue(key: TrFieldKey, value: string) {
  const trimmed = value.trim();
  if (PERSON_ID_FIELDS.has(key)) {
    return formatThaiPersonId(trimmed);
  }
  if (HOUSE_CODE_FIELDS.has(key)) {
    return formatThaiHouseCode(trimmed);
  }
  if (POSTAL_CODE_FIELDS.has(key)) {
    return compactDigits(trimmed).slice(0, 5);
  }
  return trimmed;
}

function thaiDigitsToAscii(value: string) {
  return value.replace(/[๐-๙]/g, (digit) =>
    String('๐๑๒๓๔๕๖๗๘๙'.indexOf(digit)),
  );
}

function compactDigits(value: string) {
  return thaiDigitsToAscii(value).replace(/\D/g, '');
}

function formatThaiPersonId(value: string) {
  if (value.trim() === '-') {
    return '-';
  }
  const digits = compactDigits(value);
  if (digits.length !== 13) {
    return value.trim();
  }
  return `${digits.slice(0, 1)}-${digits.slice(1, 5)}-${digits.slice(5, 10)}-${digits.slice(10, 12)}-${digits.slice(12)}`;
}

function formatThaiHouseCode(value: string) {
  if (value.trim() === '-') {
    return '-';
  }
  const digits = compactDigits(value);
  if (digits.length !== 11) {
    return value.trim();
  }
  return `${digits.slice(0, 4)}-${digits.slice(4, 10)}-${digits.slice(10)}`;
}

function hasDeceasedMarker(record: ImportRecord | null): boolean {
  if (!record) {
    return false;
  }
  return record.review_data?.flags?.deceased === true;
}

function getDeceasedDate(record: ImportRecord | null): string | null {
  if (!record) {
    return null;
  }
  const flagDate = record.review_data?.flags?.deceasedDate;
  if (typeof flagDate === 'string' && flagDate.trim()) {
    return flagDate.trim();
  }
  return null;
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
      bg={active ? '#0f766e' : 'rgba(255, 255, 255, 0.96)'}
      color={active ? 'white' : '#1f1a14'}
      fontWeight="700"
      minH="48px"
      px={6}
    >
      {children}
    </Box>
  );
}
