"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Alert, Button, Card, Radio, Typography } from "antd";
import { FiArrowLeft, FiFileText, FiUploadCloud } from "react-icons/fi";

import { ProtectedRolePage } from "../../auth/protected-role-page";

const { Text, Title } = Typography;

const MAX_FILES_PER_BATCH = 20;
const SUPPORTED_DOCUMENT_TYPE = "TR";

type UploadMessage = {
  type: "success" | "error" | "info";
  text: string;
};

function isPdfFile(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function validateFiles(files: File[]) {
  if (files.length > MAX_FILES_PER_BATCH) {
    return `Please select no more than ${MAX_FILES_PER_BATCH} PDF files.`;
  }

  const nonPdfFile = files.find((file) => !isPdfFile(file));
  if (nonPdfFile) {
    return `Only PDF files are supported for TR uploads. Remove "${nonPdfFile.name}" and try again.`;
  }

  return null;
}

export default function ManagerUploadPage() {
  const [documentType, setDocumentType] = useState<string>(SUPPORTED_DOCUMENT_TYPE);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [message, setMessage] = useState<UploadMessage | null>(null);

  const fileValidationError = useMemo(() => validateFiles(selectedFiles), [selectedFiles]);
  const submitDisabled =
    documentType !== SUPPORTED_DOCUMENT_TYPE ||
    selectedFiles.length === 0 ||
    Boolean(fileValidationError);

  function handleFileSelection(fileList: FileList | null) {
    const nextFiles = Array.from(fileList ?? []);
    const nextValidationError = validateFiles(nextFiles);

    setSelectedFiles(nextFiles);
    setValidationError(nextValidationError);
    setMessage(null);
  }

  function handleSubmit() {
    if (documentType !== SUPPORTED_DOCUMENT_TYPE) {
      setValidationError("Please select document type TR before uploading.");
      return;
    }

    if (selectedFiles.length === 0) {
      setValidationError("Please select at least one PDF file.");
      return;
    }

    const nextValidationError = validateFiles(selectedFiles);
    if (nextValidationError) {
      setValidationError(nextValidationError);
      return;
    }

    setValidationError(null);
    setMessage({
      type: "info",
      text: "Upload API integration will be implemented in a later PR.",
    });
  }

  return (
    <ProtectedRolePage
      allowedRole="manager"
      eyebrow="Manager Upload"
      title="Upload TR Documents"
      stats={[
        { label: "Document type", value: documentType || "Required" },
        { label: "Selected files", value: selectedFiles.length },
        { label: "Maximum files", value: MAX_FILES_PER_BATCH },
      ]}
    >
      <section className="roleUploadLayout">
        <Card className="roleUploadCard">
          <div className="roleUploadHeader">
            <Link href="/manager">
              <Button icon={<FiArrowLeft />}>Back to Manager</Button>
            </Link>
            <Text style={{ color: "#64748b" }}>Phase 1 supports TR documents only.</Text>
          </div>

          {(validationError || fileValidationError) && (
            <Alert
              showIcon
              type="error"
              title={validationError || fileValidationError}
              style={{ marginBottom: 18 }}
            />
          )}

          {message && (
            <Alert
              showIcon
              type={message.type}
              title={message.text}
              style={{ marginBottom: 18 }}
            />
          )}

          <div className="roleUploadSection">
            <div>
              <Title level={4} style={{ margin: 0 }}>
                Document Type
              </Title>
              <Text style={{ color: "#64748b" }}>
                Select TR before choosing files for this batch.
              </Text>
            </div>
            <Radio.Group
              value={documentType}
              onChange={(event) => {
                setDocumentType(event.target.value);
                setValidationError(null);
                setMessage(null);
              }}
            >
              <Radio.Button value={SUPPORTED_DOCUMENT_TYPE}>TR</Radio.Button>
            </Radio.Group>
          </div>

          <div className="roleUploadSection">
            <div>
              <Title level={4} style={{ margin: 0 }}>
                PDF Files
              </Title>
              <Text style={{ color: "#64748b" }}>
                Select up to {MAX_FILES_PER_BATCH} PDF files. OCR integration comes later.
              </Text>
            </div>
            <label className="roleFilePicker">
              <FiUploadCloud />
              <span>Select PDF files</span>
              <input
                type="file"
                accept="application/pdf,.pdf"
                multiple
                onChange={(event) => handleFileSelection(event.target.files)}
              />
            </label>
          </div>

          <div className="roleSelectedFiles">
            <div className="roleSelectedFilesHeader">
              <Title level={4} style={{ margin: 0 }}>
                Selected Files
              </Title>
              <Text>{selectedFiles.length} selected</Text>
            </div>

            {selectedFiles.length === 0 ? (
              <div className="roleEmptyFiles">No files selected.</div>
            ) : (
              <ul className="roleFileList">
                {selectedFiles.map((file) => (
                  <li key={`${file.name}-${file.size}-${file.lastModified}`}>
                    <FiFileText />
                    <span>{file.name}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="roleUploadActions">
            <Button onClick={() => {
              setSelectedFiles([]);
              setValidationError(null);
              setMessage(null);
            }}>
              Clear
            </Button>
            <Button
              type="primary"
              icon={<FiUploadCloud />}
              disabled={submitDisabled}
              onClick={handleSubmit}
            >
              Submit TR Upload
            </Button>
          </div>
        </Card>
      </section>
    </ProtectedRolePage>
  );
}
