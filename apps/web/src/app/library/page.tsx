"use client";

import { ArrowUpRight, Film, RefreshCw } from "lucide-react";
import { useWorkspace } from "@/components/workspace";

export default function LibraryPage() {
  const { videos, loading, error, refresh } = useWorkspace();
  return <div className="page-content"><div className="eyebrow"><span/> THE SOURCE OF EVERY DISCOVERY</div><section className="intro"><div><h1>Your video library<span className="lime">.</span></h1><p>All your footage, ready for a closer look.</p></div><Film size={34} strokeWidth={1}/></section>
    <div className="section-heading"><h2>All videos {!loading && !error && <span className="count-pill">{videos.length}</span>}</h2><button className="text-button" onClick={() => void refresh()}><RefreshCw size={15}/> Refresh</button></div>
    {error ? <div className="notice error" role="alert"><p>{error}</p><button onClick={() => void refresh()} className="button button-secondary">Try again <ArrowUpRight size={15}/></button></div> : loading ? <div className="empty-panel"><span className="spinner"/>Loading your library…</div> : videos.length === 0 ? <div className="empty-panel"><Film size={32} strokeWidth={1}/><h2>A blank reel. Endless possibilities.</h2><p>Your uploaded videos will live here.</p></div> : <div className="video-list">{videos.map(video => <div className="video-row" key={video.id}><Film size={23}/><strong>{video.filename}</strong><span className="status-badge">{video.status}</span></div>)}</div>}
  </div>;
}
