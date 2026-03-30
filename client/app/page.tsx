"use client";

import { CSSProperties, FormEvent, useEffect, useRef, useState } from "react";

type JobStatus = "queued" | "processing" | "completed" | "failed";
type ResultTab = "ocr" | "structured";

type TextSegment = {
  id: string;
  text: string;
  page_number: number;
  bbox: [number, number, number, number];
};

type PageResult = {
  page_number: number;
  markdown: string;
  segments: TextSegment[];
};

type JobRecord = {
  id: string;
  filename: string;
  mime_type: string | null;
  status: JobStatus;
  total_pages: number;
  processed_pages: number;
  extraction_prompt: string | null;
  ocr_markdown: string | null;
  structured_output: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  pages: PageResult[];
};

type AppConfig = {
  ocr_ready: boolean;
  extraction_ready: boolean;
  max_upload_mb: number;
  text_model: string;
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const statusLabels: Record<JobStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
};

export default function Home() {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<JobRecord | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [extractionPrompt, setExtractionPrompt] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [activeTab, setActiveTab] = useState<ResultTab>("ocr");
  const [copyLabel, setCopyLabel] = useState("Copy text");
  const [activePageNumber, setActivePageNumber] = useState(1);
  const [activeSegmentId, setActiveSegmentId] = useState<string | null>(null);

  const previewStageRef = useRef<HTMLDivElement | null>(null);
  const resultSurfaceRef = useRef<HTMLDivElement | null>(null);
  const selectedStatus = selectedJob?.status;
  const selectedPage = selectedJob?.pages.find((page) => page.page_number === activePageNumber) ?? null;
  const activeSegment =
    selectedPage?.segments.find((segment) => segment.id === activeSegmentId) ?? null;

  useEffect(() => {
    void loadDashboard();
  }, []);

  useEffect(() => {
    if (!selectedJobId) {
      return;
    }

    void fetchJob(selectedJobId);

    if (!selectedStatus || !isRunning(selectedStatus)) {
      return;
    }

    const interval = window.setInterval(() => {
      void fetchJob(selectedJobId);
      void fetchJobs();
    }, 2500);

    return () => window.clearInterval(interval);
  }, [selectedJobId, selectedStatus]);

  useEffect(() => {
    setCopyLabel("Copy text");
  }, [selectedJobId, activeTab]);

  useEffect(() => {
    setActivePageNumber(1);
    setActiveSegmentId(null);
  }, [selectedJobId]);

  useEffect(() => {
    if (!selectedJob?.pages.length) {
      return;
    }

    const hasPage = selectedJob.pages.some((page) => page.page_number === activePageNumber);
    if (!hasPage) {
      setActivePageNumber(selectedJob.pages[0].page_number);
    }
  }, [selectedJob, activePageNumber]);

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
        behavior: "smooth",
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
      behavior: "smooth",
    });
  }, [activePageNumber]);

  async function loadDashboard() {
    try {
      await Promise.all([fetchConfig(), fetchJobs()]);
    } catch {
      setErrorMessage("ยังติดต่อ backend ไม่ได้ ตรวจสอบว่า server รันอยู่และ API URL ถูกต้อง");
    }
  }

  async function fetchConfig() {
    const response = await fetch(`${API_BASE_URL}/api/config`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("Unable to load app config");
    }

    const data = (await response.json()) as AppConfig;
    setConfig(data);
  }

  async function fetchJobs() {
    const response = await fetch(`${API_BASE_URL}/api/jobs`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("Unable to load jobs");
    }

    const data = (await response.json()) as JobRecord[];
    setJobs(data);
    syncSelection(data);
  }

  function syncSelection(nextJobs: JobRecord[]) {
    if (nextJobs.length === 0) {
      setSelectedJobId(null);
      setSelectedJob(null);
      return;
    }

    const nextSelectedId = nextJobs.some((job) => job.id === selectedJobId)
      ? selectedJobId
      : nextJobs[0].id;

    const nextSelectedJob = nextJobs.find((job) => job.id === nextSelectedId) ?? null;
    setSelectedJobId(nextSelectedId);
    setSelectedJob(nextSelectedJob);
  }

  async function fetchJob(jobId: string) {
    const response = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      return;
    }

    const data = (await response.json()) as JobRecord;
    setSelectedJob(data);
    setJobs((currentJobs) =>
      currentJobs.map((job) => (job.id === data.id ? data : job)),
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setErrorMessage("เลือกไฟล์ PDF หรือรูปภาพก่อน");
      return;
    }

    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("extraction_prompt", extractionPrompt);

      const response = await fetch(`${API_BASE_URL}/api/jobs`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { detail?: string }
          | null;
        throw new Error(payload?.detail || "Upload failed");
      }

      const data = (await response.json()) as JobRecord;
      setFile(null);
      setExtractionPrompt("");
      setActiveTab("ocr");
      setSelectedJobId(data.id);
      setSelectedJob(data);
      setJobs((currentJobs) => [data, ...currentJobs.filter((job) => job.id !== data.id)]);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleCopyResult() {
    const resultText = getActiveResultText(selectedJob, activeTab, config);
    if (!resultText || !navigator.clipboard) {
      return;
    }

    try {
      await navigator.clipboard.writeText(resultText);
      setCopyLabel("Copied");
      window.setTimeout(() => setCopyLabel("Copy text"), 1600);
    } catch {
      setCopyLabel("Copy failed");
      window.setTimeout(() => setCopyLabel("Copy text"), 1600);
    }
  }

  async function handleRetryJob() {
    if (!selectedJob) {
      return;
    }

    setErrorMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/jobs/${selectedJob.id}/retry`, {
        method: "POST",
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { detail?: string }
          | null;
        throw new Error(payload?.detail || "Retry failed");
      }

      const data = (await response.json()) as JobRecord;
      setActiveTab("ocr");
      setSelectedJob(data);
      setJobs((currentJobs) =>
        currentJobs.map((job) => (job.id === data.id ? data : job)),
      );
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Retry failed");
    }
  }

  async function handleRefreshBoxes() {
    if (!selectedJob) {
      return;
    }

    setErrorMessage(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/jobs/${selectedJob.id}/rebuild-segments`, {
        method: "POST",
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { detail?: string }
          | null;
        throw new Error(payload?.detail || "Refresh boxes failed");
      }

      const data = (await response.json()) as JobRecord;
      setSelectedJob(data);
      setJobs((currentJobs) =>
        currentJobs.map((job) => (job.id === data.id ? data : job)),
      );
      setActiveSegmentId(null);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Refresh boxes failed");
    }
  }

  function handleSelectJob(job: JobRecord) {
    setSelectedJobId(job.id);
    setSelectedJob(job);
    setActivePageNumber(1);
    setActiveSegmentId(null);
    setCopyLabel("Copy text");
  }

  function handleSelectPage(pageNumber: number) {
    setActivePageNumber(pageNumber);
    setActiveSegmentId(null);
  }

  function handleSelectSegment(segment: TextSegment) {
    setActivePageNumber(segment.page_number);
    setActiveSegmentId(segment.id);
  }

  const originalUrl = selectedJob ? `${API_BASE_URL}/api/jobs/${selectedJob.id}/file` : "";
  const previewUrl =
    selectedJob && selectedPage
      ? `${API_BASE_URL}/api/jobs/${selectedJob.id}/pages/${selectedPage.page_number}/preview`
      : "";
  const resultBody = getActiveResultText(selectedJob, activeTab, config);
  const canCopyResult =
    !!selectedJob &&
    ((activeTab === "ocr" && !!selectedJob.ocr_markdown) ||
      (activeTab === "structured" && !!selectedJob.structured_output));

  return (
    <main className="shell">
      <section className="hero">
        <div className="heroCopy">
          <p className="eyebrow">Document OCR</p>
          <h1>กดข้อความฝั่งขวา แล้วดูตำแหน่งจริงฝั่งซ้ายได้ทันที</h1>
          <p className="lead">
            ผมปรับ flow ให้ใกล้เว็บ OCR ทั่วไปขึ้นแล้ว:
            ด้านขวาเป็นรายการข้อความที่กดได้ และเมื่อกดแล้วระบบจะเปิดหน้าที่เกี่ยวข้องพร้อมขึ้นกรอบบนต้นฉบับให้เอง
          </p>
        </div>

        <div className="heroStats">
          <div className="statCard">
            <span>OCR Engine</span>
            <strong>typhoon-ocr</strong>
          </div>
          <div className="statCard">
            <span>Interaction</span>
            <strong>Click Text To Highlight</strong>
          </div>
          <div className="statCard">
            <span>Max Upload</span>
            <strong>{config?.max_upload_mb ?? "--"} MB</strong>
          </div>
        </div>
      </section>

      <section className="topGrid">
        <section className="panel uploadPanel">
          <div className="sectionHeader">
            <div>
              <p className="stepTag">Step 1</p>
              <h2>Upload Document</h2>
            </div>
            <p>รองรับ PDF, PNG, JPG และ JPEG</p>
          </div>

          {config && !config.ocr_ready ? (
            <div className="warningBox">
              <strong>OCR key ยังไม่ถูกตั้งค่า</strong>
              <p>
                เพิ่ม `TYPHOON_OCR_API_KEY` หรือ `TYPHOON_API_KEY` ใน `server/.env`
                ก่อนเริ่มอัปโหลด
              </p>
            </div>
          ) : null}

          <form className="uploadForm" onSubmit={handleSubmit}>
            <label
              className={`dropzone ${dragActive ? "isActive" : ""}`}
              onDragOver={(event) => {
                event.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragActive(false);
                const nextFile = event.dataTransfer.files.item(0);
                if (nextFile) {
                  setFile(nextFile);
                }
              }}
            >
              <input
                type="file"
                accept=".pdf,.png,.jpg,.jpeg"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <span className="dropTitle">
                {file ? file.name : "ลากไฟล์มาวาง หรือกดเพื่อเลือกไฟล์"}
              </span>
              <span className="dropHint">
                หลัง OCR เสร็จ คุณจะกดข้อความฝั่งขวาเพื่อดูตำแหน่งบนต้นฉบับได้
              </span>
            </label>

            <label className="fieldLabel" htmlFor="prompt">
              Structured extraction (optional)
            </label>
            <textarea
              id="prompt"
              className="promptInput"
              value={extractionPrompt}
              onChange={(event) => setExtractionPrompt(event.target.value)}
              placeholder="ตัวอย่าง: ดึงเลขเอกสาร, วันที่, ชื่อคู่สัญญา, ยอดรวม และรายการสินค้าเป็น JSON"
              rows={5}
            />

            <button className="primaryButton" disabled={isSubmitting} type="submit">
              {isSubmitting ? "Uploading..." : "Start OCR"}
            </button>

            {errorMessage ? <p className="errorText">{errorMessage}</p> : null}
          </form>
        </section>

        <section className="panel jobsPanel">
          <div className="sectionHeader">
            <div>
              <p className="stepTag">Step 2</p>
              <h2>Select Job</h2>
            </div>
            <p>เลือกเอกสารที่ต้องการเปิดดูต้นฉบับและผลลัพธ์</p>
          </div>

          <div className="jobList">
            {jobs.map((job) => (
              <button
                className={`jobCard ${selectedJobId === job.id ? "isSelected" : ""}`}
                key={job.id}
                onClick={() => handleSelectJob(job)}
                type="button"
              >
                <div className="jobCardTop">
                  <div>
                    <strong>{job.filename}</strong>
                    <p>
                      {job.processed_pages}/{job.total_pages} pages
                    </p>
                  </div>
                  <span className={`statusPill status-${job.status}`}>
                    {statusLabels[job.status]}
                  </span>
                </div>
                <small>{formatDate(job.created_at)}</small>
              </button>
            ))}

            {jobs.length === 0 ? (
              <div className="emptyInlineState">
                ยังไม่มีงาน OCR อัปโหลดเอกสารแรกเพื่อเริ่มใช้งาน
              </div>
            ) : null}
          </div>
        </section>
      </section>

      <section className="workspaceGrid">
        <section className="panel previewPanel">
          <div className="workspaceHeader">
            <div>
              <p className="stepTag">Original</p>
              <h2>ต้นฉบับเอกสาร</h2>
            </div>

            {selectedJob ? (
              <a className="ghostLink" href={originalUrl} rel="noreferrer" target="_blank">
                Open original
              </a>
            ) : null}
          </div>

          {selectedJob ? (
            <>
              <div className="metaRow">
                <div className="metaCard">
                  <span>Filename</span>
                  <strong>{selectedJob.filename}</strong>
                </div>
                <div className="metaCard">
                  <span>Status</span>
                  <strong>{statusLabels[selectedJob.status]}</strong>
                </div>
                <div className="metaCard">
                  <span>Created</span>
                  <strong>{formatDate(selectedJob.created_at)}</strong>
                </div>
              </div>

              <div className="pageNav">
                {selectedJob.pages.map((page) => (
                  <button
                    className={`pageChip ${activePageNumber === page.page_number ? "isActive" : ""}`}
                    key={`${selectedJob.id}-page-${page.page_number}`}
                    onClick={() => handleSelectPage(page.page_number)}
                    type="button"
                  >
                    Page {page.page_number}
                  </button>
                ))}
              </div>

              <div className="interactionHint">
                คลิกข้อความฝั่งขวา แล้วกรอบตำแหน่งจะขึ้นบนหน้าต้นฉบับตรงนี้
              </div>

              <div className="previewStage" ref={previewStageRef}>
                {selectedPage ? (
                  <div className="previewCanvas">
                    <img
                      alt={`${selectedJob.filename} page ${selectedPage.page_number}`}
                      className="previewImage"
                      src={previewUrl}
                    />
                    {activeSegment && activeSegment.page_number === selectedPage.page_number ? (
                      <div
                        className="highlightBox"
                        style={getHighlightStyle(activeSegment.bbox)}
                      />
                    ) : null}
                  </div>
                ) : (
                  <div className="emptyWorkspace">
                    <p className="stepTag">Original</p>
                    <h2>ยังไม่มีหน้าสำหรับ preview</h2>
                    <p>เมื่อ OCR เสร็จแล้ว ระบบจะแปลงเป็นหน้า preview ที่วางกรอบ highlight ได้</p>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="emptyWorkspace">
              <p className="stepTag">Original</p>
              <h2>ยังไม่ได้เลือกเอกสารต้นฉบับ</h2>
              <p>อัปโหลดไฟล์หรือเลือก job จากรายการด้านบน แล้วไฟล์ต้นฉบับจะแสดงที่ฝั่งนี้</p>
            </div>
          )}
        </section>

        <section className="panel resultPanel">
          <div className="workspaceHeader">
            <div>
              <p className="stepTag">Result</p>
              <h2>ผลลัพธ์ที่สแกนออกมา</h2>
            </div>

            <div className="headerActions">
              {selectedJob ? (
                <button
                  className="secondaryButton"
                  onClick={() => void handleRefreshBoxes()}
                  type="button"
                >
                  Refresh boxes
                </button>
              ) : null}
              {selectedJob ? (
                <button
                  className="secondaryButton"
                  onClick={() => void handleRetryJob()}
                  type="button"
                >
                  Retry OCR
                </button>
              ) : null}
              <button
                className="secondaryButton"
                disabled={!canCopyResult}
                onClick={() => void handleCopyResult()}
                type="button"
              >
                {copyLabel}
              </button>
            </div>
          </div>

          {selectedJob ? (
            <>
              <div className="statusBannerRow">
                <div className={`statusBanner status-${selectedJob.status}`}>
                  {statusLabels[selectedJob.status]}
                </div>
                <span className="progressLabel">
                  {selectedJob.processed_pages}/{selectedJob.total_pages} pages processed
                </span>
              </div>

              <div className="progressTrack" aria-hidden="true">
                <div
                  className="progressBar"
                  style={{
                    width: `${Math.max(
                      6,
                      Math.round(
                        (selectedJob.processed_pages / Math.max(selectedJob.total_pages, 1)) *
                          100,
                      ),
                    )}%`,
                  }}
                />
              </div>

              {selectedJob.error_message ? (
                <div className="warningBox">
                  <strong>งานนี้ไม่สำเร็จ</strong>
                  <p>{selectedJob.error_message}</p>
                </div>
              ) : null}

              <div className="tabRow">
                <button
                  className={`tabButton ${activeTab === "ocr" ? "isActive" : ""}`}
                  onClick={() => setActiveTab("ocr")}
                  type="button"
                >
                  OCR Text
                </button>
                <button
                  className={`tabButton ${activeTab === "structured" ? "isActive" : ""}`}
                  onClick={() => setActiveTab("structured")}
                  type="button"
                >
                  Structured JSON
                </button>
              </div>

              {activeTab === "ocr" ? (
                <>
                  <div className="interactionHint">
                    คลิกบรรทัดหรือข้อความด้านล่าง เพื่อให้ฝั่งซ้ายขึ้นกรอบตำแหน่งอัตโนมัติ
                  </div>
                  <div className="resultSurface interactiveSurface" ref={resultSurfaceRef}>
                    {selectedPage ? (
                      <section className="ocrPageGroup" key={`${selectedJob.id}-${selectedPage.page_number}`}>
                        <div className="ocrPageHeader">
                          <span className="pageChip isActive">Page {selectedPage.page_number}</span>
                          <span>{selectedPage.segments.length} clickable blocks</span>
                        </div>

                        {selectedPage.segments.length > 0 ? (
                          <div className="segmentList">
                            {selectedPage.segments.map((segment) => (
                              <button
                                className={`segmentButton ${activeSegmentId === segment.id ? "isActive" : ""}`}
                                key={segment.id}
                                onClick={() => handleSelectSegment(segment)}
                                type="button"
                              >
                                {segment.text}
                              </button>
                            ))}
                          </div>
                        ) : (
                          <pre className="codeBlock compactCodeBlock">
                            {selectedPage.markdown || "OCR is still running..."}
                          </pre>
                        )}
                      </section>
                    ) : (
                      <div className="emptyInlineState">No OCR page is selected yet.</div>
                    )}
                  </div>

                  <details className="accordion">
                    <summary>Raw OCR Markdown</summary>
                    <pre className="codeBlock compactCodeBlock">
                      {selectedJob.ocr_markdown ?? "OCR is still running..."}
                    </pre>
                  </details>
                </>
              ) : (
                <div className="resultSurface">
                  <pre className="codeBlock">{resultBody}</pre>
                </div>
              )}

              {selectedJob.extraction_prompt ? (
                <div className="promptChip">
                  <span>Extraction goal</span>
                  <strong>{selectedJob.extraction_prompt}</strong>
                </div>
              ) : null}
            </>
          ) : (
            <div className="emptyWorkspace">
              <p className="stepTag">Result</p>
              <h2>ยังไม่มีผลลัพธ์ OCR</h2>
              <p>
                เมื่อเลือกเอกสารแล้ว ฝั่งนี้จะแสดงข้อความที่คลิกได้ และพาไปตำแหน่งจริงบนต้นฉบับให้ทันที
              </p>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

function isRunning(status: JobStatus) {
  return status === "queued" || status === "processing";
}

function formatDate(value: string) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("th-TH", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatStructuredOutput(value: string | null | undefined) {
  if (!value) {
    return null;
  }

  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function getHighlightStyle(bbox: [number, number, number, number]): CSSProperties {
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

function getActiveResultText(
  job: JobRecord | null,
  activeTab: ResultTab,
  config: AppConfig | null,
) {
  if (!job) {
    return "Select a job to see OCR output.";
  }

  if (activeTab === "ocr") {
    return job.ocr_markdown ?? "OCR is still running...";
  }

  return (
    formatStructuredOutput(job.structured_output) ??
    (config?.extraction_ready
      ? "No structured extraction requested for this document."
      : "Set TYPHOON_API_KEY to enable structured extraction.")
  );
}
