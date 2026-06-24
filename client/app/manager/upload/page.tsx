"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Alert, Button, Card, Typography, ConfigProvider } from "antd";
import { FiArrowLeft, FiFileText, FiUploadCloud, FiX, FiCheckCircle, FiInfo, FiLayers } from "react-icons/fi";
import { Box, Flex, Grid, HStack, Center, Text as ChakraText, VStack, IconButton } from '@chakra-ui/react';

import { ProtectedRolePage } from "../../auth/protected-role-page";
import { getAuthHeaders } from "../../lib/auth";
import { API_BASE_URL } from "../../lib/review";

const { Text, Title } = Typography;

const MAX_FILES_PER_BATCH = 20;
const MAX_PAGES_PER_PDF = 50;
const MAX_RECORDS_PER_BATCH = 100;
const SUPPORTED_DOCUMENT_TYPE = "TR";

type UploadMessage = {
  type: "success" | "error" | "info";
  text: string;
};

type UploadResult = {
  batch_id: string;
  status: string;
  selected_document_type: string;
  file_count: number;
  total_pages: number;
  record_count: number;
  ocr_pending_count: number;
  ocr_processing_count: number;
  ocr_succeeded_count: number;
  ocr_failed_count: number;
  ready_to_assign_count: number;
  files: Array<{
    file_id: string;
    original_filename: string;
    page_count: number | null;
    status: string;
    error_message: string | null;
  }>;
};

function isPdfFile(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function validateFiles(files: File[]) {
  if (files.length > MAX_FILES_PER_BATCH) {
    return `เลือกไฟล์ได้ไม่เกิน ${MAX_FILES_PER_BATCH} ไฟล์ต่อครั้งครับ`;
  }
  const nonPdfFile = files.find((file) => !isPdfFile(file));
  if (nonPdfFile) {
    return `รองรับเฉพาะไฟล์ PDF เท่านั้นครับ (ไฟล์ "${nonPdfFile.name}" ไม่ถูกต้อง)`;
  }
  return null;
}

function formatUploadError(payload: unknown) {
  if (!payload || typeof payload !== "object") {
    return "Upload failed.";
  }

  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg?: unknown }).msg || "");
        }
        return "";
      })
      .filter(Boolean)
      .join(", ") || "Upload failed.";
  }
  return "Upload failed.";
}

