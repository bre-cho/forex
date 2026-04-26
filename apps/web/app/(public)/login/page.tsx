'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '@/hooks/useAuth';

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      await login(email, password);
      router.push('/dashboard');
    } catch {
      setError('Email hoặc mật khẩu không đúng');
    }
  };

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center">
      <div className="bg-surface-muted p-8 rounded-xl w-full max-w-md">
        <h2 className="text-2xl font-bold text-white mb-6">Đăng nhập</h2>
        {error && <p className="text-red-400 mb-4">{error}</p>}
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            placeholder="Địa chỉ email" required
            className="w-full p-3 rounded-lg bg-surface text-white border border-gray-600 focus:border-brand outline-none"
          />
          <input
            type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            placeholder="Mật khẩu" required
            className="w-full p-3 rounded-lg bg-surface text-white border border-gray-600 focus:border-brand outline-none"
          />
          <button type="submit" className="w-full p-3 bg-brand text-white rounded-lg font-semibold hover:bg-brand-dark">
            Đăng nhập
          </button>
        </form>
        <p className="text-gray-400 mt-4 text-center">
          Chưa có tài khoản? <Link href="/register" className="text-brand">Đăng ký</Link>
        </p>
      </div>
    </div>
  );
}
