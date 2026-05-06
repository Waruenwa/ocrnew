'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Alert, Button, ConfigProvider, Typography } from 'antd';
import { FiArrowLeft } from 'react-icons/fi';

import { API_BASE_URL } from '../lib/review';
import { DOCUMENT_CATEGORIES } from '../lib/document-categories';

const MAX_UPLOAD_FILES = 10;
const MAX_UPLOAD_MB = 50;
const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;
const ACCEPTED_EXTENSIONS = new Set(['.pdf', '.png', '.jpg', '.jpeg']);

const shellStyle = {
  maxWidth: 1000,
  margin: '0 auto',
  padding: '28px 20px',
} as const;

const panelStyle = {
  border: '1px solid rgba(73, 59, 36, 0.12)',
  borderRadius: 28,
  background: 'rgba(255, 255, 255, 0.9)',
  boxShadow: '0 18px 42px rgba(31, 26, 20, 0.08)',
  padding: 24,
} as const;

const uploadPanelStyle = {
  border: '1px solid rgba(73, 59, 36, 0.08)',
  borderRadius: 24,
  background: 'rgba(251, 248, 242, 0.92)',
  padding: 24,
} as const;

export default function UploadFilePage() {
  const router = useRouter();
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [selectedDocumentCategory, setSelectedDocumentCategory] = useState<string>(
    DOCUMENT_CATEGORIES[0].value,
  );
  const [isUploading, setIsUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<{
    type: 'success' | 'error';
    text: string;
  } | null>(null);

  function validateFiles(files: File[]): string | null {
    if (files.length === 0) {
      return 'Please select at least one file before uploading.';
    }
    if (files.length > MAX_UPLOAD_FILES) {
      return `Please select no more than ${MAX_UPLOAD_FILES} files.`;
    }

    for (const file of files) {
      const extension = file.name
        .slice(file.name.lastIndexOf('.'))
        .toLowerCase();
      if (!ACCEPTED_EXTENSIONS.has(extension)) {
        return `Unsupported file type: ${file.name}`;
      }
      if (file.size > MAX_UPLOAD_BYTES) {
        return `${file.name} is larger than ${MAX_UPLOAD_MB} MB.`;
      }
    }

    return null;
  }

  function handleFileSelection(files: FileList | null) {
    const nextFiles = Array.from(files ?? []);
    const validationError = validateFiles(nextFiles);
    if (validationError) {
      setSelectedFiles([]);
      setUploadMessage({
        type: 'error',
        text: validationError,
      });
      return;
    }

    setSelectedFiles(nextFiles);
    setUploadMessage(null);
  }

  async function handleUpload() {
    const validationError = validateFiles(selectedFiles);
    if (validationError) {
      setUploadMessage({
        type: 'error',
        text: validationError,
      });
      return;
    }

    setUploadMessage(null);
    setIsUploading(true);

    try {
      for (let index = 0; index < selectedFiles.length; index += 1) {
        const file = selectedFiles[index];
        setUploadMessage({
          type: 'success',
          text: `Uploading ${index + 1}/${selectedFiles.length}: ${file.name}`,
        });

        const formData = new FormData();
        formData.append('file', file);
        formData.append('document_category', selectedDocumentCategory);

        const response = await fetch(`${API_BASE_URL}/api/imports/upload`, {
          method: 'POST',
          body: formData,
        });
        if (!response.ok) {
          const payload = (await response.json().catch(() => null)) as {
            detail?: string;
          } | null;
          throw new Error(payload?.detail || `Unable to upload ${file.name}.`);
        }
      }

      router.push('/');
    } catch (error) {
      setUploadMessage({
        type: 'error',
        text: error instanceof Error ? error.message : 'Unable to upload this file.',
      });
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <ConfigProvider
      theme={{
        token: {
          colorText: '#241d17',
          colorBgContainer: '#ffffff',
          colorBorder: 'rgba(73, 59, 36, 0.08)',
          borderRadius: 20,
          fontSize: 16,
        },
        components: {
          Button: {
            defaultShadow: 'none',
            primaryShadow: 'none',
          },
        },
      }}
    >
      <main style={shellStyle}>
        <section style={panelStyle}>
          <div style={{ marginBottom: 16 }}>
            <Link href="/" style={{ textDecoration: 'none' }}>
              <Button
                icon={<FiArrowLeft size={14} />}
                style={{
                  borderRadius: 999,
                  borderColor: 'rgba(73, 59, 36, 0.18)',
                  color: '#6a5a45',
                  background: '#fff',
                }}
              >
                Back To Queue
              </Button>
            </Link>
          </div>

          <div style={uploadPanelStyle}>
            {uploadMessage ? (
              <Alert
                showIcon
                title={uploadMessage.text}
                style={{ marginBottom: 16 }}
                type={uploadMessage.type}
              />
            ) : null}

            <div
              style={{
                display: 'flex',
                flexWrap: 'wrap',
                gap: 14,
                alignItems: 'end',
                justifyContent: 'space-between',
              }}
            >
              <div style={{ display: 'grid', gap: 10 }}>
                <Typography.Text
                  style={{
                    fontSize: '1.15rem',
                    fontWeight: 700,
                    color: '#241d17',
                  }}
                >
                  Upload document
                </Typography.Text>
                <Typography.Text
                  style={{
                    color: '#6a5a45',
                    fontSize: '0.98rem',
                  }}
                >
                  The system stores source files in `original` and creates OCR assets
                  in `derived`. Select up to {MAX_UPLOAD_FILES} files, {MAX_UPLOAD_MB} MB each.
                </Typography.Text>
                <input
                  accept=".pdf,.png,.jpg,.jpeg"
                  onChange={(event) => {
                    handleFileSelection(event.target.files);
                  }}
                  style={{
                    maxWidth: 360,
                    color: '#241d17',
                  }}
                  multiple
                  type="file"
                />
                {selectedFiles.length > 0 ? (
                  <div
                    style={{
                      display: 'grid',
                      gap: 4,
                      maxWidth: 520,
                    }}
                  >
                    {selectedFiles.map((file) => (
                      <Typography.Text
                        key={`${file.name}-${file.size}-${file.lastModified}`}
                        style={{
                          color: '#6a5a45',
                          fontSize: '0.88rem',
                        }}
                      >
                        {file.name} ({(file.size / (1024 * 1024)).toFixed(1)} MB)
                      </Typography.Text>
                    ))}
                  </div>
                ) : null}
                <select
                  value={selectedDocumentCategory}
                  onChange={(event) => {
                    setSelectedDocumentCategory(event.target.value);
                  }}
                  style={{
                    maxWidth: 260,
                    borderRadius: 10,
                    border: '1px solid rgba(73, 59, 36, 0.2)',
                    background: 'white',
                    color: '#241d17',
                    padding: '9px 10px',
                    fontSize: '0.96rem',
                  }}
                >
                  {DOCUMENT_CATEGORIES.map((category) => (
                    <option key={category.value} value={category.value}>
                      {category.label}
                    </option>
                  ))}
                </select>
              </div>

              <Button
                disabled={selectedFiles.length === 0}
                loading={isUploading}
                onClick={() => void handleUpload()}
                size="large"
                style={{
                  borderRadius: 999,
                  height: 48,
                  paddingInline: 24,
                  borderColor: 'rgba(15, 118, 110, 0.18)',
                  color: '#115e59',
                  background: 'rgba(15, 118, 110, 0.08)',
                  boxShadow: 'none',
                }}
              >
                Upload {selectedFiles.length > 1 ? 'documents' : 'document'}
              </Button>
            </div>
          </div>
        </section>
      </main>
    </ConfigProvider>
  );
}
