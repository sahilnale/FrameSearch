"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Film, Plus, Scan, Search } from "lucide-react";
import { useWorkspace } from "./workspace";

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { videos, online } = useWorkspace();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" href="/" aria-label="FrameSearch home">
          <span className="brand-mark">
            <Scan size={25} />
          </span>
          <span>
            Frame<span className="brand-light">Search</span>
          </span>
        </Link>
        <div className="workspace-label">Workspace</div>
        <nav aria-label="Main navigation">
          <Link
            className={`nav-item ${path === "/" ? "active" : ""}`}
            href="/"
            aria-current={path === "/" ? "page" : undefined}
          >
            <Search size={18} />
            <span>Search</span>
          </Link>
          <Link
            className={`nav-item ${path === "/library" ? "active" : ""}`}
            href="/library"
            aria-current={path === "/library" ? "page" : undefined}
          >
            <Film size={18} />
            <span>Library</span>
            {videos.length > 0 && (
              <span className="nav-count">{videos.length}</span>
            )}
          </Link>
        </nav>
        <div className="sidebar-bottom">
          <div className="local-status">
            <span
              className={`status-dot ${online === true ? "online" : online === false ? "offline" : ""}`}
            />
            <span>
              {online === true
                ? "Connected"
                : online === false
                  ? "Services unavailable"
                  : "Connecting…"}
            </span>
          </div>
          <span className="local-caption">Local workspace</span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span>
            <strong>{path === "/library" ? "Library" : "Search"}</strong>
          </div>
          {path !== "/library" && (
            <Link href="/library?upload=1" className="button button-small">
              <Plus size={16} /> Upload video
            </Link>
          )}
        </header>
        <main id="main-content">{children}</main>
      </div>
    </div>
  );
}
