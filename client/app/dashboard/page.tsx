'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  ConfigProvider,
  Empty,
  Input,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { TableProps } from 'antd';
import { 
  FiEye, 
  FiUpload, 
  FiSearch, 
  FiChevronLeft, 
  FiChevronRight, 
  FiCalendar, 
  FiDatabase,
  FiClock,
  FiTool,
  FiEdit,
  FiCheckCircle,
  FiXCircle,
  FiFileText,
  FiDownload,
  FiLogOut
} from 'react-icons/fi';
import { Box, Flex, Grid, HStack, Center } from '@chakra-ui/react';

import {
  API_BASE_URL,
  formatDate,
  type ImportRecord,
  importStatusLabels,
} from '../lib/review';
import {
  formatDocumentCategoryLabel,
} from '../lib/document-categories';

const { Text, Title } = Typography;

export default function Dashboard() {
  const [imports, setImports] = useState<ImportRecord[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [username, setUsername] = useState<string>('');
  const router = useRouter();

  useEffect(() => {
    setUsername(localStorage.getItem('username') || '');
  }, []);

  const handleLogout = () => {
    localStorage.removeItem('username');
    router.push('/');
  };

  useEffect(() => {
    void loadQueue();
  }, []);

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
  }, [imports]);

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
    const requestUrl = `${API_BASE_URL}/api/imports`;

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

  // Filtered imports for search
  const filteredImports = useMemo(() => {
    if (!searchText) return imports;
    return imports.filter(record => 
      record.source_filename.toLowerCase().includes(searchText.toLowerCase()) ||
      (record.document_category && record.document_category.toLowerCase().includes(searchText.toLowerCase()))
    );
  }, [imports, searchText]);

  // Derived Stats
  const stats = useMemo(() => {
    return {
      total: imports.length,
      newDocs: imports.filter(i => i.status === 'uploaded' || i.status === 'ocr_queued').length,
      inProgress: imports.filter(i => i.status === 'cleaning' || i.status === 'ocr_running').length,
      pending: imports.filter(i => i.status === 'ocr_failed').length,
      closed: imports.filter(i => i.status === 'ready_for_review' || i.status === 'review_ready' || i.status === 'checked').length,
      cancelled: 0,
    };
  }, [imports]);

  const columns = useMemo<NonNullable<TableProps<ImportRecord>['columns']>>(
    () => [
      {
        title: '',
        key: 'actions',
        width: 100,
        align: 'center',
        render: (_, record) => (
          <HStack gap="8px" justify="center">
            <Link href={getInspectHref(record, 'new')}>
              <Button 
                shape="circle" 
                size="small" 
                style={{ background: '#136360', color: '#fff', border: 'none' }}
                icon={<FiEye />}
              />
            </Link>
            <Link href={getInspectHref(record, 'classic')}>
              <Button 
                shape="circle" 
                size="small" 
                style={{ background: '#fffbeb', color: '#d97706', border: 'none' }}
                icon={<FiEdit />}
              />
            </Link>
          </HStack>
        ),
      },
      {
        title: 'Document Name',
        dataIndex: 'source_filename',
        key: 'document',
        render: (val, record) => (
          <Box>
            <Box fontWeight="600" color="#374151">{val}</Box>
            <Box fontSize="0.8rem" color="#6b7280">{record.source_path}</Box>
          </Box>
        ),
      },
      {
        title: 'Status',
        dataIndex: 'status',
        key: 'status',
        width: 160,
        render: (status: ImportRecord['status']) => {
          const badge = getImportStatusBadge(status);
          return (
            <Tag color={badge.bg} style={{ color: badge.color, borderRadius: 12, padding: '4px 12px', fontWeight: 600, border: 'none' }}>
              {importStatusLabels[status] || status}
            </Tag>
          );
        },
      },
      {
        title: 'Category',
        dataIndex: 'document_category',
        key: 'category',
        width: 140,
        render: (val) => formatDocumentCategoryLabel(val),
      },
      {
        title: 'Pages',
        dataIndex: 'total_pages',
        key: 'pages',
        width: 100,
        align: 'center',
      },
      {
        title: 'OCR Status',
        key: 'ocr',
        width: 160,
        render: (_, record) => {
          const badge = getOcrBadge(record);
          return (
            <Tag color={badge.bg} style={{ color: badge.color, borderRadius: 12, padding: '4px 12px', fontWeight: 600, border: 'none' }}>
              {badge.label}
            </Tag>
          );
        },
      },
      {
        title: 'Updated Date',
        dataIndex: 'updated_at',
        key: 'updated',
        width: 180,
        render: (val) => formatDate(val),
      },
    ],
    [],
  );

  return (
    <ConfigProvider
      theme={{
        token: {
          fontFamily: 'inherit',
          colorText: '#374151',
          colorPrimary: '#136360',
          borderRadius: 8,
        },
        components: {
          Table: {
            headerBg: '#136360',
            headerColor: '#ffffff',
            headerBorderRadius: 8,
            rowHoverBg: '#f8fafc',
            cellPaddingBlock: 16,
          },
          Button: {
            defaultShadow: 'none',
            primaryShadow: 'none',
          },
        },
      }}
    >
      <Box minH="100vh" bg="#f4f7fa">
        
        {/* Navbar */}
        <Flex 
          as="nav"
          w="100%"
          position="sticky"
          top={0}
          zIndex={100}
          bg="rgba(255, 255, 255, 0.85)"
          css={{ backdropFilter: 'blur(16px)' }}
          borderBottom="1px solid rgba(226, 232, 240, 0.8)"
          px="40px"
          py="16px"
          justify="space-between"
          align="center"
          boxShadow="0 4px 30px rgba(0, 0, 0, 0.03)"
        >
          {/* Logo */}
          <Box fontSize="26px" fontWeight="900" color="#136360" letterSpacing="0.05em">
            TYPH<Box as="span" color="#e11d48" mx="-1px">/</Box>ON
          </Box>

          {/* User Profile & Logout */}
          <HStack gap="24px">
            <HStack gap="12px" cursor="pointer" transition="opacity 0.2s" _hover={{ opacity: 0.8 }}>
              <Center 
                w="42px" 
                h="42px" 
                borderRadius="full" 
                bg="linear-gradient(135deg, #e0f2fe 0%, #bae6fd 100%)" 
                color="#0369a1" 
                fontWeight="700"
                fontSize="1.1rem"
                boxShadow="0 2px 10px rgba(3, 105, 161, 0.1)"
              >
                {username ? username.charAt(0).toUpperCase() : 'U'}
              </Center>
              <Text style={{ fontWeight: 600, color: '#374151', fontSize: '1rem', margin: 0 }}>
                {username || 'User'}
              </Text>
            </HStack>
            <Box w="1px" h="28px" bg="#e2e8f0" /> {/* Divider */}
            <Button 
              type="text" 
              onClick={handleLogout}
              style={{ color: '#ef4444', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 8, fontSize: '1rem', padding: '8px 12px', borderRadius: '8px' }}
            >
              <FiLogOut size={18} /> Logout
            </Button>
          </HStack>
        </Flex>
        
        {/* Main Content Area */}
        <Box px="40px" py="32px" maxW="1600px" mx="auto">
        {/* Top Header / Stats Section */}
        <Box bg="white" borderRadius="24px" p="32px" mb="32px" boxShadow="0 10px 40px rgba(0,0,0,0.02)">
          {/* Stats Cards */}
          <Grid templateColumns="repeat(6, 1fr)" gap="16px">
            <StatCard title="TOTAL" value={stats.total} color="#3b82f6" icon={<FiDatabase />} bg="#eff6ff" />
            <StatCard title="New" value={stats.newDocs} color="#ef4444" icon={<FiClock />} bg="#fef2f2" />
            <StatCard title="In Progress" value={stats.inProgress} color="#f97316" icon={<FiTool />} bg="#fff7ed" />
            <StatCard title="Pending" value={stats.pending} color="#eab308" icon={<FiEdit />} bg="#fefce8" />
            <StatCard title="Closed" value={stats.closed} color="#22c55e" icon={<FiCheckCircle />} bg="#f0fdf4" />
            <StatCard title="Cancelled" value={stats.cancelled} color="#94a3b8" icon={<FiXCircle />} bg="#f8fafc" />
          </Grid>
        </Box>

        {/* Main Table Section */}
        <Box bg="white" borderRadius="24px" p="32px" boxShadow="0 10px 40px rgba(0,0,0,0.02)">
          <Flex justify="space-between" align="center" mb="24px" wrap="wrap" gap="16px">
            {/* Left side title */}
            <HStack gap="16px">
              <Center bg="#136360" color="white" borderRadius="12px" w="48px" h="48px">
                <FiFileText size={24} />
              </Center>
              <Box>
                <HStack gap="12px" mb="4px">
                  <Title level={3} style={{ margin: 0, color: '#111827', fontWeight: 800 }}>Dashboard OCR</Title>
                  <HStack bg="#f1f5f9" px="12px" py="4px" borderRadius="full" fontSize="0.85rem" fontWeight="600" color="#475569" gap="8px">
                    <Box>จำนวนเอกสารค้าง</Box>
                    <Center bg="#ef4444" color="white" borderRadius="full" w="24px" h="24px" fontSize="0.75rem">
                      {stats.inProgress + stats.pending}
                    </Center>
                    <Box>รายการ</Box>
                  </HStack>
                </HStack>
                <Text style={{ color: '#64748b', fontSize: '0.85rem', fontWeight: 500, margin: 0 }}>
                  ข้อมูลการนำเข้าและประมวลผลเอกสาร OCR • ข้อมูลประจำเดือน May 2026
                </Text>
              </Box>
            </HStack>

            {/* Right side controls */}
            <HStack gap="12px">
              <Input 
                prefix={<FiSearch color="#94a3b8" />} 
                placeholder="ค้นหา..." 
                value={searchText}
                onChange={e => setSearchText(e.target.value)}
                style={{ width: 240, borderRadius: 999, padding: '8px 16px', background: '#f8fafc', border: '1px solid #e2e8f0' }}
              />
              <Link href="/upload-file" style={{ textDecoration: 'none' }}>
                <Button 
                  type="primary" 
                  icon={<FiUpload />} 
                  style={{ background: '#136360', borderRadius: 999, padding: '8px 24px', height: 'auto', fontWeight: 600 }}
                >
                  Upload
                </Button>
              </Link>
              <Button 
                icon={<FiDownload />} 
                style={{ borderRadius: 999, padding: '8px 20px', height: 'auto', color: '#136360', borderColor: '#e2e8f0', fontWeight: 600 }}
              >
                Export
              </Button>
            </HStack>
          </Flex>

          {errorMessage && (
            <Alert showIcon title={errorMessage} style={{ marginBottom: 18 }} type="error" />
          )}

          <Box borderRadius="12px" overflow="hidden" border="1px solid #e2e8f0">
            <Table<ImportRecord>
              columns={columns}
              dataSource={filteredImports}
              loading={isLoading}
              pagination={false}
              rowKey="id"
              scroll={{ x: 1000 }}
              size='small'
              bordered
            />
          </Box>
        </Box>
      </Box>
      </Box>
    </ConfigProvider>
  );
}

