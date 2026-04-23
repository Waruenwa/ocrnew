'use client';

import {
  CSSProperties,
  FormEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import {
  Box,
  Button,
  CloseButton,
  Drawer,
  Flex,
  Grid,
  Heading,
  IconButton,
  Image,
  Input,
  Portal,
  Stack,
  Text,
  Textarea,
  chakra,
} from '@chakra-ui/react';
import { MdEdit } from 'react-icons/md';

import {
  API_BASE_URL,
  type AppConfig,
  formatDate,
  type ImportPageAsset,
  type ImportRecord,
  type TextSegment,
} from '../../lib/review';

type ResultMode = 'lines' | 'page' | 'diff' | 'edit';
type DiffStatus = 'same' | 'changed' | 'missing';

type DiffRow = {
  id: string;
  status: DiffStatus;
  originalText: string;
  cleanedText: string;
  selectedText: string;
  similarity: number;
};

type ReviewLineRow = {
  segment: TextSegment;
  lineNumber: number;
  currentText: string;
  rawText: string;
  supportText: string | null;
  supportLabel: string | null;
};

type EditableLineEntry = {
  sourceIndex: number;
  markdownLineIndex: number;
  line: string;
  rawLine: string;
};

type ReviewPageViewProps = {
  activePageNumber: number;
  activeSegment: TextSegment | null;
  activeSegmentId: string | null;
  checkedBy: string;
  config: AppConfig | null;
  correctedText: string | null;
  correctionModelLabel: string;
  diffRows: DiffRow[];
  draftMessage: string | null;
  editableLineText: string;
  editableMarkdown: string;
  editingLineRow: ReviewLineRow | null;
  editingSegmentId: string | null;
  errorMessage: string | null;
  hasCorrectedText: boolean;
  hasLineDraftChanges: boolean;
  hasUnsavedChanges: boolean;
  isLineEditorOpen: boolean;
  isSavingEdit: boolean;
  isSubmittingCheck: boolean;
  lineDraft: string;
  lineEditorRef: React.RefObject<HTMLTextAreaElement | null>;
  lineReviewRows: ReviewLineRow[];
  note: string;
  pageEditorRef: React.RefObject<HTMLTextAreaElement | null>;
  previewStageRef: React.RefObject<HTMLDivElement | null>;
  previewUrl: string;
  record: ImportRecord | null;
  resultMode: ResultMode;
  resultSurfaceRef: React.RefObject<HTMLDivElement | null>;
  saveMessage: string | null;
  selectedPage: ImportPageAsset | null;
  selectedRawText: string;
  selectedSourceLabel: string;
  suspiciousDiffRows: DiffRow[];
  stableDiffRows: DiffRow[];
  onApplyLineDraft: () => void;
  onChangeCheckedBy: (value: string) => void;
  onChangeLineDraft: (value: string) => void;
  onChangeMarkdown: (value: string) => void;
  onChangeNote: (value: string) => void;
  onCheckImport: (event: FormEvent<HTMLFormElement>) => void;
  onCloseLineEditor: () => void;
  onOpenLineEditor: (segment: TextSegment) => void;
  onOpenPageEditor: () => void;
  onSavePageEdit: () => void;
  onSelectPage: (pageNumber: number) => void;
  onSelectSegment: (segment: TextSegment) => void;
  onSetResultMode: (mode: ResultMode) => void;
};

const NextLink = chakra(Link);

const panelStyles = {
  borderWidth: '1px',
  borderColor: 'rgba(73, 59, 36, 0.12)',
  borderRadius: '28px',
  bg: 'rgba(255, 255, 255, 0.9)',
  boxShadow: '0 18px 42px rgba(31, 26, 20, 0.08)',
};

const softCardStyles = {
  borderWidth: '1px',
  borderColor: 'rgba(73, 59, 36, 0.08)',
  borderRadius: '20px',
  bg: 'rgba(251, 248, 242, 0.92)',
};

const codeBlockStyles = {
  bg: '#17130f',
  borderRadius: '18px',
  color: '#fef8ef',
  fontFamily: 'var(--font-mono), monospace',
  fontSize: '0.92rem',
  lineHeight: '1.7',
  m: 0,
  overflow: 'auto',
  p: 4,
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};

export default function ImportReviewPage() {
  const params = useParams<{ importId: string }>();
  const importId = Array.isArray(params?.importId)
    ? params.importId[0]
    : params?.importId;

  const [config, setConfig] = useState<AppConfig | null>(null);
  const [record, setRecord] = useState<ImportRecord | null>(null);
  const [activePageNumber, setActivePageNumber] = useState(1);
  const [activeSegmentId, setActiveSegmentId] = useState<string | null>(null);
  const [editingSegmentId, setEditingSegmentId] = useState<string | null>(null);
  const [isLineEditorOpen, setIsLineEditorOpen] = useState(false);
  const [resultMode, setResultMode] = useState<ResultMode>('lines');
  const [editableMarkdown, setEditableMarkdown] = useState('');
  const [lineDraft, setLineDraft] = useState('');
  const [checkedBy, setCheckedBy] = useState('');
  const [note, setNote] = useState('');
  const [isSavingEdit, setIsSavingEdit] = useState(false);
  const [isSubmittingCheck, setIsSubmittingCheck] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [draftMessage, setDraftMessage] = useState<string | null>(null);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  const previewStageRef = useRef<HTMLDivElement | null>(null);
  const resultSurfaceRef = useRef<HTMLDivElement | null>(null);
  const lineEditorRef = useRef<HTMLTextAreaElement | null>(null);
  const pageEditorRef = useRef<HTMLTextAreaElement | null>(null);
  const initializedFormRef = useRef(false);

  const selectedPage =
    record?.pages.find((page) => page.page_number === activePageNumber) ?? null;
  const activeSegment =
    selectedPage?.segments.find((segment) => segment.id === activeSegmentId) ??
    null;
  const diffRows = selectedPage ? buildDiffRows(selectedPage) : [];
  const lineReviewRows = selectedPage ? buildLineReviewRows(selectedPage) : [];
  const editingLineRow =
    lineReviewRows.find((row) => row.segment.id === editingSegmentId) ?? null;
  const suspiciousDiffRows = diffRows.filter((row) => row.status !== 'same');
  const stableDiffRows = diffRows.filter((row) => row.status === 'same');
  const selectedSourceLabel = getSelectedSourceLabel(
    selectedPage?.selected_markdown_source ?? null,
  );
  const selectedRawText =
    selectedPage?.raw_markdown ?? selectedPage?.markdown ?? '';
  const correctedText = selectedPage?.corrected_markdown ?? null;
  const correctionModelLabel =
    selectedPage?.correction_model ??
    record?.correction_model ??
    config?.ocr_model ??
    'Not configured';
  const hasCorrectedText = Boolean(correctedText);
  const hasUnsavedChanges = (selectedPage?.markdown ?? '') !== editableMarkdown;
  const editableLineText = editingLineRow
    ? getEditableLineText(editableMarkdown, editingLineRow.segment)
    : '';
  const hasLineDraftChanges =
    Boolean(editingLineRow) &&
    Boolean(lineDraft.trim()) &&
    normalizeDiffText(lineDraft) !== normalizeDiffText(editableLineText);

  useEffect(() => {
    if (!importId) {
      return;
    }

    void loadReview(importId);
  }, [importId]);

  useEffect(() => {
    if (!record?.pages.length) {
      return;
    }

    const hasPage = record.pages.some(
      (page) => page.page_number === activePageNumber,
    );
    if (!hasPage) {
      setActivePageNumber(record.pages[0].page_number);
      setActiveSegmentId(null);
    }
  }, [record, activePageNumber]);

  useEffect(() => {
    if (!record || initializedFormRef.current) {
      return;
    }

    setCheckedBy(record.checked_by ?? '');
    setNote(record.note ?? '');
    initializedFormRef.current = true;
  }, [record]);

  useEffect(() => {
    setEditableMarkdown(selectedPage?.markdown ?? '');
  }, [record?.id, activePageNumber, selectedPage?.markdown]);

  useEffect(() => {
    setSaveMessage(null);
  }, [record?.id, activePageNumber]);

  useEffect(() => {
    if (!editingLineRow) {
      setLineDraft('');
      return;
    }

    setLineDraft(getEditableLineText(editableMarkdown, editingLineRow.segment));
  }, [activePageNumber, editingSegmentId, editableMarkdown]);

  useEffect(() => {
    setDraftMessage(null);
    setIsLineEditorOpen(false);
    setEditingSegmentId(null);
  }, [record?.id, activePageNumber]);

  useEffect(() => {
    if (!isLineEditorOpen) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      lineEditorRef.current?.focus();
      lineEditorRef.current?.select();
    }, 90);

    return () => window.clearTimeout(timeoutId);
  }, [editingSegmentId, isLineEditorOpen]);

  useEffect(() => {
    if (resultMode === 'lines') {
      return;
    }

    setIsLineEditorOpen(false);
    setEditingSegmentId(null);
  }, [resultMode]);

  useEffect(() => {
    if (!activeSegment || !previewStageRef.current) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      const container = previewStageRef.current;
      if (!container) {
        return;
      }

      const targetTop = activeSegment.bbox[1] * container.scrollHeight;
      container.scrollTo({
        top: Math.max(targetTop - container.clientHeight * 0.25, 0),
        behavior: 'smooth',
      });
    }, 80);

    return () => window.clearTimeout(timeoutId);
  }, [activeSegment, activePageNumber]);

  useEffect(() => {
    const container = resultSurfaceRef.current;
    if (!container) {
      return;
    }

    container.scrollTo({
      top: 0,
      behavior: 'smooth',
    });
  }, [activePageNumber, resultMode]);

  async function loadReview(currentImportId: string) {
    setErrorMessage(null);
    setSaveMessage(null);
    initializedFormRef.current = false;
    try {
      await Promise.all([fetchConfig(), fetchImport(currentImportId)]);
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Unable to load this review page.',
      );
    }
  }

  async function fetchConfig() {
    const response = await fetch(`${API_BASE_URL}/api/config`, {
      cache: 'no-store',
    });
    if (!response.ok) {
      throw new Error('Unable to load app config.');
    }

    const data = (await response.json()) as AppConfig;
    setConfig(data);
  }

  async function fetchImport(currentImportId: string) {
    const response = await fetch(
      `${API_BASE_URL}/api/imports/${currentImportId}`,
      {
        cache: 'no-store',
      },
    );
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as {
        detail?: string;
      } | null;
      throw new Error(payload?.detail || 'Unable to load this review record.');
    }

    const data = (await response.json()) as ImportRecord;
    setRecord(data);
    setActivePageNumber(data.pages[0]?.page_number ?? 1);
    setActiveSegmentId(null);
  }

  async function handleCheckImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!record) {
      return;
    }

    setIsSubmittingCheck(true);
    setErrorMessage(null);

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/imports/${record.id}/check`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            checked_by: checkedBy.trim() || null,
            note: note.trim() || null,
          }),
        },
      );

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(
          payload?.detail || 'Unable to mark this file as checked.',
        );
      }

      const data = (await response.json()) as ImportRecord;
      setRecord(data);
      setCheckedBy(data.checked_by ?? '');
      setNote(data.note ?? '');
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Unable to mark this file as checked.',
      );
    } finally {
      setIsSubmittingCheck(false);
    }
  }

  async function handleSavePageEdit() {
    if (!record || !selectedPage) {
      return;
    }

    setIsSavingEdit(true);
    setErrorMessage(null);
    setDraftMessage(null);
    setSaveMessage(null);

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/imports/${record.id}/pages/${selectedPage.page_number}/save`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            markdown: editableMarkdown,
          }),
        },
      );

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(
          payload?.detail || 'Unable to save the edited OCR text.',
        );
      }

      const data = (await response.json()) as ImportRecord;
      setRecord(data);
      setActiveSegmentId(null);
      setEditingSegmentId(null);
      setIsLineEditorOpen(false);
      setSaveMessage(`Saved page ${selectedPage.page_number} to MongoDB.`);
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Unable to save the edited OCR text.',
      );
    } finally {
      setIsSavingEdit(false);
    }
  }

  function handleSelectSegment(segment: TextSegment) {
    setActivePageNumber(segment.page_number);
    setActiveSegmentId(segment.id);
  }

  function handleOpenLineEditor(segment: TextSegment) {
    setActivePageNumber(segment.page_number);
    setActiveSegmentId(segment.id);
    setEditingSegmentId(segment.id);
    setLineDraft(getEditableLineText(editableMarkdown, segment));
    setIsLineEditorOpen(true);
  }

  function handleCloseLineEditor() {
    setIsLineEditorOpen(false);
    setEditingSegmentId(null);
  }

  function focusPageEditor() {
    window.setTimeout(() => {
      const editor = pageEditorRef.current;
      if (!editor) {
        return;
      }

      editor.focus();
      const cursorPosition = editor.value.length;
      editor.setSelectionRange(cursorPosition, cursorPosition);
    }, 90);
  }

  function handleOpenPageEditor() {
    setIsLineEditorOpen(false);
    setEditingSegmentId(null);
    setResultMode('edit');
    focusPageEditor();
  }

  function handleApplyLineDraft() {
    if (!editingLineRow) {
      return;
    }

    const nextLine = lineDraft.trim();
    if (!nextLine) {
      return;
    }

    const nextMarkdown = applySegmentEditToMarkdown(
      editableMarkdown,
      editingLineRow.segment,
      nextLine,
    );
    setEditableMarkdown(nextMarkdown);
    setDraftMessage(
      `Updated line ${editingLineRow.lineNumber} in the page draft. Open Edit page text when you want to review or save it.`,
    );
    setSaveMessage(null);
    setIsLineEditorOpen(false);
    setEditingSegmentId(null);
  }

  const previewUrl =
    record && selectedPage
      ? `${API_BASE_URL}/api/imports/${record.id}/pages/${selectedPage.page_number}/cleaned`
      : '';

  return (
    <ReviewPageView
      activePageNumber={activePageNumber}
      activeSegment={activeSegment}
      activeSegmentId={activeSegmentId}
      checkedBy={checkedBy}
      config={config}
      correctedText={correctedText}
      correctionModelLabel={correctionModelLabel}
      diffRows={diffRows}
      draftMessage={draftMessage}
      editableLineText={editableLineText}
      editableMarkdown={editableMarkdown}
      editingLineRow={editingLineRow}
      editingSegmentId={editingSegmentId}
      errorMessage={errorMessage}
      hasCorrectedText={hasCorrectedText}
      hasLineDraftChanges={hasLineDraftChanges}
      hasUnsavedChanges={hasUnsavedChanges}
      isLineEditorOpen={isLineEditorOpen}
      isSavingEdit={isSavingEdit}
      isSubmittingCheck={isSubmittingCheck}
      lineDraft={lineDraft}
      lineEditorRef={lineEditorRef}
      lineReviewRows={lineReviewRows}
      note={note}
      pageEditorRef={pageEditorRef}
      previewStageRef={previewStageRef}
      previewUrl={previewUrl}
      record={record}
      resultMode={resultMode}
      resultSurfaceRef={resultSurfaceRef}
      saveMessage={saveMessage}
      selectedPage={selectedPage}
      selectedRawText={selectedRawText}
      selectedSourceLabel={selectedSourceLabel}
      suspiciousDiffRows={suspiciousDiffRows}
      stableDiffRows={stableDiffRows}
      onApplyLineDraft={handleApplyLineDraft}
      onChangeCheckedBy={setCheckedBy}
      onChangeLineDraft={setLineDraft}
      onChangeMarkdown={setEditableMarkdown}
      onChangeNote={setNote}
      onCheckImport={handleCheckImport}
      onCloseLineEditor={handleCloseLineEditor}
      onOpenLineEditor={handleOpenLineEditor}
      onOpenPageEditor={handleOpenPageEditor}
      onSavePageEdit={() => void handleSavePageEdit()}
      onSelectPage={(pageNumber) => {
        setActivePageNumber(pageNumber);
        setActiveSegmentId(null);
      }}
      onSelectSegment={handleSelectSegment}
      onSetResultMode={setResultMode}
    />
  );
}

