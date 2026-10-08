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
      <header className="app-header">
        <Link className="brand" href="/" aria-label="FrameSearch home">
          <span className="brand-mark">
            <Scan size={22} />
          </span>
          <span>
            Frame<span className="brand-light">Search</span>
          </span>
        </Link>
        <nav aria-label="Main navigation">
          <Link
            className={`nav-item ${path === "/" ? "active" : ""}`}
            href="/"
            aria-current={path === "/" ? "page" : undefined}
          >
            <Search size={17} />
            <span>Search</span>
          </Link>
          <Link
            className={`nav-item ${path === "/library" ? "active" : ""}`}
            href="/library"
            aria-current={path === "/library" ? "page" : undefined}
          >
            <Film size={17} />
            <span>Library</span>
            {videos.length > 0 && (
              <span className="nav-count">{videos.length}</span>
            )}
          </Link>
        </nav>
        <div className="header-actions">
          <div className="local-status" role="status">
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
          {path !== "/library" && (
            <Link
              href="/library?upload=1"
              className="button button-small"
              aria-label="Upload video"
            >
              <Plus size={17} />
              <span>Upload video</span>
            </Link>
          )}
        </div>
      </header>
      <main id="main-content">{children}</main>
    </div>
  );
}
