import { Crosshair, Scan } from "lucide-react";

export function FrameArt() {
  return <div className="frame-art" aria-hidden="true">
    <div className="art-orbit orbit-one"/><div className="art-orbit orbit-two"/>
    <div className="art-frame rear"/><div className="art-frame middle"/>
    <div className="art-frame front"><div className="art-landscape"><div className="art-sun"/><div className="art-mountain one"/><div className="art-mountain two"/></div><span className="art-focus"><Scan size={50} strokeWidth={1}/></span><div className="art-time">A MOMENT, FOUND.</div></div>
    <Crosshair className="art-cross" size={20} strokeWidth={1}/><span className="art-label">SEE IT. SEARCH IT.</span>
  </div>;
}