function ReviewPageView({
  activePageNumber,
  activeSegment,
  activeSegmentId,
  checkedBy,
  config,
  correctedText,
  correctionModelLabel,
  draftMessage,
  editableLineText,
  editableMarkdown,
  editingLineRow,
  editingSegmentId,
  errorMessage,
  hasCorrectedText,
  hasLineDraftChanges,
  hasUnsavedChanges,
  isLineEditorOpen,
  isSavingEdit,
  isSubmittingCheck,
  lineDraft,
  lineEditorRef,
  lineReviewRows,
  note,
  pageEditorRef,
  previewStageRef,
  previewUrl,
  record,
  resultMode,
  resultSurfaceRef,
  saveMessage,
  selectedPage,
  selectedRawText,
  selectedSourceLabel,
  suspiciousDiffRows,
  stableDiffRows,
  onApplyLineDraft,
  onChangeCheckedBy,
  onChangeLineDraft,
  onChangeMarkdown,
  onChangeNote,
  onCheckImport,
  onCloseLineEditor,
  onOpenLineEditor,
  onOpenPageEditor,
  onSavePageEdit,
  onSelectPage,
  onSelectSegment,
  onSetResultMode,
}: ReviewPageViewProps) {
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
        >
          <Flex align="start" justify="space-between">
            <NextLink
              href="/"
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
              textDecoration="none"
              _hover={{ bg: 'rgba(15, 118, 110, 0.12)' }}
            >
              กลับหน้าแรก
            </NextLink>
          </Flex>

          {record ? (
            <>
              <Flex gap={3} wrap="wrap">
                {record.pages.map((page) => (
                  <PillButton
                    key={`${record.id}-page-${page.page_number}`}
                    active={activePageNumber === page.page_number}
                    onClick={() => onSelectPage(page.page_number)}
                  >
                    Page {page.page_number}
                  </PillButton>
                ))}
              </Flex>

              <Box
                borderWidth="1px"
                borderColor="rgba(73, 59, 36, 0.08)"
                borderRadius="24px"
                bg="white"
                minH={{ base: '52vh', xl: '78vh' }}
                overflow="auto"
                p={{ base: 2, md: 3 }}
                ref={previewStageRef}
              >
                {selectedPage ? (
                  <Box position="relative">
                    <Image
                      alt={`${record.source_filename} page ${selectedPage.page_number}`}
                      borderRadius="16px"
                      display="block"
                      src={previewUrl}
                      w="full"
                    />
                    {activeSegment ? (
                      <Box
                        pointerEvents="none"
                        position="absolute"
                        style={getHighlightStyle(activeSegment.bbox)}
                        border="2px solid rgba(255, 255, 255, 0.96)"
                        borderRadius="14px"
                        outline="3px solid rgba(231, 111, 45, 0.98)"
                        outlineOffset="4px"
                        boxShadow="0 0 0 6px rgba(231, 111, 45, 0.16), 0 14px 28px rgba(231, 111, 45, 0.14), 0 0 0 9999px rgba(231, 111, 45, 0.05)"
                      />
                    ) : null}
                  </Box>
                ) : (
                  <EmptyStateCard
                    description="This document does not have a DOIT preview yet."
                    label="DOIT"
                    title="No preview page available."
                  />
                )}
              </Box>
            </>
          ) : (
            <EmptyStateCard
              description="The document preview will appear here."
              label="DOIT"
              title="Loading document review..."
            />
          )}
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
          <Flex align="start" gap={4} justify="space-between">
            <Box>
              <StepTag>ผลลัพธ์</StepTag>
            </Box>

            {record?.checked_at ? (
              <StatusPill bg="rgba(220, 252, 231, 0.95)" color="#166534">
                Checked {formatDate(record.checked_at)}
              </StatusPill>
            ) : null}
          </Flex>

          {errorMessage ? (
            <Text color="#b42318" fontWeight="600">
              {errorMessage}
            </Text>
          ) : null}

          {record ? (
            <>
              {record.ocr_error_message ? (
                <Callout tone="warning" title="OCR could not be prepared">
                  {record.ocr_error_message}
                </Callout>
              ) : null}

              {!record.ocr_markdown && !record.ocr_error_message ? (
                <Callout tone="warning" title="OCR result is not ready yet">
                  {config?.ocr_ready
                    ? 'Restart the backend so the automatic folder scan can prepare OCR for this document.'
                    : 'Configure the OCR endpoint first, then restart the backend so the automatic folder scan can run.'}
                </Callout>
              ) : null}

              {selectedPage ? (
                <>
                  {/* <Grid
                    gap={3}
                    templateColumns={{
                      base: '1fr',
                      md: 'repeat(2, minmax(0, 1fr))',
                      xl: 'repeat(4, minmax(0, 1fr))',
                    }}
                  >
                    <MetricCard label="Selected OCR" value={selectedSourceLabel} />
                    <MetricCard
                      label="Text correction"
                      value={hasCorrectedText ? correctionModelLabel : 'Raw OCR'}
                    />
                    <MetricCard
                      label="Similarity"
                      value={formatSimilarity(selectedPage.diff_similarity)}
                    />
                    <MetricCard
                      label="Correction delta"
                      value={formatSimilarity(selectedPage.correction_similarity)}
                    />
                  </Grid> */}

                  <Flex gap={3} wrap="wrap">
                    <PillButton
                      active={resultMode === 'lines'}
                      onClick={() => onSetResultMode('lines')}
                    >
                      Review lines
                    </PillButton>
                    <PillButton
                      active={resultMode === 'page'}
                      onClick={() => onSetResultMode('page')}
                    >
                      Page text
                    </PillButton>
                    <PillButton
                      active={resultMode === 'diff'}
                      onClick={() => onSetResultMode('diff')}
                    >
                      Raw text diff
                    </PillButton>
                    <PillButton
                      active={resultMode === 'edit'}
                      minW={{ base: '100%', md: '220px' }}
                      onClick={onOpenPageEditor}
                    >
                      Edit page text
                    </PillButton>
                  </Flex>

                  {resultMode === 'lines' ? (
                    <>
                      {/* <HintBox>
                        Click a line to highlight it on the DOIT preview. Use
                        the pencil button to open the line editor.
                      </HintBox> */}
                      {draftMessage ? <HintBox>{draftMessage}</HintBox> : null}

                      {selectedPage.correction_error ? (
                        <Callout
                          tone="warning"
                          title="Typhoon OCR line reread was skipped for this page"
                        >
                          {selectedPage.correction_error}
                        </Callout>
                      ) : null}

                      <Box
                        borderWidth="1px"
                        borderColor="rgba(73, 59, 36, 0.08)"
                        borderRadius="24px"
                        bg="white"
                        display="grid"
                        gap={2}
                        flex="1"
                        minH={{ base: '52vh', xl: '78vh' }}
                        overflowY="auto"
                        overflowX="hidden"
                        p={{ base: 2, md: 3 }}
                        ref={resultSurfaceRef}
                      >
                        <Flex
                          align="center"
                          color="#6a5a45"
                          gap={3}
                          wrap="wrap"
                        >
                          <StatusPill
                            bg="linear-gradient(135deg, #0f766e, #115e59)"
                            color="white"
                          >
                            Page {selectedPage.page_number}
                          </StatusPill>
                          <Text fontSize="xl">
                            {selectedPage.segments.length} review line(s)
                          </Text>
                        </Flex>

                        {selectedPage.segments.length > 0 ? (
                          <Stack gap={2.5} w="full">
                            {lineReviewRows.map((row) => {
                              const isActive =
                                activeSegmentId === row.segment.id;
                              const isEditing =
                                editingSegmentId === row.segment.id;
                              const draftLineText =
                                getEditableLineText(
                                  editableMarkdown,
                                  row.segment,
                                ) || row.currentText;
                              const hasSupportText = Boolean(row.supportText);

                              return (
                                <Flex
                                  key={row.segment.id}
                                  align="stretch"
                                  gap={2.5}
                                  fontSize={'xs'}
                                  minW={0}
                                  w="full"
                                >
                                  <Button
                                    alignItems="stretch"
                                    bg={
                                      isActive
                                        ? 'linear-gradient(135deg, #0f766e, #115e59)'
                                        : 'white'
                                    }
                                    borderWidth="1px"
                                    borderColor={
                                      isActive
                                        ? 'transparent'
                                        : 'rgba(73, 59, 36, 0.12)'
                                    }
                                    borderRadius="18px"
                                    color={isActive ? 'white' : '#1f1a14'}
                                    flex="1"
                                    h="auto"
                                    justifyContent="flex-start"
                                    minH={hasSupportText ? '72px' : '56px'}
                                    minW={0}
                                    onClick={() => onSelectSegment(row.segment)}
                                    px={4}
                                    py={hasSupportText ? 3.5 : 3}
                                    whiteSpace="normal"
                                    _hover={{
                                      bg: isActive
                                        ? 'linear-gradient(135deg, #0f766e, #115e59)'
                                        : 'rgba(251, 248, 242, 0.92)',
                                    }}
                                  >
                                    <Stack
                                      align="stretch"
                                      gap={2}
                                      minW={0}
                                      textAlign="left"
                                      w="full"
                                    >
                                      <Text
                                        fontSize="md"
                                        fontWeight="700"
                                        lineHeight="1.4"
                                        overflowWrap="anywhere"
                                        whiteSpace="normal"
                                        wordBreak="break-word"
                                      >
                                        {draftLineText}
                                      </Text>
                                      {row.supportText ? (
                                        <Box
                                          borderTopWidth="1px"
                                          borderColor={
                                            isActive
                                              ? 'rgba(255, 255, 255, 0.24)'
                                              : 'rgba(73, 59, 36, 0.08)'
                                          }
                                          color={
                                            isActive
                                              ? 'rgba(255,255,255,0.9)'
                                              : '#6a5a45'
                                          }
                                          pt={2}
                                        >
                                          <Text
                                            fontSize="0.7rem"
                                            fontWeight="700"
                                            letterSpacing="0.04em"
                                            textTransform="uppercase"
                                          >
                                            {row.supportLabel ?? 'Support text'}
                                          </Text>
                                          <Text
                                            fontSize="sm"
                                            mt={1}
                                            overflowWrap="anywhere"
                                            whiteSpace="normal"
                                            wordBreak="break-word"
                                          >
                                            {row.supportText}
                                          </Text>
                                        </Box>
                                      ) : null}
                                    </Stack>
                                  </Button>

                                  <IconButton
                                    aria-label={`Edit line ${row.lineNumber}`}
                                    bg={
                                      isEditing
                                        ? 'linear-gradient(135deg, #0f766e, #115e59)'
                                        : 'white'
                                    }
                                    borderWidth="1px"
                                    borderColor={
                                      isEditing
                                        ? 'transparent'
                                        : 'rgba(73, 59, 36, 0.12)'
                                    }
                                    borderRadius="18px"
                                    color={isEditing ? 'white' : '#115e59'}
                                    flexShrink={0}
                                    h="56px"
                                    minW="48px"
                                    onClick={() =>
                                      onOpenLineEditor(row.segment)
                                    }
                                    px={0}
                                    _hover={{
                                      bg: 'linear-gradient(135deg, #0f766e, #115e59)',
                                      color: 'white',
                                    }}
                                  >
                                    <MdEdit size={18} />
                                  </IconButton>
                                </Flex>
                              );
                            })}
                          </Stack>
                        ) : (
                          <EmptyInlineText>
                            No review lines were generated for this page yet.
                          </EmptyInlineText>
                        )}
                      </Box>

                      <Drawer.Root
                        closeOnEscape
                        lazyMount
                        onOpenChange={(details) => {
                          if (!details.open) {
                            onCloseLineEditor();
                          }
                        }}
                        open={isLineEditorOpen}
                        placement="end"
                        size="md"
                      >
                        <Portal>
                          <Drawer.Backdrop
                            bg="rgba(0, 0, 0, 0.28)"
                            backdropFilter="blur(2px)"
                          />
                          <Drawer.Positioner p={{ base: 2, md: 4 }}>
                            <Drawer.Content
                              bg="rgba(255, 252, 248, 0.98)"
                              borderRadius={{ base: '24px', md: '28px' }}
                              borderWidth="1px"
                              borderColor="rgba(73, 59, 36, 0.08)"
                              maxW={{ base: 'calc(100vw - 16px)', md: '440px' }}
                              overflow="hidden"
                            >
                              <Drawer.Header
                                borderBottomWidth="1px"
                                borderColor="rgba(73, 59, 36, 0.08)"
                              >
                                <Flex
                                  align="start"
                                  gap={3}
                                  justify="space-between"
                                >
                                  <Box>
                                    <StepTag>Line Editor</StepTag>
                                    <Drawer.Title
                                      mt={2}
                                      fontSize="1.35rem"
                                      fontWeight="700"
                                      lineHeight="1.25"
                                    >
                                      {editingLineRow
                                        ? `Edit Line ${editingLineRow.lineNumber}`
                                        : 'Line Editor'}
                                    </Drawer.Title>
                                  </Box>
                                  <CloseButton onClick={onCloseLineEditor} />
                                </Flex>
                              </Drawer.Header>

                              <Drawer.Body p={5}>
                                {editingLineRow ? (
                                  <Stack gap={4}>
                                    <Text color="#6a5a45" lineHeight="1.7">
                                      OCR line from the image stays linked on
                                      the left. Update the text below, then
                                      apply it to the page draft.
                                    </Text>

                                    <ReferenceCard label="Current line">
                                      {editableLineText ||
                                        editingLineRow.currentText}
                                    </ReferenceCard>

                                    {normalizeDiffText(
                                      editingLineRow.rawText,
                                    ) !==
                                    normalizeDiffText(
                                      editableLineText ||
                                        editingLineRow.currentText,
                                    ) ? (
                                      <ReferenceCard label="Image anchor" muted>
                                        {editingLineRow.rawText}
                                      </ReferenceCard>
                                    ) : null}

                                    <Box>
                                      <Text fontWeight="700" mb={2}>
                                        New line text
                                      </Text>
                                      <Textarea
                                        borderColor="rgba(73, 59, 36, 0.12)"
                                        borderRadius="18px"
                                        bg="white"
                                        minH="180px"
                                        onChange={(event) =>
                                          onChangeLineDraft(event.target.value)
                                        }
                                        ref={lineEditorRef}
                                        resize="vertical"
                                        value={lineDraft}
                                      />
                                    </Box>

                                    {editingLineRow.supportText ? (
                                      <ReferenceCard
                                        label={
                                          editingLineRow.supportLabel ??
                                          'Support text'
                                        }
                                        muted
                                      >
                                        {editingLineRow.supportText}
                                      </ReferenceCard>
                                    ) : null}

                                    <Stack gap={3}>
                                      <Button
                                        bg="linear-gradient(135deg, #e76f2d, #c4511e)"
                                        borderRadius="full"
                                        color="white"
                                        disabled={!hasLineDraftChanges}
                                        onClick={onApplyLineDraft}
                                        px={5}
                                        py={6}
                                        _hover={{
                                          bg: 'linear-gradient(135deg, #e76f2d, #c4511e)',
                                        }}
                                      >
                                        Update page draft
                                      </Button>
                                      <Button
                                        bg="white"
                                        borderWidth="1px"
                                        borderColor="rgba(73, 59, 36, 0.12)"
                                        borderRadius="full"
                                        onClick={onOpenPageEditor}
                                      >
                                        Open full page editor
                                      </Button>
                                    </Stack>
                                  </Stack>
                                ) : null}
                              </Drawer.Body>
                            </Drawer.Content>
                          </Drawer.Positioner>
                        </Portal>
                      </Drawer.Root>
                    </>
                  ) : null}

                  {resultMode === 'page' ? (
                    <>
                      <HintBox>
                        {hasCorrectedText
                          ? 'This panel shows the Typhoon OCR line-reread text for the current page. Raw OCR is still available below for comparison.'
                          : 'This panel shows the selected raw OCR text for the current page.'}
                      </HintBox>

                      <Box
                        borderWidth="1px"
                        borderColor="rgba(73, 59, 36, 0.08)"
                        borderRadius="24px"
                        bg="white"
                        display="grid"
                        gap={4}
                        flex="1"
                        minH={{ base: '48vh', xl: '72vh' }}
                        overflow="auto"
                        p={4}
                        ref={resultSurfaceRef}
                      >
                        <Flex
                          align="center"
                          color="#6a5a45"
                          gap={3}
                          wrap="wrap"
                        >
                          <StatusPill
                            bg="linear-gradient(135deg, #0f766e, #115e59)"
                            color="white"
                          >
                            Page {selectedPage.page_number}
                          </StatusPill>
                          <Text fontSize="lg">
                            {hasCorrectedText
                              ? 'Typhoon OCR line-reread text'
                              : 'Selected OCR text'}
                          </Text>
                        </Flex>

                        <CodeBlock minH="52vh">
                          {selectedPage.markdown ||
                            'No OCR text found for this page.'}
                        </CodeBlock>

                        <Grid
                          gap={4}
                          templateColumns={{
                            base: '1fr',
                            md: 'repeat(2, minmax(0, 1fr))',
                          }}
                        >
                          <SourceCard title="Selected raw OCR">
                            {selectedRawText || 'No raw OCR text.'}
                          </SourceCard>
                          <SourceCard
                            title={
                              hasCorrectedText
                                ? `${correctionModelLabel} corrected OCR`
                                : 'Typhoon OCR corrected OCR'
                            }
                          >
                            {correctedText || 'No corrected OCR text.'}
                          </SourceCard>
                        </Grid>
                      </Box>
                    </>
                  ) : null}

                  {resultMode === 'edit' ? (
                    <>
                      <HintBox>
                        Edit the OCR text for this page, save it to MongoDB,
                        then continue to the check form below when the page
                        looks correct.
                      </HintBox>
                      {saveMessage ? (
                        <StatusPill
                          bg="rgba(220, 252, 231, 0.95)"
                          color="#166534"
                        >
                          {saveMessage}
                        </StatusPill>
                      ) : null}
                      {draftMessage ? <HintBox>{draftMessage}</HintBox> : null}

                      <Box {...softCardStyles} p={5}>
                        <Stack gap={4}>
                          <Box>
                            <Text fontWeight="700" mb={2}>
                              Page {selectedPage.page_number} text
                            </Text>
                            <Textarea
                              bg="white"
                              borderColor="rgba(73, 59, 36, 0.12)"
                              borderRadius="18px"
                              minH="300px"
                              onChange={(event) =>
                                onChangeMarkdown(event.target.value)
                              }
                              ref={pageEditorRef}
                              resize="vertical"
                              value={editableMarkdown}
                            />
                          </Box>

                          <Grid
                            gap={3}
                            templateColumns={{
                              base: '1fr',
                              md: 'auto repeat(2, minmax(0, 1fr))',
                            }}
                          >
                            <Button
                              bg="linear-gradient(135deg, #e76f2d, #c4511e)"
                              borderRadius="full"
                              color="white"
                              disabled={isSavingEdit || !hasUnsavedChanges}
                              onClick={onSavePageEdit}
                              px={5}
                              py={6}
                            >
                              {isSavingEdit
                                ? 'Saving page...'
                                : hasUnsavedChanges
                                  ? 'Save page text'
                                  : 'No changes'}
                            </Button>

                            <MetricCard
                              label="Current source"
                              value={selectedSourceLabel}
                            />
                            <MetricCard
                              label="Lines after save"
                              value={String(selectedPage.segments.length)}
                            />
                          </Grid>
                        </Stack>
                      </Box>
                    </>
                  ) : null}
                </>
              ) : null}

              {resultMode === 'diff' && selectedPage ? (
                <>
                  <HintBox>
                    Raw text diff compares cleaned OCR and original OCR for this
                    page so the reviewer can see changed or missing rows
                    immediately.
                  </HintBox>

                  <Grid
                    gap={3}
                    templateColumns={{
                      base: '1fr',
                      md: 'repeat(2, minmax(0, 1fr))',
                      xl: 'repeat(3, minmax(0, 1fr))',
                    }}
                  >
                    <MetricCard
                      label="Suspicious rows"
                      value={String(suspiciousDiffRows.length)}
                    />
                    <MetricCard
                      label="Stable rows"
                      value={String(stableDiffRows.length)}
                    />
                    <MetricCard
                      label="Correction model"
                      value={
                        hasCorrectedText ? correctionModelLabel : 'Not applied'
                      }
                    />
                  </Grid>

                  <Stack gap={3}>
                    {suspiciousDiffRows.length > 0 ? (
                      suspiciousDiffRows.map((row) => (
                        <DiffCard
                          key={row.id}
                          row={row}
                          selectedSource={selectedPage.selected_markdown_source}
                        />
                      ))
                    ) : (
                      <EmptyInlineText>
                        No suspicious diff rows were detected on this page.
                      </EmptyInlineText>
                    )}
                  </Stack>
                </>
              ) : null}

              {record ? (
                <>
                  <DisclosureCard title="Selected OCR Markdown">
                    <CodeBlock compact>
                      {record.ocr_markdown ?? 'OCR is not ready yet.'}
                    </CodeBlock>
                  </DisclosureCard>

                  <chakra.form
                    {...softCardStyles}
                    p={5}
                    onSubmit={onCheckImport}
                  >
                    <Stack gap={4}>
                      <Box>
                        <StepTag>Check</StepTag>
                        <Heading mt={2} size="lg">
                          Confirm This Document
                        </Heading>
                      </Box>

                      <Box>
                        <Text fontWeight="700" mb={2}>
                          Checked by
                        </Text>
                        <Input
                          bg="white"
                          borderColor="rgba(73, 59, 36, 0.12)"
                          borderRadius="16px"
                          onChange={(event) =>
                            onChangeCheckedBy(event.target.value)
                          }
                          placeholder="Optional reviewer name"
                          value={checkedBy}
                        />
                      </Box>

                      <Box>
                        <Text fontWeight="700" mb={2}>
                          Note
                        </Text>
                        <Textarea
                          bg="white"
                          borderColor="rgba(73, 59, 36, 0.12)"
                          borderRadius="16px"
                          onChange={(event) => onChangeNote(event.target.value)}
                          placeholder="Optional note saved with the checked record"
                          resize="vertical"
                          rows={4}
                          value={note}
                        />
                      </Box>

                      <Grid
                        gap={3}
                        templateColumns={{
                          base: '1fr',
                          md: 'auto repeat(2, minmax(0, 1fr))',
                        }}
                      >
                        <Button
                          bg="linear-gradient(135deg, #e76f2d, #c4511e)"
                          borderRadius="full"
                          color="white"
                          disabled={isSubmittingCheck}
                          type="submit"
                          px={5}
                          py={6}
                        >
                          {isSubmittingCheck
                            ? 'Saving...'
                            : record.status === 'checked'
                              ? 'Update checked record'
                              : 'Mark checked'}
                        </Button>

                        <MetricCard
                          label="Created"
                          value={formatDate(record.created_at)}
                        />
                        <MetricCard
                          label="Checked by"
                          value={record.checked_by || 'Not set'}
                        />
                      </Grid>
                    </Stack>
                  </chakra.form>
                </>
              ) : null}
            </>
          ) : (
            <EmptyStateCard
              description="The OCR result panel will appear here."
              label="Result"
              title="Loading OCR result..."
            />
          )}
        </Box>
      </Grid>
    </Box>
  );
}

