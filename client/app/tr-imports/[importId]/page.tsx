'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Flex, Grid, Image, Spinner, Stack, Text } from '@chakra-ui/react';

import {
  API_BASE_URL,
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
  | 'moveInDate'
  | 'remark'
  | 'updateDate'
>;

type TrFieldConfig = {
  key: TrFieldKey;
  label: string;
  tone?: 'wide' | 'title';
};

type HighlightTarget = {
  pageNumber: number;
  bbox: [number, number, number, number];
};

const TR_FIELDS: TrFieldConfig[] = [
  { key: 'tableName', label: 'table name', tone: 'title' },
  { key: 'personId', label: 'ID' },
  { key: 'houseCode', label: 'รหัสบ้าน' },
  { key: 'personName', label: 'ชื่อ' },
  { key: 'gender', label: 'เพศ' },
  { key: 'nationality', label: 'สัญชาติ' },
  { key: 'birthDate', label: 'วันเกิด' },
  { key: 'age', label: 'อายุ' },
  { key: 'status', label: 'สถานภาพที่อยู่' },
  { key: 'motherName', label: 'มารดา' },
  { key: 'motherId', label: 'ID มารดา' },
  { key: 'motherNationality', label: 'สัญชาติ มารดา' },
  { key: 'fatherName', label: 'บิดา' },
  { key: 'fatherId', label: 'ID บิดา' },
  { key: 'fatherNationality', label: 'สัญชาติ บิดา' },
  { key: 'address', label: 'ที่อยู่', tone: 'wide' },
  { key: 'moveInDate', label: 'เข้ามาอยู่วันที่' },
  { key: 'remark', label: 'Remark', tone: 'wide' },
  { key: 'updateDate', label: 'Update Date' },
];

const panelStyles = {
  borderWidth: '1px',
  borderColor: 'rgba(73, 59, 36, 0.12)',
  borderRadius: '28px',
  bg: 'rgba(255, 255, 255, 0.92)',
  boxShadow: '0 18px 42px rgba(31, 26, 20, 0.08)',
};

export default function TrImportInspectPage() {
  const params = useParams<{ importId: string }>();
  const importId = Array.isArray(params?.importId)
    ? params.importId[0]
    : params?.importId;

  const [record, setRecord] = useState<ImportRecord | null>(null);
  const [activePageNumber, setActivePageNumber] = useState(1);
  const [activeFieldKey, setActiveFieldKey] = useState<TrFieldKey | null>(null);
  const [editingFieldKey, setEditingFieldKey] = useState<TrFieldKey | null>(null);
  const [editingValue, setEditingValue] = useState('');
  const [fieldOverrides, setFieldOverrides] = useState<Partial<Record<TrFieldKey, string>>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const previewScrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (importId) {
      void loadImport(importId);
    }
  }, [importId]);

  const selectedPage =
    record?.pages.find((page) => page.page_number === activePageNumber) ?? null;
  const previewUrl =
    record && selectedPage
      ? `${API_BASE_URL}/api/imports/${record.id}/pages/${selectedPage.page_number}/original`
      : '';
  const watermarkBadge = getWatermarkBadge(selectedPage);

  const activeHighlight = useMemo(
    () => (activeFieldKey ? getFieldHighlight(record, activeFieldKey) : null),
    [activeFieldKey, record],
  );

  async function loadImport(currentImportId: string) {
    setIsLoading(true);
    setErrorMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/imports/${currentImportId}`, {
        cache: 'no-store',
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(payload?.detail || 'Unable to load this TR record.');
      }

      const data = (await response.json()) as ImportRecord;
      setRecord(data);
      setActivePageNumber(data.pages[0]?.page_number ?? 1);
      setActiveFieldKey(null);
      setEditingFieldKey(null);
      setEditingValue('');
      setFieldOverrides({});
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'Unable to load this TR record.',
      );
    } finally {
      setIsLoading(false);
    }
  }

  function getFieldValue(key: TrFieldKey): string | null {
    if (typeof fieldOverrides[key] === 'string') {
      return fieldOverrides[key] || null;
    }
    const value = record?.review_data?.fields?.[key]?.value;
    return typeof value === 'string' && value.trim() ? value.trim() : null;
  }

  function activateField(key: TrFieldKey) {
    setActiveFieldKey(key);
    const highlight = getFieldHighlight(record, key);
    if (highlight) {
      setActivePageNumber(highlight.pageNumber);
      window.requestAnimationFrame(() => {
        scrollPreviewToHighlight(highlight);
      });
    }
  }

  function startEditField(key: TrFieldKey) {
    activateField(key);
    setEditingFieldKey(key);
    setEditingValue(getFieldValue(key) || '');
  }

  function saveEditField() {
    if (!editingFieldKey) {
      return;
    }
    const normalized = editingValue.trim();
    setFieldOverrides((previous) => ({
      ...previous,
      [editingFieldKey]: normalized,
    }));
    setEditingFieldKey(null);
    setEditingValue('');
  }

  function scrollPreviewToHighlight(highlight: HighlightTarget) {
    const container = previewScrollRef.current;
    if (!container) {
      return;
    }
    const imageHeight = container.scrollHeight;
    const targetTop =
      ((highlight.bbox[1] + highlight.bbox[3]) / 2) * imageHeight -
      container.clientHeight / 2;
    container.scrollTo({ top: Math.max(0, targetTop), behavior: 'smooth' });
  }

  return (
    <Box as="main" maxW="1880px" mx="auto" px={{ base: 2, md: 3, xl: 4 }} py={4}>
      <Grid gap={4} templateColumns={{ base: '1fr', xl: 'repeat(2, minmax(0, 1fr))' }}>
        <Box
          {...panelStyles}
          p={{ base: 3, md: 4 }}
          display="flex"
          flexDirection="column"
          gap={4}
          h={{ xl: 'calc(100vh - 32px)' }}
          overflow={{ xl: 'auto' }}
          position={{ xl: 'sticky' }}
          top={{ xl: '16px' }}
        >
          <Flex justify="space-between" align="center" gap={3} wrap="wrap">
            <Link href="/" style={{ textDecoration: 'none' }}>
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

          {record ? (
            <Stack gap={3}>
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

              {watermarkBadge ? (
                <Box
                  alignSelf="start"
                  borderWidth="1px"
                  borderColor={watermarkBadge.borderColor}
                  borderRadius="full"
                  bg={watermarkBadge.bg}
                  color={watermarkBadge.color}
                  px={4}
                  py={2}
                  fontSize="0.9rem"
                  fontWeight="700"
                >
                  {watermarkBadge.label}
                  {typeof selectedPage?.watermark_score === 'number' ? (
                    <Text as="span" color="inherit" fontWeight="600" ml={2} opacity={0.78}>
                      score {selectedPage.watermark_score.toFixed(3)}
                    </Text>
                  ) : null}
                </Box>
              ) : null}
            </Stack>
          ) : null}

          <Box
            borderWidth="1px"
            borderColor="rgba(73, 59, 36, 0.08)"
            borderRadius="22px"
            bg="white"
            minH={{ base: '58vh', xl: '78vh' }}
            maxH={{ xl: '78vh' }}
            overflow="auto"
            p={3}
            ref={previewScrollRef}
          >
            {previewUrl ? (
              <Box position="relative">
                <Image
                  alt={`TR original page ${activePageNumber}`}
                  src={previewUrl}
                  w="100%"
                  display="block"
                  borderRadius="14px"
                />
                {activeHighlight && activeHighlight.pageNumber === activePageNumber ? (
                  <Box
                    pointerEvents="none"
                    position="absolute"
                    border="2px solid rgba(255, 255, 255, 0.96)"
                    borderRadius="8px"
                    outline="3px solid rgba(231, 111, 45, 0.98)"
                    outlineOffset="1px"
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
          h={{ xl: 'calc(100vh - 32px)' }}
          overflow={{ xl: 'auto' }}
        >
          <Text
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
            ผลลัพธ์ ทร
          </Text>

          {isLoading ? (
            <Flex align="center" gap={3}>
              <Spinner color="#115e59" size="sm" />
              <Text color="#6a5a45">กำลังโหลดข้อมูล...</Text>
            </Flex>
          ) : null}

          {errorMessage ? (
            <Box borderWidth="1px" borderColor="rgba(185, 28, 28, 0.24)" borderRadius="18px" bg="rgba(254, 242, 242, 0.95)" p={4}>
              <Text color="#991b1b" fontWeight="700">
                {errorMessage}
              </Text>
            </Box>
          ) : null}

          {record ? (
            <Box
              borderWidth="2px"
              borderColor="#1f1a14"
              bg="white"
              p={{ base: 3, md: 5 }}
              minH="70vh"
            >
              <Grid templateColumns={{ base: '1fr', md: 'repeat(3, minmax(0, 1fr))' }} gap={3}>
                {TR_FIELDS.map((field) => (
                  <TrFieldCard
                    key={field.key}
                    active={activeFieldKey === field.key}
                    editing={editingFieldKey === field.key}
                    editingValue={editingValue}
                    field={field}
                    value={getFieldValue(field.key)}
                    onActivate={() => activateField(field.key)}
                    onCancel={() => {
                      setEditingFieldKey(null);
                      setEditingValue('');
                    }}
                    onChange={setEditingValue}
                    onEdit={() => startEditField(field.key)}
                    onSave={saveEditField}
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

function TrFieldCard({
  active,
  editing,
  editingValue,
  field,
  value,
  onActivate,
  onCancel,
  onChange,
  onEdit,
  onSave,
}: {
  active: boolean;
  editing: boolean;
  editingValue: string;
  field: TrFieldConfig;
  value: string | null;
  onActivate: () => void;
  onCancel: () => void;
  onChange: (value: string) => void;
  onEdit: () => void;
  onSave: () => void;
}) {
  return (
    <Box
      gridColumn={field.tone === 'wide' ? { base: 'span 1', md: 'span 3' } : undefined}
      borderWidth="1px"
      borderColor={active ? 'rgba(231, 111, 45, 0.85)' : 'rgba(73, 59, 36, 0.08)'}
      bg={active ? 'rgba(255, 247, 237, 0.94)' : 'rgba(251, 248, 242, 0.92)'}
      borderRadius="10px"
      minH={field.tone === 'title' ? '54px' : '72px'}
      p={3}
      cursor="pointer"
      onClick={onActivate}
    >
      <Flex justify="space-between" align="start" gap={2}>
        <Text color={field.tone === 'title' ? '#ec4899' : '#6a5a45'} fontSize="0.78rem">
          {field.label}
        </Text>
        {field.tone !== 'title' ? (
          <Box
            as="button"
            onClick={(event) => {
              event.stopPropagation();
              onEdit();
            }}
            color="#115e59"
            borderWidth="1px"
            borderColor="rgba(15, 118, 110, 0.18)"
            borderRadius="10px"
            bg="rgba(15, 118, 110, 0.06)"
            h="32px"
            px={2.5}
            fontWeight="700"
            fontSize="0.74rem"
          >
            EDIT
          </Box>
        ) : null}
      </Flex>

      {editing ? (
        <Stack mt={2} gap={2}>
          <input
            value={editingValue}
            onChange={(event) => onChange(event.target.value)}
            onClick={(event) => event.stopPropagation()}
            style={{
              background: 'white',
              border: '1px solid rgba(73, 59, 36, 0.18)',
              borderRadius: '10px',
              height: 38,
              outline: 'none',
              padding: '0 10px',
              width: '100%',
            }}
          />
          <Flex gap={2}>
            <Box as="button" onClick={(event) => { event.stopPropagation(); onSave(); }} borderRadius="9px" borderWidth="1px" borderColor="rgba(194, 65, 12, 0.28)" bg="rgba(255, 237, 213, 0.92)" color="#9a3412" fontWeight="700" px={3} py={1}>
              Update
            </Box>
            <Box as="button" onClick={(event) => { event.stopPropagation(); onCancel(); }} borderRadius="9px" borderWidth="1px" borderColor="rgba(73, 59, 36, 0.18)" bg="white" color="#6a5a45" fontWeight="700" px={3} py={1}>
              Cancel
            </Box>
          </Flex>
        </Stack>
      ) : (
        <Text mt={1} color="#241d17" fontSize="1rem" fontWeight="700" lineHeight="1.45">
          {value || '-'}
        </Text>
      )}
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

function getWatermarkBadge(page: ImportRecord['pages'][number] | null) {
  if (!page) {
    return null;
  }

  if (
    page.cleaning_mode === 'tr_watermark_cleaned' ||
    page.cleaning_mode === 'tr_dotted_watermark_cleaned' ||
    page.watermark_detected === true
  ) {
    return {
      label: 'พบลายน้ำ: ตัดลายน้ำก่อน OCR',
      bg: 'rgba(255, 247, 237, 0.96)',
      borderColor: 'rgba(231, 111, 45, 0.3)',
      color: '#9a3412',
    };
  }

  if (page.cleaning_mode === 'tr_original_no_watermark' || page.watermark_detected === false) {
    return {
      label: 'ไม่มีลายน้ำ: ใช้ต้นฉบับ OCR',
      bg: 'rgba(236, 253, 245, 0.9)',
      borderColor: 'rgba(15, 118, 110, 0.22)',
      color: '#115e59',
    };
  }

  return {
    label: 'ยังไม่มีผลตรวจลายน้ำ',
    bg: 'rgba(243, 244, 246, 0.92)',
    borderColor: 'rgba(107, 114, 128, 0.22)',
    color: '#4b5563',
  };
}

function getFieldHighlight(
  record: ImportRecord | null,
  key: TrFieldKey,
): HighlightTarget | null {
  const field = record?.review_data?.fields?.[key];
  if (!field) {
    return null;
  }
  return toHighlightTarget(field);
}

function toHighlightTarget(field: ReviewField): HighlightTarget | null {
  const pageNumber =
    typeof field.pageNumber === 'number' && Number.isFinite(field.pageNumber)
      ? field.pageNumber
      : null;
  const bbox = normalizeBbox(field.bbox);
  if (!pageNumber || !bbox) {
    return null;
  }
  return { pageNumber, bbox };
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
  const [left, top, right, bottom] = normalized;
  if (normalized.some((value) => Number.isNaN(value)) || right <= left || bottom <= top) {
    return null;
  }
  return [
    Math.max(0, Math.min(1, left)),
    Math.max(0, Math.min(1, top)),
    Math.max(0, Math.min(1, right)),
    Math.max(0, Math.min(1, bottom)),
  ];
}

function getHighlightStyle(bbox: [number, number, number, number]) {
  const [left, top, right, bottom] = bbox;
  return {
    left: `${left * 100}%`,
    top: `${top * 100}%`,
    width: `${(right - left) * 100}%`,
    height: `${(bottom - top) * 100}%`,
  };
}
