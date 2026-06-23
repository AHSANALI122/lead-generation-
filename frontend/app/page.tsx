import ChatWidget from "@/components/ChatWidget";

// Demo/host page. The page itself stays a server component; ChatWidget is the
// "use client" island that talks to the backend. NEXT_PUBLIC_API_BASE_URL is the
// browser-visible FastAPI base URL.
export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-cream px-6 text-center">
      <div className="max-w-xl space-y-4">
        <h1 className="font-display text-4xl font-semibold text-forest">
          Honeycomb
        </h1>
        <p className="text-lg text-forest/70">
          Tell us a little about what you need and we&apos;ll point you to the right
          place. Tap the chat bubble in the corner to get started.
        </p>
      </div>

      <ChatWidget
        apiBaseUrl={process.env.NEXT_PUBLIC_API_BASE_URL!}
        suggestions={[
          "I'm exploring options",
          "I need pricing",
          "Book a demo",
        ]}
        nudgeAfter={15}
      />
    </main>
  );
}
