'use client';
import { useAuthStore } from '@/lib/auth';

export function useAuth() {
  return useAuthStore();
}
