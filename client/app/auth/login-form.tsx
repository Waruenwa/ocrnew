"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, Button, Card, ConfigProvider, Form, Input, Typography } from "antd";
import { FiArrowRight, FiLock, FiUser } from "react-icons/fi";

import { getRoleHomePath, login } from "../lib/auth";

const { Text } = Typography;

type LoginValues = {
  username: string;
  password: string;
};

export function LoginForm() {
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const router = useRouter();

  async function onFinish(values: LoginValues) {
    setIsLoading(true);
    setErrorMessage(null);

    try {
      const { user } = await login(values.username, values.password);
      router.replace(getRoleHomePath(user.role));
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Login failed. Please try again.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <ConfigProvider
      theme={{
        token: {
          fontFamily: "inherit",
          colorText: "#374151",
          colorPrimary: "#136360",
          colorBgContainer: "#ffffff",
          colorBorder: "#e5e7eb",
          borderRadius: 8,
          fontSize: 14,
        },
        components: {
          Button: {
            defaultShadow: "none",
            primaryShadow: "0 4px 12px rgba(19, 99, 96, 0.2)",
            controlHeight: 44,
          },
          Input: {
            controlHeight: 48,
            activeBorderColor: "#136360",
            hoverBorderColor: "#136360",
            colorIcon: "#136360",
            colorIconHover: "#136360",
          },
        },
      }}
    >
      <main
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background:
            "radial-gradient(circle at 15% 20%, rgba(255, 240, 240, 0.6) 0%, transparent 35%), linear-gradient(135deg, #edf2f7 0%, #e2e8f0 100%)",
          padding: 24,
        }}
      >
        <Card
          style={{
            width: "100%",
            maxWidth: 460,
            borderRadius: 20,
            boxShadow:
              "0 25px 50px -12px rgba(0, 0, 0, 0.08), 0 10px 24px -10px rgba(0,0,0,0.04)",
            border: "none",
          }}
          styles={{ body: { padding: "48px 40px" } }}
        >
          <div style={{ textAlign: "center", marginBottom: 40 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                marginBottom: 16,
                fontSize: "28px",
                fontWeight: 900,
                color: "#136360",
                letterSpacing: "0.05em",
              }}
            >
              TYPH<span style={{ color: "#e11d48", margin: "0 -1px" }}>/</span>ON
            </div>

            <Text style={{ color: "#6b7280", fontSize: "0.95rem", fontWeight: 500 }}>
              Sign in to Typhoon OCR Studio
            </Text>
          </div>

          {errorMessage && (
            <Alert
              type="error"
              title={errorMessage}
              showIcon
              style={{ marginBottom: 24, borderRadius: 8 }}
            />
          )}

          <Form name="login" onFinish={onFinish} layout="vertical" requiredMark={false}>
            <Form.Item
              name="username"
              rules={[{ required: true, message: "Please enter your username" }]}
              style={{ marginBottom: 20 }}
            >
              <Input
                prefix={<FiUser style={{ color: "#136360", marginRight: 8, fontSize: 16 }} />}
                placeholder="Username"
                size="large"
              />
            </Form.Item>

            <Form.Item
              name="password"
              rules={[{ required: true, message: "Please enter your password" }]}
              style={{ marginBottom: 32 }}
            >
              <Input.Password
                prefix={<FiLock style={{ color: "#136360", marginRight: 8, fontSize: 16 }} />}
                placeholder="Password"
                size="large"
              />
            </Form.Item>

            <Form.Item style={{ marginBottom: 0, textAlign: "center" }}>
              <Button
                type="primary"
                htmlType="submit"
                loading={isLoading}
                style={{
                  background: "#136360",
                  fontWeight: 600,
                  fontSize: "1rem",
                  borderRadius: 8,
                  width: 140,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 8,
                }}
              >
                Login <FiArrowRight strokeWidth={2.5} />
              </Button>
            </Form.Item>
          </Form>
        </Card>
      </main>
    </ConfigProvider>
  );
}
