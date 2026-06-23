import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";
import "./globals.css";

// Display face for headings/brand; body face for everything else. Both are variable
// fonts, so no explicit weights are needed. Exposed as CSS variables that globals.css
// maps onto the `font-display` / `font-body` Tailwind utilities.
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
});

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Honeycomb — Lead-Gen Assistant",
  description: "A friendly assistant that helps us understand how we can help you.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${fraunces.variable} ${inter.variable} h-full antialiased`}
    >
      <body className="min-h-full">{children}</body>
    </html>
  );
}