function StepTag({ children }: { children: ReactNode }) {
  return (
    <Flex
      align="center"
      bg="rgba(15, 118, 110, 0.1)"
      borderRadius="full"
      color="#115e59"
      display="inline-flex"
      fontSize="0.82rem"
      fontWeight="800"
      letterSpacing="0.04em"
      minH="30px"
      px={3}
      textTransform="uppercase"
      w="fit-content"
    >
      {children}
    </Flex>
  );
}

function StatusPill({
  bg,
  children,
  color,
}: {
  bg: string;
  children: ReactNode;
  color: string;
}) {
  return (
    <Flex
      align="center"
      as="span"
      bg={bg}
      borderRadius="full"
      color={color}
      display="inline-flex"
      fontSize="0.95rem"
      fontWeight="700"
      justify="center"
      minH="40px"
      px={4}
      whiteSpace="nowrap"
      w="fit-content"
    >
      {children}
    </Flex>
  );
}

function PillButton({
  active,
  children,
  minW,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  minW?: string | Record<string, string>;
  onClick: () => void;
}) {
  return (
    <Button
      bg={active ? 'linear-gradient(135deg, #0f766e, #115e59)' : 'white'}
      borderWidth="1px"
      borderColor={active ? 'transparent' : 'rgba(73, 59, 36, 0.12)'}
      borderRadius="full"
      color={active ? 'white' : '#1f1a14'}
      fontWeight="700"
      minW={minW}
      onClick={onClick}
      px={5}
      py={6}
      _hover={{
        bg: active
          ? 'linear-gradient(135deg, #0f766e, #115e59)'
          : 'rgba(251, 248, 242, 0.92)',
      }}
    >
      {children}
    </Button>
  );
}

