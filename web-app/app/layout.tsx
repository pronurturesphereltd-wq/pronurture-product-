import type { Metadata } from "next";
import "./globals.css";

// The scaffold pulled Geist from next/font/google. Dropped deliberately: it
// adds a build-time network fetch for no benefit here, since the stylesheet
// uses system fonts and this pass is explicitly not a design pass.

export const metadata: Metadata = {
  title: "PSL Facility",
  description: "Staff import and rota management for PSL facilities.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
