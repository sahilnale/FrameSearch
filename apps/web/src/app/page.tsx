import Link from "next/link";
import { ArrowRight, Film, Scan, Search } from "lucide-react";
import { FrameArt } from "@/components/frame-art";

export default function SearchPage() {
  return <div className="page-content">
    <div className="eyebrow"><span/> A DIFFERENT WAY TO FIND</div>
    <section className="intro"><div><h1>You remember the moment.<br/><span>We find the frame.</span></h1><p>Turn a thought into a timestamp.<br/>Search your videos by what you see in them.</p></div><div className="intro-mark"><Scan size={36} strokeWidth={1}/><span>VISUAL<br/>INTELLIGENCE</span></div></section>
    <section className="welcome-card"><div className="welcome-copy"><span className="eyebrow muted">YOUR NEXT DISCOVERY STARTS HERE</span><h2>A whole new way<br/>to see your footage.</h2><p>Add your first video, describe a visual moment,<br/>and go straight to the matching frame.</p><Link href="/library?upload=1" className="button">Upload your first video <ArrowRight size={17}/></Link><span className="file-note">MP4 · Up to 100 MB · 3 minutes</span></div><FrameArt/></section>
    <div className="section-heading"><h2>From footage to found.</h2><span>THREE SIMPLE STEPS</span></div>
    <div className="steps"><div><Film size={21}/><span className="step-number">01</span><h3>Bring your footage</h3><p>Upload a short video. We make its visual moments searchable.</p></div><div><Search size={21}/><span className="step-number">02</span><h3>Describe what you see</h3><p>A place, an object, a color. Search in your own words.</p></div><div><Scan size={21}/><span className="step-number">03</span><h3>Get to the good part</h3><p>Find the frame, click the timestamp, and play the moment.</p></div></div>
  </div>;
}
