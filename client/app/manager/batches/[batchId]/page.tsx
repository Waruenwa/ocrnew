"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { Alert, Button, Card, InputNumber, Select, Spin, Tag, Typography } from "antd";
import { FiArrowLeft, FiClipboard, FiRefreshCw, FiUserPlus } from "react-icons/fi";

import { ProtectedRolePage } from "../../../auth/protected-role-page";
import { getAuthHeaders } from "../../../lib/auth";
import { API_BASE_URL } from "../../../lib/review";

const { Text, Title } = Typography;

type BatchFile = {
  file_id: string;
  original_filename: string;
  stored_filename: string;
  mime_type: string | null;
  file_size_bytes: number;
  page_count: number | null;
  original_path: string;
  derived_root: string;
  status: string;
  error_message: string | null;
};

type BatchRecord = {
  record_id: string;
  file_id: string;
  original_filename: string;
  page_number: number;
  has_watermark: boolean | null;
  ocr_status: string;
  review_status: string;
  ocr_error: string | null;
  processed_at: string | null;
  assigned_to_user_id: string | null;
  assigned_to_username: string | null;
  assigned_at: string | null;
};

type BatchDetail = {
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
  unassigned_count: number;
  in_review_count: number;
  completed_count: number;
  files: BatchFile[];
  records: BatchRecord[];
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

const ACTIVE_STATUSES = new Set(["records_created", "ocr_processing"]);

function statusTagColor(status: string) {
  if (status === "ocr_completed" || status === "succeeded" || status === "assigned") {
    return "green";
  }
  if (status === "partially_failed" || status === "failed" || status === "page_limit_exceeded") {
    return "red";
  }
  if (status === "ocr_processing" || status === "processing") {
    return "blue";
  }
  return "default";
}

export default function ManagerBatchDetailPage() {
  const params = useParams<{ batchId: string }>();
  const rawBatchId = params.batchId;
  const batchId = Array.isArray(rawBatchId) ? rawBatchId[0] : rawBatchId;
  const [batch, setBatch] = useState<BatchDetail | null>(null);
  const [staffUsers, setStaffUsers] = useState<StaffUser[]>([]);
  const [staffLoadError, setStaffLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [assignmentMessage, setAssignmentMessage] = useState<AssignmentMessage | null>(null);
  const [selectedStaffUserId, setSelectedStaffUserId] = useState<string | null>(null);
  const [assignCount, setAssignCount] = useState<number | null>(1);
  const [isLoading, setIsLoading] = useState(true);
  const [isAssigning, setIsAssigning] = useState(false);

  const shouldPoll = batch ? ACTIVE_STATUSES.has(batch.status) : true;
  const fileErrors = useMemo(
    () => batch?.files.filter((file) => file.error_message || file.status === "page_limit_exceeded") ?? [],
    [batch],
  );
  const recordErrors = useMemo(
    () => batch?.records.filter((record) => record.ocr_status === "failed" && record.ocr_error) ?? [],
    [batch],
  );

  async function loadBatch() {
    if (!batchId) {
      setError("Batch ID is missing from the page URL.");
      return;
    }

    setIsLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/manager/batches/${batchId}`, {
        headers: getAuthHeaders(),
      });
      const payload = (await response.json().catch(() => null)) as
        | BatchDetail
        | { detail?: unknown }
        | null;

      if (!response.ok) {
        const detail = String((payload as { detail?: unknown } | null)?.detail || "Unable to load batch.");
        setError(response.status === 404 ? `Batch not found: ${batchId}` : detail);
        return;
      }

      setBatch(payload as BatchDetail);
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

  async function handleAssignRecords() {
    if (!batch) {
      return;
    }
    if (!selectedStaffUserId) {
      setAssignmentMessage({ type: "error", text: "Please select a staff user." });
      return;
    }
    if (!assignCount || assignCount < 1) {
      setAssignmentMessage({ type: "error", text: "Please enter a valid assignment count." });
      return;
    }
    if (assignCount > batch.ready_to_assign_count) {
      setAssignmentMessage({
        type: "error",
        text: `Cannot assign ${assignCount} records; only ${batch.ready_to_assign_count} are ready.`,
      });
      return;
    }

    const selectedStaff = staffUsers.find((staffUser) => staffUser.user_id === selectedStaffUserId);
    setIsAssigning(true);
    setAssignmentMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/manager/batches/${batch.batch_id}/assign`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders(),
        },
        body: JSON.stringify({
          staff_user_id: selectedStaffUserId,
          staff_username: selectedStaff?.username ?? selectedStaffUserId,
          count: assignCount,
        }),
      });
      const payload = (await response.json().catch(() => null)) as BatchDetail | { detail?: unknown } | null;
      if (!response.ok) {
        setAssignmentMessage({
          type: "error",
          text: String((payload as { detail?: unknown } | null)?.detail || "Assignment failed."),
        });
        return;
      }

      setBatch(payload as BatchDetail);
      setAssignmentMessage({
        type: "success",
        text: `Assigned ${assignCount} record${assignCount === 1 ? "" : "s"} successfully.`,
      });
      setAssignCount(1);
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
    void loadBatch();
    void loadStaffUsers();
  }, [batchId]);

  useEffect(() => {
    if (!shouldPoll) {
      return;
    }

    const intervalId = setInterval(() => {
      void loadBatch();
    }, 3000);

    return () => clearInterval(intervalId);
  }, [batchId, shouldPoll]);

  return (
    <ProtectedRolePage
      allowedRole="manager"
      eyebrow="Manager Batch"
      title="Batch Detail"
      stats={[
        { label: "Files", value: batch?.file_count ?? 0 },
        { label: "Records", value: batch?.record_count ?? 0 },
        { label: "Ready to assign", value: batch?.ready_to_assign_count ?? 0 },
        { label: "Assigned", value: batch?.assigned_count ?? 0 },
      ]}
    >
      <section className="roleUploadLayout">
        <Card className="roleUploadCard">
          <div className="roleUploadHeader">
            <Link href="/manager">
              <Button icon={<FiArrowLeft />}>Back to Manager</Button>
            </Link>
            <Button icon={<FiRefreshCw />} onClick={() => void loadBatch()} loading={isLoading}>
              Refresh
            </Button>
          </div>

          {error && <Alert showIcon type="error" title={error} style={{ marginBottom: 18 }} />}

          {isLoading && !batch ? (
            <div className="roleEmptyFiles">
              <Spin />
            </div>
          ) : null}

          {batch ? (
            <>
              <div className="roleUploadResult">
                <div>
                  <Text style={{ color: "#64748b" }}>Batch ID</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.batch_id}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>Document Type</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.selected_document_type}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>Batch Status</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.status}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>Total Pages</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.total_pages}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>OCR Pending</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.ocr_pending_count}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>OCR Processing</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.ocr_processing_count}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>OCR Succeeded</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.ocr_succeeded_count}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>OCR Failed</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.ocr_failed_count}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>Ready to Assign</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.ready_to_assign_count}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>Assigned</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.assigned_count}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>In Review</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.in_review_count}
                  </Title>
                </div>
                <div>
                  <Text style={{ color: "#64748b" }}>Completed</Text>
                  <Title level={5} style={{ margin: 0 }}>
                    {batch.completed_count}
                  </Title>
                </div>
              </div>

              {ACTIVE_STATUSES.has(batch.status) ? (
                <Alert
                  showIcon
                  type="info"
                  title="OCR is processing. Results will appear here automatically."
                  style={{ marginBottom: 18 }}
                />
              ) : null}

              {batch.ready_to_assign_count > 0 ? (
                <div className="roleOcrActions">
                  <Select
                    style={{ minWidth: 240 }}
                    placeholder="Select staff"
                    value={selectedStaffUserId}
                    onChange={(value) => {
                      setSelectedStaffUserId(value);
                      setAssignmentMessage(null);
                    }}
                    options={staffUsers.map((staffUser) => ({
                      value: staffUser.user_id,
                      label: `${staffUser.display_name} (${staffUser.username})`,
                    }))}
                  />
                  <InputNumber
                    min={1}
                    max={batch.ready_to_assign_count}
                    value={assignCount}
                    onChange={(value) => {
                      setAssignCount(value);
                      setAssignmentMessage(null);
                    }}
                  />
                  <Button
                    type="primary"
                    icon={<FiUserPlus />}
                    loading={isAssigning}
                    disabled={!staffUsers.length}
                    onClick={() => void handleAssignRecords()}
                  >
                    Assign Records
                  </Button>
                  <Text style={{ color: "#64748b" }}>
                    Assigns the first ready OCR-success records in this batch.
                  </Text>
                  {staffLoadError ? <Text type="danger">{staffLoadError}</Text> : null}
                </div>
              ) : null}

              {assignmentMessage ? (
                <Alert
                  showIcon
                  type={assignmentMessage.type}
                  title={assignmentMessage.text}
                  style={{ marginBottom: 18 }}
                />
              ) : null}

              <div className="roleSelectedFiles">
                <div className="roleSelectedFilesHeader">
                  <Title level={4} style={{ margin: 0 }}>
                    Files
                  </Title>
                  <Text>{batch.files.length} files</Text>
                </div>

                {batch.files.length === 0 ? (
                  <div className="roleEmptyFiles">No files in this batch.</div>
                ) : (
                  <ul className="roleFileList">
                    {batch.files.map((file) => (
                      <li key={file.file_id}>
                        <FiClipboard />
                        <span>{file.original_filename}</span>
                        <Tag color={statusTagColor(file.status)}>{file.status}</Tag>
                        <Text style={{ color: "#64748b" }}>
                          {file.page_count ?? 0} page{file.page_count === 1 ? "" : "s"}
                        </Text>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {fileErrors.length ? (
                <div className="roleFileErrors">
                  <Title level={5} style={{ margin: 0 }}>
                    File Issues
                  </Title>
                  <ul>
                    {fileErrors.map((file) => (
                      <li key={file.file_id}>
                        <strong>{file.original_filename}</strong>: {file.error_message || file.status}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="roleSelectedFiles">
                <div className="roleSelectedFilesHeader">
                  <Title level={4} style={{ margin: 0 }}>
                    Records
                  </Title>
                  <Text>{batch.records.length} records</Text>
                </div>

                {batch.records.length === 0 ? (
                  <div className="roleEmptyFiles">No review records were created.</div>
                ) : (
                  <ul className="roleFileList">
                    {batch.records.map((record) => (
                      <li key={record.record_id}>
                        <FiClipboard />
                        <span>
                          {record.original_filename} page {record.page_number}
                        </span>
                        <Tag color={statusTagColor(record.ocr_status)}>{record.ocr_status}</Tag>
                        <Tag color={statusTagColor(record.review_status)}>{record.review_status}</Tag>
                        {record.has_watermark === null ? null : (
                          <Text style={{ color: "#64748b" }}>
                            {record.has_watermark ? "Watermark" : "No watermark"}
                          </Text>
                        )}
                        {record.assigned_to_username ? (
                          <Text style={{ color: "#64748b" }}>
                            Assigned to {record.assigned_to_username}
                          </Text>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {recordErrors.length ? (
                <div className="roleFileErrors">
                  <Title level={5} style={{ margin: 0 }}>
                    OCR Errors
                  </Title>
                  <ul>
                    {recordErrors.map((record) => (
                      <li key={record.record_id}>
                        <strong>
                          {record.original_filename} page {record.page_number}
                        </strong>
                        : {record.ocr_error}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : null}
        </Card>
      </section>
    </ProtectedRolePage>
  );
}
