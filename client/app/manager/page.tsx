import { ProtectedRolePage } from "../auth/protected-role-page";

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
    />
  );
}