function HintBox({ children }: { children: ReactNode }) {
  return (
    <Box
      borderWidth="1px"
      borderColor="rgba(15, 118, 110, 0.12)"
      borderRadius="18px"
      bg="rgba(15, 118, 110, 0.08)"
      color="#115e59"
      px={4}
      py={3}
    >
      <Text lineHeight="1.7">{children}</Text>
    </Box>
  );
}

function Callout({
  children,
  title,
  tone,
}: {
  children: ReactNode;
  title: string;
  tone: 'warning' | 'danger';
}) {
  const bg =
    tone === 'danger'
      ? 'rgba(254, 242, 242, 0.98)'
      : 'rgba(255, 243, 224, 0.78)';
  const borderColor =
    tone === 'danger' ? 'rgba(185, 28, 28, 0.22)' : 'rgba(231, 111, 45, 0.35)';
  return (
    <Box
      borderWidth="1px"
      borderColor={borderColor}
      borderRadius="18px"
      bg={bg}
      px={4}
      py={4}
    >
      <Text fontWeight="800">{title}</Text>
      <Box color="#6a5a45" lineHeight="1.7" mt={2}>
        {children}
      </Box>
    </Box>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <Box {...softCardStyles} p={4}>
      <Text color="#6a5a45" fontSize="0.9rem">
        {label}
      </Text>
      <Text fontSize="lg" fontWeight="700" mt={1} wordBreak="break-word">
        {value}
      </Text>
    </Box>
  );
}

