import { Suspense } from "react";
import { SearchWorkspace } from "@/components/search-workspace";

export default function SearchPage() {
  return <Suspense fallback={<div className="page-content empty-panel"><span className="spinner"/>Loading your workspace…</div>}><SearchWorkspace/></Suspense>;
}
