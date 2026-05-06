"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Button, Card, ConfigProvider, Spin, Statistic, Typography } from "antd";
import { FiLogOut, FiShield, FiUserCheck } from "react-icons/fi";

import {
  type AuthUser,
  type UserRole,
  getCurrentUser,
  getRoleHomePath,
  logout,
} from "../lib/auth";

const { Text, Title } = Typography;

type ProtectedRolePageProps = {
  allowedRole: UserRole;
  title: string;
  eyebrow: string;
  stats: Array<{
    label: string;
    value: number | string;
  }>;
  children?: ReactNode;
};

export function ProtectedRolePage({
  allowedRole,
  title,
  eyebrow,
  stats,
  children,
}: ProtectedRolePageProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isChecking, setIsChecking] = useState(true);
  const router = useRouter();

  useEffect(() => {
    let isMounted = true;

    async function checkAccess() {
      try {
        const currentUser = await getCurrentUser();
        if (!isMounted) {
          return;
        }

        if (!currentUser) {
          router.replace("/login");
          return;
        }

        if (currentUser.role !== allowedRole) {
          router.replace(getRoleHomePath(currentUser.role));
          return;
        }

        setUser(currentUser);
      } catch {
        if (isMounted) {
          router.replace("/login");
        }
      } finally {
        if (isMounted) {
          setIsChecking(false);
        }
      }
    }

    void checkAccess();

    return () => {
      isMounted = false;
    };
  }, [allowedRole, router]);

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  if (isChecking || !user) {
    return (
      <main className="roleShell roleShellCentered">
        <Spin size="large" />
      </main>
    );
  }

  return (
    <ConfigProvider
      theme={{
        token: {
          fontFamily: "inherit",
          colorPrimary: "#136360",
          borderRadius: 8,
        },
      }}
    >
      <main className="roleShell">
        <section className="roleHeader">
          <div>
            <Text className="roleEyebrow">{eyebrow}</Text>
            <Title level={1} style={{ margin: "8px 0 0", color: "#111827" }}>
              {title}
            </Title>
            <Text style={{ color: "#64748b" }}>{user.display_name}</Text>
          </div>
          <Button icon={<FiLogOut />} onClick={handleLogout}>
            Logout
          </Button>
        </section>

        <section className="roleStats">
          {stats.map((stat) => (
            <Card key={stat.label} className="roleStatCard">
              <Statistic title={stat.label} value={stat.value} />
            </Card>
          ))}
        </section>

        {children ?? (
          <section className="rolePlaceholder">
            <div className="rolePlaceholderIcon">
              {allowedRole === "manager" ? <FiShield /> : <FiUserCheck />}
            </div>
            <div>
              <Title level={3} style={{ margin: 0 }}>
                Phase 1 Auth + Roles
              </Title>
              <Text style={{ color: "#64748b" }}>
                Dashboard workflow modules will be added in later phases.
              </Text>
            </div>
          </section>
        )}
      </main>
    </ConfigProvider>
  );
}