function EmptyStateCard({
  description,
  label,
  title,
}: {
  description: string;
  label: string;
  title: string;
}) {
  return (
    <Box {...softCardStyles} p={6}>
      <StepTag>{label}</StepTag>
      <Heading mt={3} size="lg">
        {title}
      </Heading>
      <Text color="#6a5a45" lineHeight="1.7" mt={2}>
        {description}
      </Text>
    </Box>
  );
}

function EmptyInlineText({ children }: { children: ReactNode }) {
  return (
    <Box {...softCardStyles} p={6}>
      <Text color="#6a5a45" lineHeight="1.7">
        {children}
      </Text>
    </Box>
  );
}

function DisclosureCard({
  children,
  title,
}: {
  children: ReactNode;
  title: string;
}) {
  return (
    <chakra.details
      borderWidth="1px"
      borderColor="rgba(73, 59, 36, 0.08)"
      borderRadius="18px"
      bg="white"
      overflow="hidden"
    >
      <chakra.summary
        bg="rgba(15, 118, 110, 0.06)"
        cursor="pointer"
        fontWeight="700"
        px={4}
        py={3}
      >
        {title}
      </chakra.summary>
      <Box p={4}>{children}</Box>
    </chakra.details>
  );
}

function CodeBlock({
  children,
  compact = false,
  minH,
}: {
  children: ReactNode;
  compact?: boolean;
  minH?: string;
}) {
  return (
    <chakra.pre
      {...codeBlockStyles}
      maxH={compact ? '320px' : undefined}
      minH={minH ?? (compact ? '0' : '52vh')}
    >
      {children}
    </chakra.pre>
  );
}

