'use client';
import Link from 'next/link';

export default function BotsPage() {
  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold">Bot</h1>
        <button className="px-4 py-2 bg-brand text-white rounded-lg">+ Tạo bot mới</button>
      </div>
      <p className="text-gray-400">Chọn workspace để xem danh sách bot.</p>
    </div>
  );
}
