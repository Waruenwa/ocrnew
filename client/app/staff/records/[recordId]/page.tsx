"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Box, Flex, Grid, Image, Spinner, Stack, Text, Textarea } from "@chakra-ui/react";
import { Alert, Button, Tag } from "antd";

import { getAuthHeaders, getCurrentUser } from "../../../lib/auth";
import { API_BASE_URL } from "../../../lib/review";

type StaffRecordDetail = {
  record_id: string;
  batch_id: string;
  file_id: string;
  original_filename: string;
  selected_document_type: string;
  page_number: number;
  ocr_status: string;
  review_status: string;
  ocr_text: string | null;
  ocr_result: string | null;
  corrected_result: unknown;
  preview_url: string;
  assigned_at: string | null;
  completed_at: string | null;
};

const panelStyles = {
  bg: "rgba(255, 252, 246, 0.96)",
  borderWidth: "1px",
  borderColor: "rgba(73, 59, 36, 0.08)",
  borderRadius: "28px",
  boxShadow: "0 18px 60px rgba(64, 50, 31, 0.12)",
};

function stringifyCorrection(value: unknown, fallback: string) {
  if (typeof value === "string") {
    return value;
  }
  if (value && typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return fallback;
}

function statusColor(status: string) {
  if (status === "completed" || status === "succeeded") {
    return "green";
  }
  if (status === "failed") {
    return "red";
  }
  if (status === "in_review" || status === "processing") {
    return "blue";
  }
  return "default";
}

export default function StaffRecordReviewPage() {
  const params = useParams<{ recordId: string }>();
  const router = useRouter();
  const recordId = params.recordId;
  const [record, setRecord] = useState<StaffRecordDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isCheckingAccess, setIsCheckingAccess] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isCompleting, setIsCompleting] = useState(false);

  const ocrText = useMemo(() => record?.ocr_result || record?.ocr_text || "", [record]);
  const previewUrl = record ? `${API_BASE_URL}${record.preview_url}` : "";

  async function loadRecord() {
    if (!recordId) {
      return;
    }
    setIsLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/staff/records/${recordId}`, {
        headers: getAuthHeaders(),
      });
      const payload = (await response.json().catch(() => null)) as
        | StaffRecordDetail
        | { detail?: unknown }
        | null;
      if (!response.ok) {
        setError(String((payload as { detail?: unknown } | null)?.detail || "Unable to load assigned record."));
        return;
      }
      const nextRecord = payload as StaffRecordDetail;
      setRecord(nextRecord);
      setDraft(
        stringifyCorrection(
          nextRecord.corrected_result,
          nextRecord.ocr_result || nextRecord.ocr_text || "",
        ),
      );
      setError(null);
    } catch {
      setError(`Unable to reach the backend API at ${API_BASE_URL}.`);
    } finally {
      setIsLoading(false);
    }
  }

  async function saveProgress() {
    setIsSaving(true);
    setMessage(null);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/staff/records/${recordId}/progress`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders(),
        },
        body: JSON.stringify({ corrected_result: draft }),
      });
      const payload = (await response.json().catch(() => null)) as
        | StaffRecordDetail
        | { detail?: unknown }
        | null;
      if (!response.ok) {
        setError(String((payload as { detail?: unknown } | null)?.detail || "Unable to save progress."));
        return;
      }
      setRecord(payload as StaffRecordDetail);
      setMessage("Progress saved.");
    } catch {
      setError(`Unable to reach the backend API at ${API_BASE_URL}.`);
    } finally {
      setIsSaving(false);
    }
  }

  async function markCompleted() {
    setIsCompleting(true);
    setMessage(null);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/staff/records/${recordId}/complete`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders(),
        },
        body: JSON.stringify({ corrected_result: draft }),
      });
      const payload = (await response.json().catch(() => null)) as
        | StaffRecordDetail
        | { detail?: unknown }
        | null;
      if (!response.ok) {
        setError(String((payload as { detail?: unknown } | null)?.detail || "Unable to complete record."));
        return;
      }
      setRecord(payload as StaffRecordDetail);
      setMessage("Record completed.");
    } catch {
      setError(`Unable to reach the backend API at ${API_BASE_URL}.`);
    } finally {
      setIsCompleting(false);
    }
  }

  useEffect(() => {
    let mounted = true;
    async function checkAccessAndLoad() {
      const currentUser = await getCurrentUser();
      if (!mounted) {
        return;
      }
      if (!currentUser) {
        router.replace("/login");
        return;
      }
      if (currentUser.role !== "staff") {
        router.replace("/manager");
        return;
      }
      setIsCheckingAccess(false);
      void loadRecord();
    }
    void checkAccessAndLoad();
    return () => {
      mounted = false;
    };
  }, [recordId, router]);

  if (isCheckingAccess) {
    return (
      <Flex align="center" justify="center" minH="100vh">
        <Spinner color="#115e59" />
      </Flex>
    );
  }

  return (
    <Box as="main" maxW="1880px" mx="auto" px={{ base: 2, md: 3, xl: 4 }} py={{ base: 3, md: 4 }}>
      <Grid gap={{ base: 3, xl: 4 }} templateColumns={{ base: "1fr", xl: "repeat(2, minmax(0, 1fr))" }}>
        <Box
          {...panelStyles}
          p={{ base: 3, md: 4 }}
          display="flex"
          flexDirection="column"
          gap={4}
          h={{ xl: "calc(100vh - 24px)" }}
          overflow={{ xl: "auto" }}
          position={{ xl: "sticky" }}
          top={{ xl: "12px" }}
        >
          <Flex align="center" justify="space-between" gap={3} wrap="wrap">
            <Link href="/staff" style={{ textDecoration: "none" }}>
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
                Back to staff tasks
              </Box>
            </Link>
            {record ? (
              <Text color="#6a5a45" fontWeight="700">
                {record.original_filename} page {record.page_number}
              </Text>
            ) : null}
          </Flex>

          <Box
            borderWidth="1px"
            borderColor="rgba(73, 59, 36, 0.08)"
            borderRadius="24px"
            bg="white"
            minH={{ base: "52vh", xl: "78vh" }}
            overflow="auto"
            p={{ base: 2, md: 3 }}
          >
            {isLoading ? (
              <Flex align="center" gap={3}>
                <Spinner color="#115e59" size="sm" />
                <Text color="#6a5a45">Loading assigned record...</Text>
              </Flex>
            ) : previewUrl ? (
              <Image alt="Assigned record preview" borderRadius="16px" display="block" src={previewUrl} w="full" />
            ) : (
              <Text color="#6a5a45">No preview is available.</Text>
            )}
          </Box>
        </Box>

        <Box
          {...panelStyles}
          p={{ base: 3, md: 4 }}
          display="flex"
          flexDirection="column"
          gap={4}
          h={{ xl: "calc(100vh - 24px)" }}
          overflow={{ xl: "auto" }}
          position={{ xl: "sticky" }}
          top={{ xl: "12px" }}
        >
          <Flex align="start" gap={4} justify="space-between" wrap="wrap">
            <Box>
              <Text
                bg="rgba(15, 118, 110, 0.08)"
                color="#115e59"
                borderRadius="full"
                display="inline-flex"
                px={3}
                py={1}
                fontSize="0.84rem"
                fontWeight="700"
              >
                OCR Review
              </Text>
            </Box>
            {record ? (
              <Flex gap={2} wrap="wrap">
                <Tag color={statusColor(record.ocr_status)}>{record.ocr_status}</Tag>
                <Tag color={statusColor(record.review_status)}>{record.review_status}</Tag>
              </Flex>
            ) : null}
          </Flex>

          {error ? <Alert showIcon type="error" title={error} /> : null}
          {message ? <Alert showIcon type="success" title={message} /> : null}

          {record ? (
            <Stack gap={4}>
              <Box borderWidth="1px" borderColor="rgba(73, 59, 36, 0.08)" borderRadius="20px" bg="white" p={4}>
                <Text color="#6a5a45" fontSize="0.9rem" fontWeight="700" mb={2}>
                  OCR Result
                </Text>
                <Box
                  as="pre"
                  whiteSpace="pre-wrap"
                  color="#1f2937"
                  fontSize="0.95rem"
                  maxH="26vh"
                  overflow="auto"
                  m={0}
                >
                  {ocrText || "OCR result is empty."}
                </Box>
              </Box>

              <Box borderWidth="1px" borderColor="rgba(73, 59, 36, 0.08)" borderRadius="20px" bg="white" p={4}>
                <Text color="#6a5a45" fontSize="0.9rem" fontWeight="700" mb={2}>
                  Correction
                </Text>
                <Textarea
                  minH="36vh"
                  value={draft}
                  onChange={(event) => {
                    setDraft(event.target.value);
                    setMessage(null);
                  }}
                  disabled={record.review_status === "completed"}
                />
              </Box>

              <Flex gap={3} justify="flex-end" wrap="wrap">
                <Button
                  disabled={record.review_status === "completed"}
                  loading={isSaving}
                  onClick={() => void saveProgress()}
                >
                  Save Progress
                </Button>
                <Button
                  type="primary"
                  disabled={record.review_status === "completed"}
                  loading={isCompleting}
                  onClick={() => void markCompleted()}
                >
                  Mark Completed
                </Button>
              </Flex>
            </Stack>
          ) : null}
        </Box>
      </Grid>
    </Box>
  );
}