function SourceCard({
  children,
  title,
}: {
  children: ReactNode;
  title: string;
}) {
  return (
    <Box
      borderWidth="1px"
      borderColor="rgba(73, 59, 36, 0.08)"
      borderRadius="18px"
      bg="rgba(255, 255, 255, 0.96)"
      p={4}
    >
      <Text color="#6a5a45" fontWeight="700" mb={3}>
        {title}
      </Text>
      <CodeBlock compact>{children}</CodeBlock>
    </Box>
  );
}

function ReferenceCard({
  children,
  label,
  muted = false,
}: {
  children: ReactNode;
  label: string;
  muted?: boolean;
}) {
  return (
    <Box
      borderWidth="1px"
      borderColor={
        muted ? 'rgba(73, 59, 36, 0.08)' : 'rgba(15, 118, 110, 0.14)'
      }
      borderRadius="18px"
      bg={muted ? 'rgba(255, 255, 255, 0.92)' : 'rgba(236, 253, 245, 0.6)'}
      p={4}
    >
      <Text
        color="#6a5a45"
        fontSize="0.76rem"
        fontWeight="700"
        letterSpacing="0.04em"
        textTransform="uppercase"
      >
        {label}
      </Text>
      <Text fontWeight="700" lineHeight="1.7" mt={2} wordBreak="break-word">
        {children}
      </Text>
    </Box>
  );
}

