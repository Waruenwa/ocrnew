import type { Metadata } from "next";
import { IBM_Plex_Mono, Noto_Sans_Thai } from "next/font/google";

import { Provider } from "../components/ui/provider";
import "./globals.css";

const displayFont = Noto_Sans_Thai({
  subsets: ["latin", "thai"],
  variable: "--font-display",
  weight: ["400", "500", "600", "700"],
});

const monoFont = IBM_Plex_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "Typhoon OCR Studio",
  description: "Document OCR workspace powered by Typhoon",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="th" suppressHydrationWarning>
      <body
        suppressHydrationWarning
        className={`${displayFont.variable} ${monoFont.variable}`}
      >
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}
