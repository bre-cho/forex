'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { authApi } from '@/lib/api';

export default function RegisterPage() {
  const router = useRouter();
  const [form, setForm] = useState({ email: '', password: '', full_name: '' });
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      await authApi.register(form);
      router.push('/login');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Đăng ký thất bại');
    }
  };

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center">
      <div className="bg-surface-muted p-8 rounded-xl w-full max-w-md">
        <h2 className="text-2xl font-bold text-white mb-6">Tạo tài khoản</h2>
        {error && <p className="text-red-400 mb-4">{error}</p>}
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="text" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })}
            placeholder="Họ và tên" required
            className="w-full p-3 rounded-lg bg-surface text-white border border-gray-600 focus:border-brand outline-none"
          />
          <input
            type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })}
            placeholder="Địa chỉ email" required
            className="w-full p-3 rounded-lg bg-surface text-white border border-gray-600 focus:border-brand outline-none"
          />
          <input
            type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })}
            placeholder="Mật khẩu (tối thiểu 8 ký tự)" required minLength={8}
            className="w-full p-3 rounded-lg bg-surface text-white border border-gray-600 focus:border-brand outline-none"
          />
          <button type="submit" className="w-full p-3 bg-brand text-white rounded-lg font-semibold hover:bg-brand-dark">
            Tạo tài khoản
          </button>
        </form>
        <p className="text-gray-400 mt-4 text-center">
          Đã có tài khoản? <Link href="/login" className="text-brand">Đăng nhập</Link>
        </p>
      </div>
    </div>
  );
}
