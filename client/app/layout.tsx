import type { Metadata } from "next";

import { Provider } from "../components/ui/provider";
import "./globals.css";

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
      <body suppressHydrationWarning>
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}
