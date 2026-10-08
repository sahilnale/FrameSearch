import type { Metadata } from "next";
import localFont from "next/font/local";
import { Shell } from "@/components/shell";
import { WorkspaceProvider } from "@/components/workspace";
import { UploadProvider } from "@/components/upload-provider";
import "./globals.css";

const manrope = localFont({ src: "../fonts/Manrope.ttf", variable: "--font-manrope", display: "swap", weight: "200 800" });

export const metadata: Metadata = {
  title: "FrameSearch — Find the moment",
  description: "Search what you see. Find visual moments in your own videos with natural language.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body className={manrope.variable}><a href="#main-content" className="skip-link">Skip to content</a><WorkspaceProvider><UploadProvider><Shell>{children}</Shell></UploadProvider></WorkspaceProvider></body></html>;
}
