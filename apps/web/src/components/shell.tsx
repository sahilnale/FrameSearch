"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowUpRight, Film, Plus, Scan, Search } from "lucide-react";
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
            frame<span className="brand-light">search</span>
            <i />
          </span>
        </Link>
        <div className="workspace-label">
          YOUR WORKSPACE <span>01</span>
        </div>
        <nav aria-label="Main navigation">
          <Link
            className={`nav-item ${path === "/" ? "active" : ""}`}
            href="/"
            aria-current={path === "/" ? "page" : undefined}
          >
            <Search size={18} />
            <span>Search</span>
            <span className="nav-hint">↗</span>
          </Link>
          <Link
            className={`nav-item ${path === "/library" ? "active" : ""}`}
            href="/library"
            aria-current={path === "/library" ? "page" : undefined}
          >
            <Film size={18} />
            <span>Video library</span>
            {videos.length > 0 && (
              <span className="nav-count">{videos.length}</span>
            )}
          </Link>
        </nav>
        <Link href="/library?upload=1" className="sidebar-upload">
          <Plus size={18} /> Upload a video
        </Link>
        <div className="sidebar-bottom">
          <div className="workspace-note">
            <Scan size={20} />
            <p>
              Your footage.
              <br />
              <strong>A new perspective.</strong>
            </p>
            <ArrowUpRight size={16} />
          </div>
          <div className="local-status">
            <span
              className={`status-dot ${online === true ? "online" : online === false ? "offline" : ""}`}
            />
            <span>
              {online === true
                ? "All systems ready"
                : online === false
                  ? "Services unavailable"
                  : "Connecting to workspace"}
            </span>
          </div>
          <span className="local-caption">LOCAL WORKSPACE</span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span>
            <strong>
              {path === "/library" ? "Video library" : "Visual search"}
            </strong>
          </div>
          <Link href="/library?upload=1" className="button button-small">
            <Plus size={16} /> Upload video
          </Link>
        </header>
        <main id="main-content">{children}</main>
        <footer className="app-footer">
          <span>FIND THE FRAME. KEEP THE STORY.</span>
          <span>
            FrameSearch <i /> Visual search for your videos
          </span>
        </footer>
      </div>
    </div>
  );
}
