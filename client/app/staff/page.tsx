"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Alert, Button, ConfigProvider, Input, Spin, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { FiClipboard, FiDatabase, FiClock, FiCheckCircle, FiExternalLink, FiSearch } from "react-icons/fi";
import { Box, Flex, Grid, HStack, Center, Text as ChakraText } from '@chakra-ui/react';

import { ProtectedRolePage } from "../auth/protected-role-page";
import { getAuthHeaders } from "../lib/auth";
import { API_BASE_URL } from "../lib/review";

const { Text, Title } = Typography;

type StaffAssignedRecord = {
  record_id: string;
  record_no: string | null;
  batch_id: string;
  file_id: string;
  original_filename: string;
  selected_document_type: string;
  page_number: number;
  ocr_status: string;
  review_status: string;
  assigned_to_user_id: string | null;
  assigned_to_username: string | null;
  assigned_at: string | null;
  processed_at: string | null;
};

function PremiumTag({ status }: { status: string }) {
  let color = "#64748b";
  let bg = "rgba(226, 232, 240, 0.5)";
  
  const s = String(status).toLowerCase();
  
  if (["completed", "succeeded"].includes(s)) {
    color = "#059669";
    bg = "rgba(16, 185, 129, 0.15)";
  } else if (["failed"].includes(s)) {
    color = "#dc2626";
    bg = "rgba(239, 68, 68, 0.15)";
  } else if (["processing", "assigned"].includes(s)) {
    color = "#2563eb";
    bg = "rgba(59, 130, 246, 0.15)";
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
      fontSize="0.7rem" 
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
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("th-TH", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

export default function StaffPage() {
  const [records, setRecords] = useState<StaffAssignedRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState<string | null>(null);
  const [recordSearch, setRecordSearch] = useState("");

  const stats = useMemo(() => {
    return {
      total: records.length,
      inProgress: records.filter(r => r.review_status !== "completed").length,
      completed: records.filter(r => r.review_status === "completed").length,
    };
  }, [records]);

  const filteredRecords = useMemo(() => {
    const statusFilteredRecords =
      !filterStatus
        ? records
        : filterStatus === 'pending'
          ? records.filter(r => r.review_status !== "completed")
          : filterStatus === 'completed'
            ? records.filter(r => r.review_status === "completed")
            : records;
    const query = recordSearch.trim().toLowerCase();
    if (!query) {
      return statusFilteredRecords;
    }
    return statusFilteredRecords.filter((record) =>
      [
        record.record_no,
        record.record_id,
        record.original_filename,
        `page ${record.page_number}`,
        String(record.page_number),
        record.review_status,
        record.ocr_status,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(query),
    );
  }, [records, filterStatus, recordSearch]);

  useEffect(() => {
    async function loadAssignedRecords() {
      try {
        const response = await fetch(`${API_BASE_URL}/api/staff/records`, {
          headers: getAuthHeaders(),
        });
        const payload = await response.json();
        if (!response.ok) {
          setError(payload?.detail || "Unable to load assigned records.");
          return;
        }
        setRecords(payload);
      } catch {
        setError(`Unable to reach the backend API.`);
      } finally {
        setIsLoading(false);
      }
    }
    void loadAssignedRecords();
  }, []);

  const columns: ColumnsType<StaffAssignedRecord> = [
    {
      title: "งานตรวจ",
      dataIndex: "original_filename",
      key: "original_filename",
      render: (val, record) => (
        <Box>
          <HStack gap="8px" mb="2px">
            <FiClipboard color="#64748b" />
            <Box fontWeight="700" color="#1e293b">{val}</Box>
          </HStack>
          <Box fontSize="0.75rem" color="#94a3b8" ml="24px">
            Page {record.page_number} · ID: {record.record_id.slice(0, 8)}
          </Box>
        </Box>
      ),
    },
    {
      title: "ประเภท",
      dataIndex: "selected_document_type",
      key: "type",
      width: 100,
      render: (val) => (
        <Box display="inline-block" px="8px" py="2px" borderRadius="6px" bg="#f1f5f9" color="#475569" fontWeight="700" fontSize="0.7rem" border="1px solid #e2e8f0">
          {val}
        </Box>
      ),
    },
    {
      title: "OCR",
      dataIndex: "ocr_status",
      key: "ocr",
      width: 120,
      render: (val) => <PremiumTag status={val} />,
    },
    {
      title: "สถานะตรวจ",
      dataIndex: "review_status",
      key: "status",
      width: 120,
      render: (val) => <PremiumTag status={val} />,
    },
    {
      title: "รับงานเมื่อ",
      dataIndex: "assigned_at",
      key: "assigned",
      width: 160,
      render: (val, record) => formatShortDate(val || record.processed_at),
    },
    {
      title: "",
      key: "actions",
      width: 120,
      render: (_, record) => (
        <Link href={`/tr-imports/${record.record_id}?source=staff`}>
          <Button 
            size="small" 
            type={record.review_status === "completed" ? "default" : "primary"}
            icon={record.review_status === "completed" ? <FiExternalLink /> : <FiExternalLink />}
            style={{ 
              borderRadius: '8px', 
              fontWeight: 600,
              ...(record.review_status === "completed" ? {
                color: '#475569',
                borderColor: '#e2e8f0',
                background: 'white',
                boxShadow: '0 2px 4px rgba(0,0,0,0.02)'
              } : {
                background: '#136360',
                borderColor: '#136360',
                boxShadow: '0 2px 8px rgba(19, 99, 96, 0.2)'
              })
            }}
          >
            {record.review_status === "completed" ? "View" : "Open"}
          </Button>
        </Link>
      ),
    },
  ];

  return (
    <ProtectedRolePage
      allowedRole="staff"
      eyebrow="Staff"
      title="Staff Dashboard"
      contentMaxW="min(1400px, calc(100vw - 80px))"
      stats={[]}
    >
      <ConfigProvider
        theme={{
          token: { borderRadius: 8, fontFamily: 'inherit' },
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
        <Flex direction="column" gap="24px">
          {/* Metrics Grid */}
          <Grid templateColumns="repeat(3, 1fr)" gap="16px">
            <MetricCard 
              title="All Jobs" 
              value={stats.total} 
              color="#64748b" 
              bg="#f1f5f9"
              iconColor="#475569"
              iconType="all"
              isActive={filterStatus === null}
              onClick={() => setFilterStatus(null)}
            />
            <MetricCard 
              title="In Progress" 
              value={stats.inProgress} 
              color="#3b82f6" 
              bg="#eff6ff"
              iconColor="#2563eb"
              iconType="pending"
              isActive={filterStatus === 'pending'}
              onClick={() => setFilterStatus('pending')}
            />
            <MetricCard 
              title="Completed" 
              value={stats.completed} 
              color="#10b981" 
              bg="#f0fdf4"
              iconColor="#059669"
              iconType="completed"
              isActive={filterStatus === 'completed'}
              onClick={() => setFilterStatus('completed')}
            />
          </Grid>

          {/* Table Area */}
          <Box bg="white" borderRadius="20px" p="24px" border="1px solid rgba(226, 232, 240, 0.6)" boxShadow="0 10px 40px rgba(0, 0, 0, 0.02)">
            <Flex justify="space-between" align="center" mb="16px">
              <Title level={4} style={{ margin: 0, color: '#1e293b', fontWeight: 800 }}>Assigned Records</Title>
              <Flex gap="12px" align="center" wrap="wrap" justify="flex-end">
                <Input
                  allowClear
                  prefix={<FiSearch color="#94a3b8" />}
                  placeholder="Search record no, file, page"
                  value={recordSearch}
                  onChange={(event) => setRecordSearch(event.target.value)}
                  style={{ width: 320, borderRadius: 10, height: 38 }}
                />
                <Box fontSize="0.85rem" color="#64748b" fontWeight="600">
                  {filteredRecords.length} records
                </Box>
              </Flex>
            </Flex>

            {error && <Alert showIcon type="error" title={error} style={{ marginBottom: 16 }} />}

            <Box borderRadius="12px" overflow="hidden" border="1px solid #f1f5f9">
              <Table<StaffAssignedRecord>
                columns={columns}
                dataSource={filteredRecords}
                loading={isLoading}
                rowKey="record_id"
                size="small"
                pagination={{ pageSize: 10, showSizeChanger: true }}
              />
            </Box>
          </Box>
        </Flex>
      </ConfigProvider>
    </ProtectedRolePage>
  );
}

// Reusable Metric Card (Copy from manager for consistency)
function MetricCard({ 
  title, value, color, bg, iconColor, isActive, onClick, iconType 
}: { 
  title: string, value: number, color: string, bg: string, iconColor: string, isActive?: boolean, onClick?: () => void, iconType: 'all' | 'pending' | 'completed' 
}) {
  let Icon = FiDatabase;
  if (iconType === 'pending') Icon = FiClock;
  else if (iconType === 'completed') Icon = FiCheckCircle;

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
      _hover={{ transform: 'translateY(-2px)', borderColor: color, bg: "white" }}
      position="relative"
      overflow="hidden"
    >
      {isActive && <Box position="absolute" left={0} top={0} bottom={0} w="4px" bg={color} />}
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
