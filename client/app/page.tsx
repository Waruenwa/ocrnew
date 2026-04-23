'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  ConfigProvider,
  Empty,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { TableProps } from 'antd';
import { FiEye, FiUpload } from 'react-icons/fi';

import {
  API_BASE_URL,
  formatDate,
  type ImportRecord,
  importStatusLabels,
} from './lib/review';
import {
  CATEGORY_FILTER_OPTIONS,
  formatDocumentCategoryLabel,
} from './lib/document-categories';

const shellStyle = {
  maxWidth: 1540,
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

const controlsStyle = {
  border: '1px solid rgba(73, 59, 36, 0.08)',
  borderRadius: 20,
  background: 'rgba(251, 248, 242, 0.92)',
  padding: 16,
  marginBottom: 20,
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'end',
  gap: 16,
  flexWrap: 'wrap',
} as const;

const tableWrapStyle = {
  overflow: 'hidden',
  border: '1px solid rgba(73, 59, 36, 0.08)',
  borderRadius: 24,
  background: '#ffffff',
} as const;

export default function Home() {
  const [imports, setImports] = useState<ImportRecord[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedCategoryFilter, setSelectedCategoryFilter] = useState<string>('all');

  useEffect(() => {
    void loadQueue();
  }, [selectedCategoryFilter]);

  useEffect(() => {
    if (!imports.some(isImportWorking)) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void fetchImports().catch((error) => {
        setErrorMessage(
          error instanceof Error
            ? error.message
            : 'Unable to refresh the review queue.',
        );
      });
    }, 4000);

    return () => window.clearInterval(intervalId);
  }, [imports, selectedCategoryFilter]);

  async function loadQueue() {
    setErrorMessage(null);
    setIsLoading(true);

    try {
      await fetchImports();
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : 'Unable to load the review queue.',
      );
    } finally {
      setIsLoading(false);
    }
  }

  async function fetchImports() {
    const searchParams = new URLSearchParams();
    if (selectedCategoryFilter !== 'all') {
      searchParams.set('category', selectedCategoryFilter);
    }
    const queryString = searchParams.toString();
    const requestUrl = `${API_BASE_URL}/api/imports${
      queryString ? `?${queryString}` : ''
    }`;

    const response = await fetch(requestUrl, {
      cache: 'no-store',
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as {
        detail?: string;
      } | null;
      throw new Error(payload?.detail || 'Unable to load review records.');
    }

    const data = (await response.json()) as ImportRecord[];
    setImports(data);
  }

  const columns = useMemo<NonNullable<TableProps<ImportRecord>['columns']>>(
    () => [
      {
        title: 'Document',
        dataIndex: 'source_filename',
        key: 'document',
        width: '46%',
        onHeaderCell: () => ({
          style: {
            color: '#6a5a45',
            fontSize: '0.92rem',
            fontWeight: 500,
            letterSpacing: '0.04em',
            textTransform: 'uppercase' as const,
          },
        }),
        render: (_, record) => (
          <div style={{ display: 'grid', gap: 14 }}>
            <Typography.Text
              style={{
                fontSize: '2rem',
                lineHeight: 1.12,
                fontWeight: 800,
                color: '#241d17',
              }}
            >
              {record.source_filename}
            </Typography.Text>
            <Typography.Text
              style={{
                color: '#6a5a45',
                fontSize: '1.02rem',
                lineHeight: 1.6,
                wordBreak: 'break-word',
              }}
            >
              {record.source_path}
            </Typography.Text>
          </div>
        ),
      },
      {
        title: 'Status',
        dataIndex: 'status',
        key: 'status',
        width: 190,
        onHeaderCell: () => ({
          style: {
            color: '#6a5a45',
            fontSize: '0.92rem',
            fontWeight: 500,
            letterSpacing: '0.04em',
            textTransform: 'uppercase' as const,
          },
        }),
        render: (status: ImportRecord['status']) => {
          const badge = getImportStatusBadge(status);
          return (
            <Tag
              variant="filled"
              style={{
                marginInlineEnd: 0,
                borderRadius: 999,
                padding: '8px 18px',
                fontSize: '0.98rem',
                fontWeight: 700,
                color: badge.color,
                background: badge.bg,
              }}
            >
              {importStatusLabels[status]}
            </Tag>
          );
        },
      },
      {
        title: 'Category',
        dataIndex: 'document_category',
        key: 'document_category',
        width: 160,
        onHeaderCell: () => ({
          style: {
            color: '#6a5a45',
            fontSize: '0.92rem',
            fontWeight: 500,
            letterSpacing: '0.04em',
            textTransform: 'uppercase' as const,
          },
        }),
        render: (value: ImportRecord['document_category']) => (
          <Typography.Text style={{ fontSize: '0.96rem', color: '#3f3327' }}>
            {formatDocumentCategoryLabel(value)}
          </Typography.Text>
        ),
      },
      {
        title: 'Pages',
        dataIndex: 'total_pages',
        key: 'pages',
        width: 110,
        align: 'center',
        onHeaderCell: () => ({
          style: {
            color: '#6a5a45',
            fontSize: '0.92rem',
            fontWeight: 500,
            letterSpacing: '0.04em',
            textTransform: 'uppercase' as const,
            textAlign: 'center' as const,
          },
        }),
        render: (value: number) => (
          <Typography.Text
            style={{ fontSize: '1.1rem', fontWeight: 700, color: '#241d17' }}
          >
            {value}
          </Typography.Text>
        ),
      },
      {
        title: 'OCR',
        key: 'ocr',
        width: 170,
        onHeaderCell: () => ({
          style: {
            color: '#6a5a45',
            fontSize: '0.92rem',
            fontWeight: 500,
            letterSpacing: '0.04em',
            textTransform: 'uppercase' as const,
          },
        }),
        render: (_, record) => {
          const badge = getOcrBadge(record);
          return (
            <Tag
              variant="filled"
              style={{
                marginInlineEnd: 0,
                borderRadius: 999,
                padding: '8px 18px',
                fontSize: '0.98rem',
                fontWeight: 700,
                color: badge.color,
                background: badge.bg,
              }}
            >
              {badge.label}
            </Tag>
          );
        },
      },
      {
        title: 'Updated',
        dataIndex: 'updated_at',
        key: 'updated',
        width: 210,
        onHeaderCell: () => ({
          style: {
            color: '#6a5a45',
            fontSize: '0.92rem',
            fontWeight: 500,
            letterSpacing: '0.04em',
            textTransform: 'uppercase' as const,
          },
        }),
        render: (value: string) => (
          <Typography.Text
            style={{
              fontSize: '1rem',
              color: '#241d17',
              whiteSpace: 'nowrap',
            }}
          >
            {formatDate(value)}
          </Typography.Text>
        ),
      },
      {
        title: 'Inspect',
        dataIndex: 'id',
        key: 'inspect',
        width: 120,
        align: 'center',
        onHeaderCell: () => ({
          style: {
            color: '#6a5a45',
            fontSize: '0.92rem',
            fontWeight: 500,
            letterSpacing: '0.04em',
            textTransform: 'uppercase' as const,
            textAlign: 'center' as const,
          },
        }),
        render: (id: string, record) => (
          <Link
            aria-label={`Inspect ${record.source_filename}`}
            href={`/imports-new/${id}`}
            style={{ textDecoration: 'none' }}
          >
            <Button
              shape="circle"
              size="large"
              style={{
                width: 54,
                height: 54,
                borderColor: 'rgba(15, 118, 110, 0.18)',
                color: '#115e59',
                background: 'rgba(15, 118, 110, 0.08)',
                boxShadow: 'none',
              }}
            >
              <FiEye size={18} />
            </Button>
          </Link>
        ),
      },
      {
        title: 'NEW Inspect',
        dataIndex: 'id',
        key: 'inspect',
        width: 120,
        align: 'center',
        onHeaderCell: () => ({
          style: {
            color: '#6a5a45',
            fontSize: '0.92rem',
            fontWeight: 500,
            letterSpacing: '0.04em',
            textTransform: 'uppercase' as const,
            textAlign: 'center' as const,
          },
        }),
        render: (id: string, record) => (
          <Link
            aria-label={`Inspect ${record.source_filename}`}
            href={`/imports/${id}`}
            style={{ textDecoration: 'none' }}
          >
            <Button
              shape="circle"
              size="large"
              style={{
                width: 54,
                height: 54,
                borderColor: 'rgba(15, 118, 110, 0.18)',
                color: '#115e59',
                background: 'rgba(15, 118, 110, 0.08)',
                boxShadow: 'none',
              }}
            >
              <FiEye size={18} />
            </Button>
          </Link>
        ),
      },
    ],
    [],
  );

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
          Table: {
            headerBg: 'rgba(251, 248, 242, 0.96)',
            headerColor: '#6a5a45',
            borderColor: 'rgba(73, 59, 36, 0.08)',
            cellPaddingBlock: 22,
            cellPaddingInline: 28,
            rowHoverBg: 'rgba(15, 118, 110, 0.03)',
          },
          Button: {
            defaultShadow: 'none',
            primaryShadow: 'none',
          },
        },
      }}
    >
      <main style={shellStyle}>
        <section style={panelStyle}>
          <div style={controlsStyle}>
            <div style={{ display: 'grid', gap: 8 }}>
              <Typography.Text
                style={{
                  color: '#6a5a45',
                  fontSize: '0.9rem',
                  letterSpacing: '0.02em',
                  textTransform: 'uppercase',
                }}
              >
                Filter category
              </Typography.Text>
              <select
                value={selectedCategoryFilter}
                onChange={(event) => {
                  setSelectedCategoryFilter(event.target.value);
                }}
                style={{
                  width: 260,
                  borderRadius: 10,
                  border: '1px solid rgba(73, 59, 36, 0.2)',
                  background: 'white',
                  color: '#241d17',
                  padding: '9px 10px',
                  fontSize: '0.96rem',
                }}
              >
                {CATEGORY_FILTER_OPTIONS.map((category) => (
                  <option key={category.value} value={category.value}>
                    {category.label}
                  </option>
                ))}
              </select>
            </div>

            <Link href="/upload-file" style={{ textDecoration: 'none' }}>
              <Button
                icon={<FiUpload size={16} />}
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
                Go To Upload Page
              </Button>
            </Link>
          </div>

          {errorMessage ? (
            <Alert
              showIcon
              title={errorMessage}
              style={{ marginBottom: 18 }}
              type="error"
            />
          ) : null}

          <div style={tableWrapStyle}>
            <Table<ImportRecord>
              columns={columns}
              dataSource={imports}
              loading={isLoading}
              locale={{
                emptyText: (
                  <Empty
                    description="No review records yet. Use the upload page or place files in the incoming folder and run scan."
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                  />
                ),
              }}
              pagination={false}
              rowKey="id"
              scroll={{ x: 1120 }}
            />
          </div>
        </section>
      </main>
    </ConfigProvider>
  );
}