export default function ManagerUploadPage() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const router = useRouter();
  const [documentType] = useState<string>(SUPPORTED_DOCUMENT_TYPE);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [message, setMessage] = useState<UploadMessage | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const fileValidationError = useMemo(() => validateFiles(selectedFiles), [selectedFiles]);
  const submitDisabled = selectedFiles.length === 0 || Boolean(fileValidationError) || isUploading;

  function handleFileSelection(fileList: FileList | null) {
    const nextFiles = Array.from(fileList ?? []);
    const error = validateFiles(nextFiles);
    setSelectedFiles(nextFiles);
    setValidationError(error);
    setMessage(null);
  }

  const removeFile = (index: number) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== index));
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  async function handleSubmit() {
    if (selectedFiles.length === 0) return;
    setIsUploading(true);
    const formData = new FormData();
    formData.append("selected_document_type", documentType);
    selectedFiles.forEach((file) => formData.append("files", file));

    try {
      const response = await fetch(`${API_BASE_URL}/api/manager/uploads`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok) {
        setMessage({ type: "error", text: formatUploadError(payload) });
        return;
      }
      const limitErrorFile = (payload as UploadResult)?.files?.find(
        (file) => file.status === "page_limit_exceeded" || file.error_message,
      );
      if (limitErrorFile) {
        setMessage({
          type: "error",
          text:
            limitErrorFile.error_message ||
            `Record limit exceeded: ${limitErrorFile.original_filename} has too many pages.`,
        });
        return;
      }
      router.push("/manager");
    } catch {
      setMessage({ type: "error", text: "Unable to reach the backend API." });
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <ProtectedRolePage
      allowedRole="manager"
      eyebrow="Manager Upload"
      title="Upload TR Documents"
      contentMaxW="min(1200px, calc(100vw - 80px))"
      stats={[]}
    >
      <ConfigProvider theme={{ token: { borderRadius: 10, fontFamily: 'inherit' } }}>
        <Flex direction="column" gap="24px">
          
          {/* Metrics Row */}
          <Grid templateColumns="repeat(3, 1fr)" gap="16px">
            <MetricCard 
              title="Document Type" 
              value="ทร (TR)" 
              color="#136360" 
              bg="rgba(19, 99, 96, 0.1)"
              iconColor="#136360"
              iconType="type"
            />
            <MetricCard 
              title="Selected Files" 
              value={selectedFiles.length} 
              color={selectedFiles.length > MAX_FILES_PER_BATCH ? "#ef4444" : "#3b82f6"} 
              bg={selectedFiles.length > MAX_FILES_PER_BATCH ? "#fef2f2" : "#eff6ff"}
              iconColor={selectedFiles.length > MAX_FILES_PER_BATCH ? "#dc2626" : "#2563eb"}
              iconType="files"
            />
            <MetricCard 
              title="Maximum Records" 
              value={MAX_RECORDS_PER_BATCH} 
              color="#64748b" 
              bg="#f1f5f9"
              iconColor="#475569"
              iconType="limit"
            />
          </Grid>

          {/* Upload Card */}
          <Box bg="white" borderRadius="24px" p="32px" border="1px solid rgba(226, 232, 240, 0.6)" boxShadow="0 10px 40px rgba(0, 0, 0, 0.02)">
            <VStack gap="24px" align="stretch">
              
              {/* Back Button & Header */}
              <Flex justify="space-between" align="center">
                <Link href="/manager">
                  <Button icon={<FiArrowLeft />} style={{ borderRadius: '10px', fontWeight: 600 }}>Back to Manager</Button>
                </Link>
                <HStack color="#64748b" fontSize="0.85rem">
                  <FiInfo />
                  <Text>Phase 1 supports TR documents only.</Text>
                </HStack>
              </Flex>

              {(validationError || fileValidationError) && (
                <Alert showIcon type="error" title={validationError || fileValidationError} />
              )}
              {message && <Alert showIcon type={message.type} title={message.text} />}

              {/* Modern Dropzone */}
              <Box position="relative">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf"
                  multiple
                  onChange={(event) => handleFileSelection(event.target.files)}
                  style={{ display: 'none' }}
                  id="file-upload"
                />
                <label htmlFor="file-upload" style={{ cursor: 'pointer', display: 'block' }}>
                  <Center 
                    w="100%" 
                    h="200px" 
                    border="2px dashed" 
                    borderColor="rgba(19, 99, 96, 0.2)" 
                    borderRadius="20px" 
                    bg="rgba(19, 99, 96, 0.02)"
                    transition="all 0.2s"
                    _hover={{ bg: 'rgba(19, 99, 96, 0.05)', borderColor: '#136360' }}
                  >
                    <VStack gap="12px">
                      <Center w="64px" h="64px" borderRadius="full" bg="rgba(19, 99, 96, 0.1)" color="#136360">
                        <FiUploadCloud size={32} />
                      </Center>
                      <VStack gap="4px">
                        <Text style={{ fontSize: '1.1rem', fontWeight: 800, color: '#0f172a', margin: 0 }}>Click to select PDF files</Text>
                        <Text style={{ color: '#64748b', fontSize: '0.9rem' }}>
                          Up to {MAX_FILES_PER_BATCH} files, {MAX_PAGES_PER_PDF} pages per PDF, {MAX_RECORDS_PER_BATCH} records total
                        </Text>
                      </VStack>
                    </VStack>
                  </Center>
                </label>
              </Box>

              {/* Selected Files Grid */}
              {selectedFiles.length > 0 && (
                <Box mt="8px">
                  <Title level={5} style={{ marginBottom: '16px', color: '#1e293b' }}>Selected Files ({selectedFiles.length})</Title>
                  <Grid templateColumns="repeat(auto-fill, minmax(240px, 1fr))" gap="12px">
                    {selectedFiles.map((file, idx) => (
                      <Flex 
                        key={`${file.name}-${idx}`} 
                        bg="#f8fafc" 
                        p="12px" 
                        borderRadius="12px" 
                        border="1px solid #e2e8f0"
                        align="center"
                        justify="space-between"
                      >
                        <HStack gap="10px" overflow="hidden">
                          <Center minW="32px" h="32px" borderRadius="8px" bg="white" border="1px solid #e2e8f0" color="#136360">
                            <FiFileText size={16} />
                          </Center>
                          <VStack align="start" gap="0" overflow="hidden">
                            <Text style={{ fontSize: '0.85rem', fontWeight: 700, margin: 0, width: '100%', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                              {file.name}
                            </Text>
                            <Text style={{ fontSize: '0.75rem', color: '#64748b', margin: 0 }}>
                              {(file.size / 1024 / 1024).toFixed(2)} MB
                            </Text>
                          </VStack>
                        </HStack>
                        <IconButton
                          aria-label="Remove file"
                          size="xs"
                          variant="ghost"
                          colorPalette="red"
                          onClick={() => removeFile(idx)}
                          borderRadius="full"
                        >
                          <FiX size={14} />
                        </IconButton>
                      </Flex>
                    ))}
                  </Grid>
                </Box>
              )}

              {/* Final Actions */}
              <Flex gap="12px" justify="flex-end" pt="16px" borderTop="1px solid #f1f5f9">
                <Button 
                  onClick={() => setSelectedFiles([])} 
                  disabled={selectedFiles.length === 0 || isUploading}
                  style={{ borderRadius: '10px', height: '44px', paddingInline: '24px', fontWeight: 600 }}
                >
                  Clear All
                </Button>
                <Button
                  type="primary"
                  icon={<FiCheckCircle />}
                  disabled={submitDisabled}
                  loading={isUploading}
                  onClick={handleSubmit}
                  style={{ 
                    borderRadius: '10px', 
                    height: '44px', 
                    padding: '0 32px', 
                    fontWeight: 700,
                    color: '#FFF',
                    background: 'linear-gradient(135deg, #136360 0%, #0d4a48 100%)',
                    border: 'none',
                    boxShadow: '0 4px 14px rgba(19, 99, 96, 0.3)'
                  }}
                >
                  Submit TR Upload
                </Button>
              </Flex>
            </VStack>
          </Box>
        </Flex>
      </ConfigProvider>
    </ProtectedRolePage>
  );
}

function MetricCard({ title, value, color, bg, iconColor, iconType }: { title: string, value: string | number, color: string, bg: string, iconColor: string, iconType: 'type' | 'files' | 'limit' }) {
  let Icon = FiLayers;
  if (iconType === 'files') Icon = FiFileText;
  else if (iconType === 'limit') Icon = FiUploadCloud;

  return (
    <Box 
      bg="white" 
      p="16px 20px" 
      borderRadius="16px" 
      border="1px solid rgba(226, 232, 240, 0.8)"
      boxShadow="0 4px 12px rgba(0,0,0,0.02)"
    >
      <Flex justify="space-between" align="center">
        <Box>
          <ChakraText color="#64748b" fontSize="0.7rem" fontWeight="800" textTransform="uppercase" letterSpacing="0.05em" mb="4px">{title}</ChakraText>
          <ChakraText fontSize="1.8rem" lineHeight="1" color="#0f172a" fontWeight="800" letterSpacing="-0.02em">{value}</ChakraText>
        </Box>
        <Center w="36px" h="36px" borderRadius="10px" bg={bg} color={iconColor}>
          <Icon size={18} />
        </Center>
      </Flex>
    </Box>
  );
}