function DiffCard({
  row,
  selectedSource,
}: {
  row: DiffRow;
  selectedSource: 'original' | 'cleaned' | 'manual' | null;
}) {
  const statusLabel =
    row.status === 'missing'
      ? 'Missing'
      : row.status === 'changed'
        ? 'Changed'
        : 'Same';
  const borderColor =
    row.status === 'missing'
      ? 'rgba(185, 28, 28, 0.22)'
      : row.status === 'changed'
        ? 'rgba(217, 119, 6, 0.28)'
        : 'rgba(73, 59, 36, 0.1)';
  const bg =
    row.status === 'missing'
      ? 'rgba(254, 242, 242, 0.98)'
      : row.status === 'changed'
        ? 'rgba(255, 247, 237, 0.98)'
        : 'rgba(255, 255, 255, 0.96)';

  return (
    <Box
      borderWidth="1px"
      borderColor={borderColor}
      borderRadius="18px"
      bg={bg}
      p={4}
    >
      <Flex
        align="center"
        color="#6a5a45"
        gap={3}
        justify="space-between"
        wrap="wrap"
      >
        <StatusPill
          bg={
            row.status === 'missing'
              ? 'rgba(254, 226, 226, 0.95)'
              : row.status === 'changed'
                ? 'rgba(255, 237, 213, 0.95)'
                : 'rgba(236, 253, 245, 0.95)'
          }
          color={
            row.status === 'missing'
              ? '#991b1b'
              : row.status === 'changed'
                ? '#9a3412'
                : '#166534'
          }
        >
          {statusLabel}
        </StatusPill>
        <Text>Similarity {formatSimilarity(row.similarity)}</Text>
      </Flex>

      <Grid
        gap={4}
        mt={4}
        templateColumns={{ base: '1fr', md: 'repeat(2, minmax(0, 1fr))' }}
      >
        <Box
          borderWidth="1px"
          borderColor={
            selectedSource === 'original'
              ? 'rgba(15, 118, 110, 0.26)'
              : 'rgba(73, 59, 36, 0.08)'
          }
          borderRadius="16px"
          bg={
            selectedSource === 'original'
              ? 'rgba(236, 253, 245, 0.8)'
              : 'rgba(255,255,255,0.96)'
          }
          p={4}
        >
          <Text color="#6a5a45" fontWeight="700" mb={2}>
            DOIT OCR
          </Text>
          <Text
            lineHeight="1.7"
            minH="64px"
            whiteSpace="pre-wrap"
            wordBreak="break-word"
          >
            {row.originalText || 'No text'}
          </Text>
        </Box>

        <Box
          borderWidth="1px"
          borderColor={
            selectedSource === 'cleaned'
              ? 'rgba(15, 118, 110, 0.26)'
              : 'rgba(73, 59, 36, 0.08)'
          }
          borderRadius="16px"
          bg={
            selectedSource === 'cleaned'
              ? 'rgba(236, 253, 245, 0.8)'
              : 'rgba(255,255,255,0.96)'
          }
          p={4}
        >
          <Text color="#6a5a45" fontWeight="700" mb={2}>
            Cleaned OCR
          </Text>
          <Text
            lineHeight="1.7"
            minH="64px"
            whiteSpace="pre-wrap"
            wordBreak="break-word"
          >
            {row.cleanedText || 'No text'}
          </Text>
        </Box>
      </Grid>
    </Box>
  );
}

function getHighlightStyle(
  bbox: [number, number, number, number],
): CSSProperties {
  const xPadding = 0.008;
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

function buildLineReviewRows(page: ImportPageAsset): ReviewLineRow[] {
  const supportSource =
    page.corrected_markdown ?? page.markdown ?? page.raw_markdown ?? '';
  const supportLines = extractReviewBlocks(supportSource);

  return page.segments.map((segment, index) => {
    const rawText = getRawSegmentText(segment);
    const correctedText = segment.corrected_text?.trim() || null;
    const currentText = getCurrentSegmentText(segment);
    const normalizedRaw = normalizeDiffText(rawText);
    const normalizedCorrected = correctedText
      ? normalizeDiffText(correctedText)
      : '';
    if (
      correctedText &&
      normalizedCorrected &&
      normalizedCorrected !== normalizedRaw
    ) {
      return {
        segment,
        lineNumber: index + 1,
        currentText,
        rawText,
        supportText: correctedText,
        supportLabel: 'Typhoon OCR line',
      };
    }

    const supportText = findSupportLineText(
      rawText,
      supportLines,
      index,
      page.segments.length,
    );
    const normalizedSupport = supportText ? normalizeDiffText(supportText) : '';

    return {
      segment,
      lineNumber: index + 1,
      currentText,
      rawText,
      supportText:
        normalizedSupport && normalizedSupport !== normalizedRaw
          ? supportText
          : null,
      supportLabel:
        normalizedSupport && normalizedSupport !== normalizedRaw
          ? 'Page text'
          : null,
    };
  });
}

function findSupportLineText(
  rawText: string,
  supportLines: string[],
  index: number,
  rowCount: number,
) {
  if (supportLines.length === 0) {
    return null;
  }

  const proportionalIndex = Math.min(
    supportLines.length - 1,
    Math.max(
      0,
      Math.round((index * supportLines.length) / Math.max(rowCount, 1)),
    ),
  );
  const candidateIndexes = new Set<number>([proportionalIndex]);
  for (let offset = 1; offset <= 2; offset += 1) {
    candidateIndexes.add(Math.max(0, proportionalIndex - offset));
    candidateIndexes.add(
      Math.min(supportLines.length - 1, proportionalIndex + offset),
    );
  }

  let bestText = '';
  let bestScore = -1;
  for (const candidateIndex of candidateIndexes) {
    const candidate = supportLines[candidateIndex];
    if (!candidate) {
      continue;
    }

    const similarity = getCharacterSimilarity(rawText, candidate);
    const distancePenalty =
      Math.abs(candidateIndex - proportionalIndex) /
      Math.max(supportLines.length - 1, 1);
    const score = similarity - distancePenalty * 0.08;
    if (score > bestScore) {
      bestScore = score;
      bestText = candidate;
    }
  }

  if (bestText && bestScore >= 0.28) {
    return bestText;
  }

  const fallback = joinSpanBlocks(supportLines, index, rowCount);
  return fallback || null;
}

function buildDiffRows(page: ImportPageAsset): DiffRow[] {
  const originalBlocks = extractReviewBlocks(page.original_markdown);
  const cleanedBlocks = extractReviewBlocks(page.cleaned_markdown);
  const rowCount = Math.max(originalBlocks.length, cleanedBlocks.length);
  if (rowCount === 0) {
    return [];
  }

  const rows: DiffRow[] = [];
  for (let index = 0; index < rowCount; index += 1) {
    const originalText = joinSpanBlocks(originalBlocks, index, rowCount);
    const cleanedText = joinSpanBlocks(cleanedBlocks, index, rowCount);
    if (!originalText && !cleanedText) {
      continue;
    }

    const similarity = getCharacterSimilarity(originalText, cleanedText);
    const status: DiffStatus =
      !originalText || !cleanedText
        ? 'missing'
        : similarity >= 0.985
          ? 'same'
          : 'changed';
    const selectedText =
      page.selected_markdown_source === 'original'
        ? originalText
        : page.selected_markdown_source === 'cleaned'
          ? cleanedText
          : cleanedText || originalText;

    rows.push({
      id: `diff-row-${page.page_number}-${index + 1}`,
      status,
      originalText,
      cleanedText,
      selectedText,
      similarity,
    });
  }

  return rows;
}

function extractReviewBlocks(markdown: string | null) {
  if (!markdown) {
    return [];
  }

  const groups: string[][] = [];
  let currentGroup: string[] = [];
  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('## Page')) {
      if (currentGroup.length > 0) {
        groups.push(currentGroup);
        currentGroup = [];
      }
      continue;
    }

    const lowerLine = line.toLowerCase();
    if (lowerLine.startsWith('<figure') || lowerLine.startsWith('</figure')) {
      continue;
    }
    if (line.startsWith('<') && line.endsWith('>')) {
      continue;
    }

    currentGroup.push(line);
  }

  if (currentGroup.length > 0) {
    groups.push(currentGroup);
  }

  const blocks: string[] = [];
  for (const lines of groups) {
    for (const line of lines) {
      if (line) {
        blocks.push(line);
      }
    }
  }

  return blocks.filter(Boolean);
}