// Subcomponents
function StatCard({ title, value, color, icon, bg }: { title: string, value: number, color: string, icon: React.ReactNode, bg: string }) {
  return (
    <Flex 
      bg="white" 
      border="1px solid #e2e8f0"
      borderLeft={`6px solid ${color}`}
      borderRadius="16px" 
      p="20px 24px"
      justify="space-between"
      align="center"
      boxShadow="0 2px 10px rgba(0,0,0,0.02)"
      transition="all 0.3s ease"
      css={{
        '&:hover': {
          transform: 'translateY(-4px)',
          boxShadow: '0 12px 24px rgba(0,0,0,0.06)',
          borderColor: '#cbd5e1'
        }
      }}
    >
      <Box>
        <Box color="#64748b" fontSize="0.85rem" fontWeight="700" textTransform="uppercase" mb="6px" letterSpacing="0.02em">{title}</Box>
        <Box color="#0f172a" fontSize="2rem" fontWeight="900" lineHeight="1">{value}</Box>
      </Box>
      <Center bg={bg} color={color} w="48px" h="48px" borderRadius="full" fontSize="1.4rem">
        {icon}
      </Center>
    </Flex>
  );
}

// Helpers
function getImportStatusBadge(status: ImportRecord['status']) {
  if (status === 'checked') return { bg: '#dcfce7', color: '#166534' };
  if (status === 'ready_for_review' || status === 'review_ready') return { bg: '#dbeafe', color: '#1d4ed8' };
  if (status === 'ocr_failed') return { bg: '#fee2e2', color: '#991b1b' };
  if (status === 'cleaning' || status === 'ocr_running' || status === 'ocr_queued') return { bg: '#e0f2fe', color: '#0369a1' };
  return { bg: '#ffedd5', color: '#9a3412' };
}

function getOcrBadge(record: ImportRecord) {
  if (record.status === 'cleaning') return { label: 'Cleaning', bg: '#e0f2fe', color: '#0369a1' };
  if (record.status === 'ocr_queued' || record.status === 'uploaded') return { label: 'Queued', bg: '#e0f2fe', color: '#0369a1' };
  if (record.status === 'ocr_running') return { label: 'Running', bg: '#dbeafe', color: '#1d4ed8' };
  if (record.status === 'ocr_failed' || record.ocr_error_message) return { label: 'Failed', bg: '#fee2e2', color: '#991b1b' };
  if (record.ocr_markdown) return { label: 'Ready', bg: '#dcfce7', color: '#166534' };
  return { label: 'Pending', bg: '#e0f2fe', color: '#0369a1' };
}

function getInspectHref(record: ImportRecord, mode: 'new' | 'classic') {
  if ((record.document_category || '').trim().toLowerCase() === 'tr') return `/tr-imports/${record.id}`;
  return mode === 'new' ? `/imports-new/${record.id}` : `/imports/${record.id}`;
}

function isImportWorking(record: ImportRecord) {
  return record.status === 'uploaded' || record.status === 'cleaning' || record.status === 'ocr_queued' || record.status === 'ocr_running';
}
