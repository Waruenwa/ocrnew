"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Input, Modal, Select, Table, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { FiExternalLink, FiRefreshCw, FiUploadCloud, FiUserPlus, FiClock, FiCheckCircle, FiXCircle, FiDatabase, FiSearch } from "react-icons/fi";
import { Box, Flex, Grid, HStack, Center, Text as ChakraText } from '@chakra-ui/react';
import { ConfigProvider } from 'antd';

import { ProtectedRolePage } from "../auth/protected-role-page";
import { getAuthHeaders } from "../lib/auth";
import { API_BASE_URL } from "../lib/review";

const { Text, Title } = Typography;

type DashboardBatch = {
  batch_id: string;
  selected_document_type: string;
  status: string;
  file_count: number;
  total_pages: number;
  record_count: number;
  ocr_pending_count: number;
  ocr_processing_count: number;
  ocr_succeeded_count: number;
  ocr_failed_count: number;
  ready_to_assign_count: number;
  assigned_count: number;
  in_review_count: number;
  completed_count: number;
};

type DashboardRecord = {
  record_id: string;
  record_no: string | null;
  batch_id: string;
  file_id: string;
  original_filename: string;
  selected_document_type: string;
  page_number: number;
  ocr_status: string;
  review_status: string;
  has_watermark: boolean | null;
  ocr_error: string | null;
  processed_at: string | null;
  assigned_to_user_id: string | null;
  assigned_to_username: string | null;
  assigned_at: string | null;
  created_at: string;
};

type ManagerDashboard = {
  batch_count: number;
  file_count: number;
  record_count: number;
  total_pages: number;
  ocr_pending_count: number;
  ocr_processing_count: number;
  ocr_succeeded_count: number;
  ocr_failed_count: number;
  ready_to_assign_count: number;
  assigned_count: number;
  unassigned_count: number;
  in_review_count: number;
  completed_count: number;
  batches: DashboardBatch[];
  records: DashboardRecord[];
};

type StaffUser = {
  user_id: string;
  username: string;
  display_name: string;
};

type AssignmentMessage = {
  type: "success" | "error";
  text: string;
};

type FileSortOrder = "ascend" | "descend" | null;

const ACTIVE_OCR_STATUSES = new Set(["pending", "processing"]);

function toNumber(value: unknown) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : 0;
}

function getFilenameTimestamp(filename: string) {
  const match = filename.match(/^(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})(\d{2})/);
  if (!match) {
    return null;
  }

  const [, day, month, year, hour, minute, second] = match;
  return Number(`${year}${month}${day}${hour}${minute}${second}`);
}

function compareFileRecords(a: DashboardRecord, b: DashboardRecord) {
  const aTimestamp = getFilenameTimestamp(String(a.original_filename || ""));
  const bTimestamp = getFilenameTimestamp(String(b.original_filename || ""));
  if (aTimestamp !== null && bTimestamp !== null && aTimestamp !== bTimestamp) {
    return aTimestamp - bTimestamp;
  }

  const filenameComparison = String(a.original_filename || "").localeCompare(String(b.original_filename || ""), undefined, {
    numeric: true,
    sensitivity: "base",
  });

  return filenameComparison || toNumber(a.page_number) - toNumber(b.page_number);
}

function PremiumTag({ status }: { status: string }) {
  let color = "#64748b";
  let bg = "rgba(226, 232, 240, 0.5)";
  
  const s = String(status).toLowerCase();
  
  if (["ocr_completed", "succeeded", "assigned", "completed", "yes", "true"].includes(s)) {
    color = "#059669";
    bg = "rgba(16, 185, 129, 0.15)";
  } else if (["partially_failed", "failed", "page_limit_exceeded"].includes(s)) {
    color = "#dc2626";
    bg = "rgba(239, 68, 68, 0.15)";
  } else if (["ocr_processing", "processing", "in_review"].includes(s)) {
    color = "#2563eb";
    bg = "rgba(59, 130, 246, 0.15)";
  } else if (["records_created", "pending", "unassigned"].includes(s)) {
    color = "#d97706";
    bg = "rgba(245, 158, 11, 0.15)";
  }

  return (
    <Box 
      display="inline-flex" 
      alignItems="center" 
      justifyContent="center" 
      px="12px" 
      py="4px" 
      borderRadius="8px" 
      bg={bg} 
      color={color} 
      fontSize="0.75rem" 
      fontWeight="800" 
      textTransform="uppercase" 
      letterSpacing="0.05em"
      border={`1px solid ${bg.replace('0.15', '0.3')}`}
    >
      {s.replace(/_/g, " ")}
    </Box>
  );
}

