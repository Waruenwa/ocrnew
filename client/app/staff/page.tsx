import { ProtectedRolePage } from "../auth/protected-role-page";

export default function StaffPage() {
  return (
    <ProtectedRolePage
      allowedRole="staff"
      eyebrow="Staff"
      title="Staff Dashboard"
      stats={[
        { label: "Assigned", value: 0 },
        { label: "Completed", value: 0 },
        { label: "Remaining", value: 0 },
      ]}
    />
  );
}
