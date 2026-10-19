"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback } from "react";
import { ApiError, get, post } from "./api";

export type Me = {
  kind: "user" | "api_key";
  user: { id: string; email: string; full_name: string; locale: string; mfa_enabled: boolean; is_platform_admin: boolean } | null;
  org: { id: string; slug: string; name: string; kind: "demo" | "bank" | "platform"; isolation_mode: string };
  role: string;
  role_label: { fr?: string; en?: string };
  permissions: string[];
  modules: string[];
  mfa_verified: boolean;
  memberships: { org_id: string; slug: string; name: string; kind: string; role: string }[];
};

export function useMe() {
  return useQuery<Me, ApiError>({
    queryKey: ["me"],
    queryFn: () => get<Me>("/auth/me"),
    retry: false,
    staleTime: 60_000,
  });
}

export function useCan() {
  const { data } = useMe();
  return useCallback((perm: string) => !!data?.permissions.includes(perm), [data]);
}

export function useLogout() {
  const qc = useQueryClient();
  const router = useRouter();
  return useCallback(async () => {
    try {
      await post("/auth/logout");
    } finally {
      qc.clear();
      router.replace("/login");
    }
  }, [qc, router]);
}