function getImportStatusBadge(status: ImportRecord['status']) {
  if (status === 'checked') {
    return {
      bg: 'rgba(220, 252, 231, 0.95)',
      color: '#166534',
    };
  }

  if (status === 'ready_for_review' || status === 'review_ready') {
    return {
      bg: 'rgba(219, 234, 254, 0.95)',
      color: '#1d4ed8',
    };
  }

  if (status === 'ocr_failed') {
    return {
      bg: 'rgba(254, 226, 226, 0.95)',
      color: '#991b1b',
    };
  }

  if (status === 'cleaning' || status === 'ocr_running' || status === 'ocr_queued') {
    return {
      bg: 'rgba(224, 242, 254, 0.95)',
      color: '#075985',
    };
  }

  return {
    bg: 'rgba(255, 237, 213, 0.95)',
    color: '#9a3412',
  };
}

function getOcrBadge(record: ImportRecord) {
  if (record.status === 'cleaning') {
    return {
      label: 'Cleaning',
      bg: 'rgba(224, 242, 254, 0.95)',
      color: '#075985',
    };
  }

  if (record.status === 'ocr_queued' || record.status === 'uploaded') {
    return {
      label: 'Queued',
      bg: 'rgba(224, 242, 254, 0.95)',
      color: '#075985',
    };
  }

  if (record.status === 'ocr_running') {
    return {
      label: 'OCR running',
      bg: 'rgba(219, 234, 254, 0.95)',
      color: '#1d4ed8',
    };
  }

  if (record.status === 'ocr_failed' || record.ocr_error_message) {
    return {
      label: 'OCR error',
      bg: 'rgba(254, 226, 226, 0.95)',
      color: '#991b1b',
    };
  }

  if (record.ocr_markdown) {
    return {
      label: 'OCR ready',
      bg: 'rgba(220, 252, 231, 0.95)',
      color: '#166534',
    };
  }

  return {
    label: 'Pending',
    bg: 'rgba(224, 242, 254, 0.95)',
    color: '#075985',
  };
}

function isImportWorking(record: ImportRecord) {
  return (
    record.status === 'uploaded' ||
    record.status === 'cleaning' ||
    record.status === 'ocr_queued' ||
    record.status === 'ocr_running'
  );
}
