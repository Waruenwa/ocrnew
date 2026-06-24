"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Button, ConfigProvider, Spin, Typography } from "antd";
import { FiLogOut, FiShield, FiUserCheck } from "react-icons/fi";

import {
  type AuthUser,
  type UserRole,
  getCurrentUser,
  getRoleHomePath,
  logout,
} from "../lib/auth";
import { Box, Flex, Grid, Center, Text as ChakraText } from '@chakra-ui/react';

const { Title } = Typography;

type ProtectedRolePageProps = {
  allowedRole: UserRole;
  title: string;
  eyebrow: string;
  contentMaxW?: string;
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
  contentMaxW = "1180px",
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
          colorPrimary: "#2F5553",
          borderRadius: 8,
          colorTextHeading: "#2F5553",
        },
        components: {
          Card: {
            headerBg: "#2F5553",
          },
          Button: {
            defaultShadow: "none",
            primaryShadow: "0 4px 14px 0 rgba(47,85,83,0.39)",
            borderRadius: 8,
            controlHeight: 40,
          },
          Table: {
            headerBg: "transparent",
            headerColor: "#475569",
            headerSplitColor: "transparent",
            rowHoverBg: "rgba(241, 245, 249, 0.6)",
            borderColor: "rgba(226, 232, 240, 0.5)",
            cellPaddingBlock: 20,
            cellPaddingInline: 24,
            rowSelectedBg: "rgba(47, 85, 83, 0.04)",
            rowSelectedHoverBg: "rgba(47, 85, 83, 0.08)",
          }
        }
      }}
    >
      <Box minH="100vh" bg="#f8fafc">
        {/* Navbar */}
        <Flex 
          as="header"
          bg="rgba(255, 255, 255, 0.85)"
          w="100%"
          h="76px"
          px="40px"
          align="center"
          justify="space-between"
          borderBottom="1px solid rgba(226, 232, 240, 0.8)"
          position="sticky"
          top="0"
          zIndex="100"
          boxShadow="0 4px 30px rgba(0, 0, 0, 0.02)"
          css={{ backdropFilter: "blur(20px)" }}
        >
          {/* Left side: Logo / Role */}
          <Flex align="center" gap="20px">
            <Center w="44px" h="44px" bg="linear-gradient(135deg, #2F5553 0%, #1a3231 100%)" color="white" borderRadius="12px" fontSize="22px" boxShadow="0 4px 15px rgba(47, 85, 83, 0.3)">
              {allowedRole === "manager" ? <FiShield /> : <FiUserCheck />}
            </Center>
            <Box>
              <ChakraText fontWeight="800" color="#64748b" textTransform="uppercase" letterSpacing="0.08em" fontSize="0.7rem" mb="0">
                {eyebrow}
              </ChakraText>
              <Title level={4} style={{ margin: 0, color: "#0f172a", fontWeight: 800, letterSpacing: "-0.02em" }}>
                {title}
              </Title>
            </Box>
          </Flex>

          {/* Right side: User & Logout */}
          <Flex align="center" gap="24px">
            <Box textAlign="right" display={{ base: 'none', md: 'block' }}>
              <ChakraText color="#0f172a" fontWeight="600" fontSize="0.9rem">{user.display_name}</ChakraText>
            </Box>
            <Button 
              type="text"
              icon={<FiLogOut />} 
              onClick={handleLogout} 
              style={{ fontWeight: 600, color: '#ef4444' }}
            >
              Logout
            </Button>
          </Flex>
        </Flex>

        {/* Main Content Area */}
        <Box p="40px" maxW={contentMaxW} mx="auto">

        {/* Stats */}
        {stats.length > 0 && (
          <Grid templateColumns="repeat(3, 1fr)" gap="16px" maxW={contentMaxW} mx="auto" mb="24px">
            {stats.map((stat) => (
              <Box 
                key={stat.label} 
                bg="white" 
                p="20px 24px" 
                borderRadius="16px" 
                border="1px solid rgba(226, 232, 240, 0.8)"
                boxShadow="0 4px 30px rgba(0, 0, 0, 0.03)"
                transition="all 0.3s ease"
                _hover={{ transform: 'translateY(-2px)', boxShadow: '0 10px 40px rgba(0, 0, 0, 0.06)' }}
              >
                <ChakraText color="#64748b" fontSize="0.85rem" fontWeight="600" textTransform="uppercase" mb="4px">
                  {stat.label}
                </ChakraText>
                <ChakraText color="#0f172a" fontSize="2rem" fontWeight="800" lineHeight="1">
                  {stat.value}
                </ChakraText>
              </Box>
            ))}
          </Grid>
        )}

        {/* Children content area */}
        <Box maxW={contentMaxW} mx="auto">
          {children ?? (
            <Flex 
              align="center" 
              gap="16px" 
              p="28px" 
              border="1px dashed #cbd5e1" 
              borderRadius="8px" 
              bg="white"
            >
              <Center w="54px" h="54px" bg="#ecfdf5" color="#2F5553" fontSize="26px" borderRadius="8px">
                {allowedRole === "manager" ? <FiShield /> : <FiUserCheck />}
              </Center>
              <Box>
                <Title level={3} style={{ margin: 0 }}>
                  Phase 1 Auth + Roles
                </Title>
                <ChakraText color="#64748b">
                  Dashboard workflow modules will be added in later phases.
                </ChakraText>
              </Box>
            </Flex>
          )}
        </Box>
        </Box>
      </Box>
    </ConfigProvider>
  );
}