function formatShortDate(value: string | null) {
  if (!value) {
    return "-";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("th-TH", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function canAssignRecord(record: DashboardRecord) {
  return (
    record.ocr_status === "succeeded" &&
    record.review_status === "unassigned" &&
    !record.assigned_to_user_id &&
    !record.assigned_to_username
  );
}

const emptyDashboard: ManagerDashboard = {
  batch_count: 0,
  file_count: 0,
  record_count: 0,
  total_pages: 0,
  ocr_pending_count: 0,
  ocr_processing_count: 0,
  ocr_succeeded_count: 0,
  ocr_failed_count: 0,
  ready_to_assign_count: 0,
  assigned_count: 0,
  unassigned_count: 0,
  in_review_count: 0,
  completed_count: 0,
  batches: [],
  records: [],
};

export default function ManagerPage() {
  const [dashboard, setDashboard] = useState<ManagerDashboard>(emptyDashboard);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [staffUsers, setStaffUsers] = useState<StaffUser[]>([]);
  const [staffLoadError, setStaffLoadError] = useState<string | null>(null);
  const [selectedRecordIds, setSelectedRecordIds] = useState<string[]>([]);
  const [selectedStaffUserId, setSelectedStaffUserId] = useState<string | undefined>();
  const [isAssignModalOpen, setIsAssignModalOpen] = useState(false);
  const [isAssigning, setIsAssigning] = useState(false);
  const [assignmentMessage, setAssignmentMessage] = useState<AssignmentMessage | null>(null);
  const [filterStatus, setFilterStatus] = useState<string | null>(null);
  const [recordSearch, setRecordSearch] = useState("");
  const [recordPagination, setRecordPagination] = useState({ current: 1, pageSize: 10 });
  const [recordFileSortOrder, setRecordFileSortOrder] = useState<FileSortOrder>(null);

  const filteredRecords = useMemo(() => {
    const statusFilteredRecords = !filterStatus
      ? dashboard.records
      : dashboard.records.filter(record => {
      if (filterStatus === 'running') return ['pending', 'processing'].includes(record.ocr_status);
      if (filterStatus === 'review') return record.review_status === 'unassigned' && record.ocr_status === 'succeeded';
      if (filterStatus === 'completed') return record.review_status === 'completed';
      if (filterStatus === 'failed') return record.ocr_status === 'failed';
      return true;
    });

    const query = recordSearch.trim().toLowerCase();
    if (!query) {
      return statusFilteredRecords;
    }

    return statusFilteredRecords.filter((record) => {
      const searchableText = [
        record.record_no,
        record.record_id,
        record.original_filename,
        `page ${record.page_number}`,
        String(record.page_number),
        record.selected_document_type,
        record.ocr_status,
        record.review_status,
        record.assigned_to_username,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return searchableText.includes(query);
    });
  }, [dashboard.records, filterStatus, recordSearch]);

  const sortedRecords = useMemo(() => {
    if (!recordFileSortOrder) {
      return filteredRecords;
    }

    return [...filteredRecords].sort((a, b) => {
      const comparison = compareFileRecords(a, b);
      return recordFileSortOrder === "ascend" ? comparison : -comparison;
    });
  }, [filteredRecords, recordFileSortOrder]);

  const shouldPoll = dashboard.records.some((record) => ACTIVE_OCR_STATUSES.has(record.ocr_status));
  const selectedAssignableRecords = dashboard.records.filter(
    (record) => selectedRecordIds.includes(record.record_id) && canAssignRecord(record),
  );

  async function loadDashboard() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/manager/dashboard`, {
        headers: getAuthHeaders(),
      });
      const payload = (await response.json().catch(() => null)) as ManagerDashboard | { detail?: unknown } | null;

      if (!response.ok) {
        setError(String((payload as { detail?: unknown } | null)?.detail || "Unable to load dashboard."));
        return;
      }

      setDashboard(payload as ManagerDashboard);
      setError(null);
    } catch {
      setError(`Unable to reach the backend API at ${API_BASE_URL}.`);
    } finally {
      setIsLoading(false);
    }
  }

  async function loadStaffUsers() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/manager/staff`, {
        headers: getAuthHeaders(),
      });
      const payload = (await response.json().catch(() => null)) as StaffUser[] | { detail?: unknown } | null;
      if (!response.ok) {
        setStaffLoadError(String((payload as { detail?: unknown } | null)?.detail || "Unable to load staff users."));
        return;
      }
      setStaffUsers(payload as StaffUser[]);
      setStaffLoadError(null);
    } catch {
      setStaffUsers([]);
      setStaffLoadError(`Unable to reach the backend API at ${API_BASE_URL}.`);
    }
  }

  async function handleAssignSelectedRecords() {
    const assignRecordIds = selectedAssignableRecords.map((record) => record.record_id);
    if (!assignRecordIds.length) {
      setAssignmentMessage({ type: "error", text: "Please select records to assign." });
      return;
    }
    if (!selectedStaffUserId) {
      setAssignmentMessage({ type: "error", text: "Please select a reviewer." });
      return;
    }

    const selectedStaff = staffUsers.find((staffUser) => staffUser.user_id === selectedStaffUserId);
    setIsAssigning(true);
    setAssignmentMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/manager/records/assign`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders(),
        },
        body: JSON.stringify({
          record_ids: assignRecordIds,
          staff_user_id: selectedStaffUserId,
          staff_username: selectedStaff?.username ?? selectedStaffUserId,
        }),
      });
      const payload = (await response.json().catch(() => null)) as ManagerDashboard | { detail?: unknown } | null;
      if (!response.ok) {
        setAssignmentMessage({
          type: "error",
          text: String((payload as { detail?: unknown } | null)?.detail || "Assignment failed."),
        });
        return;
      }

      setDashboard(payload as ManagerDashboard);
      setSelectedRecordIds([]);
      setSelectedStaffUserId(undefined);
      setIsAssignModalOpen(false);
      setAssignmentMessage({
        type: "success",
        text: `Assigned ${assignRecordIds.length} record${assignRecordIds.length === 1 ? "" : "s"} successfully.`,
      });
    } catch {
      setAssignmentMessage({
        type: "error",
        text: `Unable to reach the backend API at ${API_BASE_URL}.`,
      });
    } finally {
      setIsAssigning(false);
    }
  }

  useEffect(() => {
    void loadDashboard();
    void loadStaffUsers();
  }, []);

  useEffect(() => {
    setSelectedRecordIds((currentRecordIds) =>
      currentRecordIds.filter((recordId) =>
        dashboard.records.some((record) => record.record_id === recordId && canAssignRecord(record)),
      ),
    );
  }, [dashboard.records]);

  useEffect(() => {
    setRecordPagination((current) => ({ ...current, current: 1 }));
  }, [filterStatus, recordSearch]);

  useEffect(() => {
    if (!shouldPoll) {
      return;
    }

    const intervalId = setInterval(() => {
      void loadDashboard();
    }, 3000);

    return () => clearInterval(intervalId);
  }, [shouldPoll]);

  const recordColumns = useMemo<ColumnsType<DashboardRecord>>(
    () => [
      {
        title: "Record No",
        dataIndex: "record_no",
        key: "record_no",
        width: 150,
        render: (value: string | null, record) => (
          <div className="tablePrimaryCell">
            <span className="roleTableFilename">{value || record.record_id.slice(0, 8)}</span>
            <span>ID {record.record_id.slice(0, 8)}</span>
          </div>
        ),
      },
      {
        title: "File",
        dataIndex: "original_filename",
        key: "original_filename",
        sorter: true,
        sortOrder: recordFileSortOrder,
        sortDirections: ["descend", "ascend"],
        render: (value: string, record) => (
          <div className="tablePrimaryCell">
            <span className="roleTableFilename">{value}</span>
            <span>page {record.page_number}</span>
          </div>
        ),
      },
      {
        title: "Type",
        dataIndex: "selected_document_type",
        key: "selected_document_type",
        width: 100,
        render: (value: string) => (
          <Box display="inline-block" px="10px" py="4px" borderRadius="6px" bg="#f1f5f9" color="#475569" fontWeight="700" fontSize="0.75rem" border="1px solid #e2e8f0">
            {value}
          </Box>
        ),
      },
      {
        title: "OCR",
        dataIndex: "ocr_status",
        key: "ocr_status",
        width: 140,
        render: (value: string, record) => {
          if (value !== "failed" || !record.ocr_error) {
            return <PremiumTag status={value} />;
          }

          return (
            <Tooltip title={record.ocr_error}>
              <div className="tablePrimaryCell">
                <PremiumTag status={value} />
                <span style={{ color: "#dc2626", fontSize: "0.72rem", maxWidth: 220 }}>
                  {record.ocr_error}
                </span>
              </div>
            </Tooltip>
          );
        },
      },
      {
        title: "Review",
        dataIndex: "review_status",
        key: "review_status",
        width: 140,
        render: (value: string) => <PremiumTag status={value} />,
      },
      {
        title: "Watermark",
        dataIndex: "has_watermark",
        key: "has_watermark",
        width: 120,
        render: (value: boolean | null) => {
          if (value === null) {
            return <ChakraText color="#94a3b8" fontWeight="500" fontSize="0.85rem">checking</ChakraText>;
          }
          return <PremiumTag status={value ? "yes" : "no"} />;
        },
      },
      {
        title: "Assigned To",
        dataIndex: "assigned_to_username",
        key: "assigned_to_username",
        width: 150,
        render: (value: string | null) => value ? <ChakraText fontWeight="600" color="#334155">{value}</ChakraText> : <ChakraText color="#cbd5e1">-</ChakraText>,
      },
      {
        title: "Updated",
        dataIndex: "processed_at",
        key: "processed_at",
        width: 150,
        render: (value: string | null, record) => (
          <ChakraText color="#64748b" fontSize="0.85rem">
            {formatShortDate(value || record.assigned_at || record.created_at)}
          </ChakraText>
        ),
      },
      {
        title: "",
        key: "actions",
        width: 180,
        render: (_, record) => (
          <HStack gap="8px" justify="flex-end">
            <Link href={`/tr-imports/${record.record_id}?source=manager`}>
              <Button 
                size="small" 
                icon={<FiExternalLink />}
                style={{ 
                  borderRadius: '8px', 
                  fontWeight: 600,
                  color: '#475569',
                  borderColor: '#e2e8f0',
                  background: 'white',
                  boxShadow: '0 2px 4px rgba(0,0,0,0.02)'
                }}
              >
                View
              </Button>
            </Link>
            {/* <Link href={`/manager/batches/${record.batch_id}`}>
              <Button size="small">
                Batch
              </Button>
            </Link> */}
          </HStack>
        ),
      },
    ],
    [recordFileSortOrder],
  );

  return (
    <ProtectedRolePage
      allowedRole="manager"
      eyebrow="Manager"
      title="Manager Dashboard"
      contentMaxW="min(1540px, calc(100vw - 80px))"
      stats={[]}
    >
      <ConfigProvider
        theme={{
          token: {
            borderRadius: 8,
            fontFamily: 'inherit',
          },
          components: {
            Table: {
              headerBg: '#f8fafc',
              headerColor: '#475569',
              headerBorderRadius: 12,
              rowHoverBg: '#f1f5f9',
              cellPaddingBlock: 10,
              cellPaddingInline: 12,
              fontSize: 13,
            },
          },
        }}
      >
        <Flex direction="column" gap="24px" maxW="100%" mx="auto">
          {/* Top Header & Actions */}
          <Flex justify="space-between" align="center" wrap="wrap" gap="16px">
            <Box>
              <Title level={2} style={{ margin: 0, color: '#0f172a', fontWeight: 800, letterSpacing: "-0.03em" }}>
                TR Records
              </Title>
              <ChakraText color="#64748b" fontSize="0.85rem" mt="2px">
                Uploads return here automatically. OCR status updates while processing runs.
              </ChakraText>
            </Box>
            <HStack gap="12px" justify="flex-end" flexWrap="wrap">
              <Button size="middle" icon={<FiRefreshCw />} onClick={() => void loadDashboard()} loading={isLoading} style={{ borderRadius: '10px', fontWeight: 600 }}>
                Refresh
              </Button>
              <Button
                size="middle"
                icon={<FiUserPlus />}
                disabled={!selectedRecordIds.length}
                onClick={() => {
                  setAssignmentMessage(null);
                  setIsAssignModalOpen(true);
                }}
                style={{ 
                  borderRadius: '10px', 
                  fontWeight: 700,
                  transition: 'all 0.3s ease',
                  ...(selectedRecordIds.length > 0 ? {
                    backgroundColor: '#f97316',
                    color: 'white',
                    borderColor: '#ea580c',
                    boxShadow: '0 4px 12px rgba(249, 115, 22, 0.25)',
                  } : {})
                }}
              >
                Assign Selected {selectedRecordIds.length > 0 && `(${selectedRecordIds.length})`}
              </Button>
              <Link href="/manager/upload">
                <Button 
                  size="middle" 
                  type="primary" 
                  icon={<FiUploadCloud />} 
                  style={{ 
                    background: 'linear-gradient(135deg, #136360 0%, #0d4a48 100%)', 
                    borderColor: 'transparent', 
                    borderRadius: '10px', 
                    fontWeight: 700, 
                    boxShadow: '0 4px 14px rgba(19, 99, 96, 0.2)',
                    padding: '0 20px'
                  }}
                >
                  Upload New TR
                </Button>
              </Link>
            </HStack>
          </Flex>

          {/* Metrics Grid */}
          <Grid templateColumns="repeat(5, 1fr)" gap="12px">
            <MetricCard 
              title="All Records" 
              value={dashboard.record_count} 
              color="#64748b" 
              bg="#f1f5f9"
              iconColor="#475569"
              isActive={filterStatus === null}
              onClick={() => setFilterStatus(null)}
              iconType="all"
            />
            <MetricCard 
              title="OCR Running" 
              value={dashboard.ocr_pending_count + dashboard.ocr_processing_count} 
              color="#3b82f6" 
              bg="#eff6ff"
              iconColor="#2563eb"
              isActive={filterStatus === 'running'}
              onClick={() => setFilterStatus('running')}
              iconType="running"
            />
            <MetricCard 
              title="Review" 
              value={dashboard.ready_to_assign_count} 
              color="#f97316" 
              bg="#fff7ed"
              iconColor="#ea580c"
              isActive={filterStatus === 'review'}
              onClick={() => setFilterStatus('review')}
              iconType="review"
            />
            <MetricCard 
              title="Completed" 
              value={dashboard.completed_count} 
              color="#10b981" 
              bg="#f0fdf4"
              iconColor="#059669"
              isActive={filterStatus === 'completed'}
              onClick={() => setFilterStatus('completed')}
              iconType="completed"
            />
            <MetricCard 
              title="Failed" 
              value={dashboard.ocr_failed_count} 
              color="#ef4444" 
              bg="#fef2f2"
              iconColor="#dc2626"
              isActive={filterStatus === 'failed'}
              onClick={() => setFilterStatus('failed')}
              iconType="failed"
            />
          </Grid>

          {/* Table Container */}
          <Box bg="white" borderRadius="20px" p="24px" border="1px solid rgba(226, 232, 240, 0.6)" boxShadow="0 10px 40px rgba(0, 0, 0, 0.02)" w="100%">
            {error ? <Alert showIcon type="error" title={error} style={{ marginBottom: 16 }} /> : null}
            {assignmentMessage ? (
              <Alert
                showIcon
                type={assignmentMessage.type}
                title={assignmentMessage.text}
                style={{ marginBottom: 16 }}
              />
            ) : null}

            <Flex justify="space-between" align="center" gap="12px" mb="14px" wrap="wrap">
              <Input
                allowClear
                prefix={<FiSearch color="#94a3b8" />}
                placeholder="Search record no, file, page, status, assignee"
                value={recordSearch}
                onChange={(event) => setRecordSearch(event.target.value)}
                style={{
                  maxWidth: 420,
                  borderRadius: 10,
                  height: 40,
                }}
              />
              <ChakraText color="#64748b" fontSize="0.85rem" fontWeight="600">
                {sortedRecords.length} record{sortedRecords.length === 1 ? "" : "s"}
              </ChakraText>
            </Flex>

            <Box borderRadius="12px" overflow="hidden" className="managerRecordsTable" border="1px solid #f1f5f9">
              <Table<DashboardRecord>
                columns={recordColumns}
                dataSource={sortedRecords}
                loading={isLoading}
                rowKey="record_id"
                size="small"
                onChange={(pagination, __, sorter, extra) => {
                  const fileSorter = Array.isArray(sorter)
                    ? sorter.find((item) => item.columnKey === "original_filename")
                    : sorter;
                  const nextOrder = fileSorter?.order === "ascend" || fileSorter?.order === "descend"
                    ? fileSorter.order
                    : null;
                  setRecordFileSortOrder(nextOrder);
                  setRecordPagination((current) => ({
                    current: extra.action === "sort" ? 1 : pagination.current ?? current.current,
                    pageSize: pagination.pageSize ?? current.pageSize,
                  }));
                }}
                rowSelection={{
                  selectedRowKeys: selectedRecordIds,
                  onChange: (selectedRowKeys) => {
                    setSelectedRecordIds(selectedRowKeys.map(String));
                    setAssignmentMessage(null);
                  },
                  getCheckboxProps: (record) => ({
                    disabled: !canAssignRecord(record),
                  }),
                }}
                pagination={{
                  current: recordPagination.current,
                  pageSize: recordPagination.pageSize,
                  showSizeChanger: true,
                  pageSizeOptions: [10, 20, 50, 100],
                }}
                locale={{ emptyText: "No records yet." }}
              />
            </Box>
          </Box>
        </Flex>
      </ConfigProvider>

      <Modal
        title="Assign Selected Records"
        open={isAssignModalOpen}
        okText="Assign Records"
        confirmLoading={isAssigning}
        okButtonProps={{ disabled: !selectedStaffUserId || !selectedAssignableRecords.length }}
        onOk={() => void handleAssignSelectedRecords()}
        onCancel={() => {
          setIsAssignModalOpen(false);
          setAssignmentMessage(null);
        }}
      >
        <Flex direction="column" gap="16px" pt="10px">
            <Text>
              {selectedAssignableRecords.length} ready record{selectedAssignableRecords.length === 1 ? "" : "s"} selected
            </Text>
            <Select
              placeholder="Select reviewer"
              value={selectedStaffUserId}
              onChange={(value) => {
                setSelectedStaffUserId(value);
                setAssignmentMessage(null);
              }}
              options={staffUsers.map((staffUser) => ({
                value: staffUser.user_id,
                label: `${staffUser.display_name} (${staffUser.username})`,
              }))}
              style={{ width: "100%" }}
            />
            {staffLoadError ? <Text type="danger">{staffLoadError}</Text> : null}
            {!staffUsers.length && !staffLoadError ? <Text type="secondary">No reviewers are available.</Text> : null}
          </Flex>
      </Modal>
    </ProtectedRolePage>
  );
}

// Subcomponents
function MetricCard({ 
  title, 
  value, 
  color, 
  bg, 
  iconColor, 
  isActive, 
  onClick,
  iconType
}: { 
  title: string, 
  value: number | string, 
  color: string, 
  bg: string, 
  iconColor: string,
  isActive?: boolean,
  onClick?: () => void,
  iconType: 'all' | 'running' | 'review' | 'completed' | 'failed'
}) {
  let Icon = FiDatabase;
  if (iconType === 'running') Icon = FiClock;
  else if (iconType === 'review') Icon = FiUserPlus;
  else if (iconType === 'completed') Icon = FiCheckCircle;
  else if (iconType === 'failed') Icon = FiXCircle;
  else if (iconType === 'all') Icon = FiDatabase;

  return (
    <Box 
      bg={isActive ? "white" : "rgba(255, 255, 255, 0.4)"} 
      p="16px 20px" 
      borderRadius="16px" 
      border="1px solid"
      borderColor={isActive ? color : "rgba(226, 232, 240, 0.8)"}
      boxShadow={isActive ? `0 8px 20px ${color}15` : "0 2px 10px rgba(0,0,0,0.02)"}
      transition="all 0.2s cubic-bezier(0.4, 0, 0.2, 1)"
      cursor="pointer"
      onClick={onClick}
      role="button"
      _hover={{ 
        transform: 'translateY(-2px)', 
        boxShadow: `0 10px 25px ${color}10`,
        borderColor: color,
        bg: "white"
      }}
      position="relative"
      overflow="hidden"
    >
      {isActive && (
        <Box 
          position="absolute" 
          left={0} 
          top={0} 
          bottom={0} 
          w="4px" 
          bg={color} 
        />
      )}
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
