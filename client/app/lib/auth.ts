export type UserRole = "manager" | "staff";

export type AuthUser = {
  id: string;
  username: string;
  email: string;
  display_name: string;
  role: UserRole;
  Role_ocr: UserRole;
};

type NormalizedLoginResponse = {
  user: AuthUser;
};

export const AUTH_API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_USER ?? "http://localhost:5900"
).replace(/\/$/, "");

const AUTH_TOKEN_STORAGE_KEY = "ocr_auth_token";
const AUTH_USER_STORAGE_KEY = "ocr_auth_user";

export function getRoleHomePath(role: UserRole) {
  return role === "manager" ? "/manager" : "/staff";
}

function isUserRole(value: unknown): value is UserRole {
  return value === "manager" || value === "staff";
}

function normalizeRole(value: unknown): UserRole | null {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  return isUserRole(normalized) ? normalized : null;
}

function normalizeUser(rawUser: Record<string, unknown>): AuthUser {
  const role = normalizeRole(rawUser.Role_ocr ?? rawUser.role_ocr ?? rawUser.role);
  if (!role) {
    throw new Error(
      "Login succeeded, but Role_ocr is missing or unsupported. Please contact an administrator.",
    );
  }

  const username = String(rawUser.EUserName ?? rawUser.username ?? rawUser.Username ?? "");
  const email = String(rawUser.email ?? rawUser.Email ?? rawUser.CM_Email ?? username);
  return {
    id: String(rawUser.id ?? rawUser.user_id ?? rawUser.UserID ?? rawUser.UserId ?? rawUser.ID ?? username),
    username,
    email,
    display_name: String(
      rawUser.display_name ?? rawUser.DisplayName ?? rawUser.TUserName ?? rawUser.name ?? rawUser.Name ?? username,
    ),
    role,
    Role_ocr: role,
  };
}

function normalizeAuthPayload(payload: unknown): NormalizedLoginResponse {
  if (!payload || typeof payload !== "object") {
    throw new Error("Invalid auth response.");
  }

  const authPayload = payload as Record<string, unknown>;
  let rawUser = authPayload;
  if (authPayload.user && typeof authPayload.user === "object") {
    rawUser = authPayload.user as Record<string, unknown>;
  } else if (authPayload.User && typeof authPayload.User === "object") {
    rawUser = authPayload.User as Record<string, unknown>;
  } else if (authPayload.data && typeof authPayload.data === "object") {
    rawUser = authPayload.data as Record<string, unknown>;
  }

  return {
    user: normalizeUser(rawUser),
  };
}

function getStoredToken() {
  if (typeof window === "undefined") {
    return null;
  }

  return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
}

function storeAuthPayload(payload: Record<string, unknown>, user: AuthUser) {
  if (typeof window === "undefined") {
    return;
  }

  const token = payload.token ?? payload.access_token ?? payload.jwt;
  if (typeof token === "string" && token) {
    window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  }

  window.localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify(user));
  window.localStorage.setItem("username", user.username);
}

function clearStoredAuth() {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
  window.localStorage.removeItem(AUTH_USER_STORAGE_KEY);
  window.localStorage.removeItem("username");
}

export function getAuthHeaders(): HeadersInit {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function login(username: string, password: string) {
  const response = await fetch(`${AUTH_API_BASE_URL}/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    credentials: "include",
    body: JSON.stringify({
      username,
      password,
      EUserName: username,
      UserPassword: password,
    }),
  });

  if (!response.ok) {
    throw new Error("Invalid username or password");
  }

  const payload = (await response.json()) as Record<string, unknown>;
  if (payload.status === false) {
    throw new Error("Invalid username or password");
  }
  const normalized = normalizeAuthPayload(payload);
  storeAuthPayload(payload, normalized.user);
  return normalized;
}

export async function logout() {
  try {
    await fetch(`${AUTH_API_BASE_URL}/logout`, {
      method: "POST",
      credentials: "include",
      headers: getAuthHeaders(),
    });
  } finally {
    clearStoredAuth();
  }
}

export async function getCurrentUser() {
  let response: Response;
  try {
    response = await fetch(`${AUTH_API_BASE_URL}/getprofile`, {
      cache: "no-store",
      credentials: "include",
      headers: getAuthHeaders(),
    });
  } catch {
    clearStoredAuth();
    return null;
  }

  if (response.status === 401) {
    clearStoredAuth();
    return null;
  }

  if (response.status === 404) {
    clearStoredAuth();
    return null;
  }

  if (!response.ok) {
    throw new Error("Unable to load the current user.");
  }

  const payload = (await response.json()) as Record<string, unknown>;
  const { user } = normalizeAuthPayload(payload);
  storeAuthPayload(payload, user);
  return user;
}