function extractEditableLineEntries(
  markdown: string | null,
): EditableLineEntry[] {
  if (!markdown) {
    return [];
  }

  const entries: EditableLineEntry[] = [];
  let insideFigure = false;
  let sourceIndex = 0;

  for (const [markdownLineIndex, rawLine] of markdown
    .split(/\r?\n/)
    .entries()) {
    const line = rawLine.trim();
    if (!line || line.startsWith('## Page')) {
      continue;
    }

    const lowerLine = line.toLowerCase();
    if (lowerLine.startsWith('<figure')) {
      insideFigure = true;
      continue;
    }
    if (lowerLine.startsWith('</figure')) {
      insideFigure = false;
      continue;
    }
    if (insideFigure) {
      continue;
    }
    if (line.startsWith('<') && line.endsWith('>')) {
      continue;
    }

    entries.push({
      sourceIndex,
      markdownLineIndex,
      line,
      rawLine,
    });
    sourceIndex += 1;
  }

  return entries;
}

function findEditableLineEntry(
  markdown: string | null,
  segment: TextSegment,
): EditableLineEntry | null {
  const entries = extractEditableLineEntries(markdown);
  if (entries.length === 0) {
    return null;
  }

  if (typeof segment.source_line_index === 'number') {
    const directMatch = entries.find(
      (entry) => entry.sourceIndex === segment.source_line_index,
    );
    if (directMatch) {
      return directMatch;
    }
  }

  const referenceTexts = Array.from(
    new Set(
      [
        getCurrentSegmentText(segment),
        getRawSegmentText(segment),
        segment.corrected_text?.trim() ?? '',
        segment.text?.trim() ?? '',
      ].filter((value) => Boolean(value.trim())),
    ),
  );

  let bestEntry: EditableLineEntry | null = null;
  let bestScore = -1;

  for (const entry of entries) {
    let score = 0;
    for (const referenceText of referenceTexts) {
      const similarity = getCharacterSimilarity(referenceText, entry.line);
      if (similarity > score) {
        score = similarity;
      }
    }

    if (typeof segment.source_line_index === 'number') {
      const distance = Math.abs(entry.sourceIndex - segment.source_line_index);
      score += Math.max(0, 0.18 - distance * 0.06);
    }

    if (score > bestScore) {
      bestScore = score;
      bestEntry = entry;
    }
  }

  return bestEntry;
}

function getEditableLineText(markdown: string | null, segment: TextSegment) {
  const entry = findEditableLineEntry(markdown, segment);
  return entry?.line ?? getCurrentSegmentText(segment);
}

function applySegmentEditToMarkdown(
  markdown: string | null,
  segment: TextSegment,
  replacement: string,
) {
  const nextLine = replacement.trim();
  if (!nextLine) {
    return markdown ?? '';
  }
  if (!markdown) {
    return nextLine;
  }

  const entry = findEditableLineEntry(markdown, segment);
  if (!entry) {
    return markdown;
  }

  const lines = markdown.split(/\r?\n/);
  const originalLine = lines[entry.markdownLineIndex] ?? entry.rawLine;
  const leadingWhitespace = originalLine.match(/^\s*/)?.[0] ?? '';
  const trailingWhitespace = originalLine.match(/\s*$/)?.[0] ?? '';
  lines[entry.markdownLineIndex] =
    `${leadingWhitespace}${nextLine}${trailingWhitespace}`;
  return lines.join('\n');
}

function prefersOwnReviewBlock(line: string) {
  const compactLength = normalizedTextLength(line);
  if (compactLength <= 10 || line.endsWith(':')) {
    return true;
  }

  const compactLine = normalizeDiffText(line);
  return [
    'เนเธเธ—เธเน',
    'เธเธณเน€เธฅเธข',
    'เธฃเธฐเธซเธงเนเธฒเธ',
    'เธฃเธฒเธขเธฅเธฐเน€เธญเธตเธขเธ”',
    'เธเธงเธฒเธกเนเธเนเธ',
    'เน€เธฃเธทเนเธญเธ',
    'เธเธณเธเธญ',
  ].includes(compactLine);
}

function normalizedTextLength(text: string) {
  return normalizeDiffText(text).length;
}

function joinSpanBlocks(blocks: string[], index: number, rowCount: number) {
  if (blocks.length === 0 || rowCount <= 0) {
    return '';
  }

  const start = Math.round((index * blocks.length) / rowCount);
  const end = Math.max(
    start + 1,
    Math.round(((index + 1) * blocks.length) / rowCount),
  );
  return blocks.slice(start, Math.min(end, blocks.length)).join(' ').trim();
}

function normalizeDiffText(text: string) {
  return text.replace(/\s+/g, '').trim().toLowerCase();
}

function getCurrentSegmentText(segment: TextSegment) {
  return (
    segment.corrected_text?.trim() ||
    segment.text?.trim() ||
    segment.raw_text?.trim() ||
    ''
  );
}

function getRawSegmentText(segment: TextSegment) {
  return segment.raw_text?.trim() || segment.text?.trim() || '';
}

function getCharacterSimilarity(left: string, right: string) {
  const normalizedLeft = normalizeDiffText(left);
  const normalizedRight = normalizeDiffText(right);
  if (!normalizedLeft && !normalizedRight) {
    return 1;
  }
  if (!normalizedLeft || !normalizedRight) {
    return 0;
  }
  if (normalizedLeft === normalizedRight) {
    return 1;
  }

  const leftBigrams = buildCharacterBigrams(normalizedLeft);
  const rightBigrams = buildCharacterBigrams(normalizedRight);
  const union = new Set([...leftBigrams, ...rightBigrams]);
  if (union.size === 0) {
    return 0;
  }

  let intersection = 0;
  for (const gram of leftBigrams) {
    if (rightBigrams.has(gram)) {
      intersection += 1;
    }
  }

  return intersection / union.size;
}

function buildCharacterBigrams(value: string) {
  const grams = new Set<string>();
  if (value.length <= 2) {
    grams.add(value);
    return grams;
  }

  for (let index = 0; index < value.length - 1; index += 1) {
    grams.add(value.slice(index, index + 2));
  }
  return grams;
}

function getSelectedSourceLabel(
  source: 'original' | 'cleaned' | 'manual' | null,
) {
  if (source === 'original') {
    return 'Original OCR';
  }
  if (source === 'cleaned') {
    return 'Cleaned OCR';
  }
  if (source === 'manual') {
    return 'Manual edit';
  }
  return 'Not selected';
}

function formatSimilarity(value: number | null) {
  if (value === null || Number.isNaN(value)) {
    return 'N/A';
  }

  return `${Math.round(value * 100)}%`;
}
