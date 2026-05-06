"use client";

import Link from "next/link";
import { Button, Card, Typography } from "antd";
import { FiUploadCloud } from "react-icons/fi";

import { ProtectedRolePage } from "../auth/protected-role-page";

const { Text, Title } = Typography;

export default function ManagerPage() {
  return (
    <ProtectedRolePage
      allowedRole="manager"
      eyebrow="Manager"
      title="Manager Dashboard"
      stats={[
        { label: "Batches", value: 0 },
        { label: "Records", value: 0 },
        { label: "Staff workload", value: 0 },
      ]}
    >
      <section className="roleActionGrid">
        <Card className="roleActionCard">
          <div className="rolePlaceholderIcon">
            <FiUploadCloud />
          </div>
          <div className="roleActionCopy">
            <Title level={3} style={{ margin: 0 }}>
              Upload TR Documents
            </Title>
            <Text style={{ color: "#64748b" }}>
              Start a TR batch upload with PDF-only validation for Phase 1.
            </Text>
          </div>
          <Link href="/manager/upload">
            <Button type="primary" icon={<FiUploadCloud />}>
              Upload TR Documents
            </Button>
          </Link>
        </Card>
      </section>
    </ProtectedRolePage>
  );
}
