"use client";

import PoolTab from "@/components/admin/PoolTab";

export default function AdminPage() {
  return (
    <div className="h-full">
      <div className="px-5 py-5">
        <PoolTab onToast={() => {}} />
      </div>
    </div>
  );
}
