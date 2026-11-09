import type { NextConfig } from "next";

const apiUrl = process.env.API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  // The browser only talks to this origin, so the session cookie stays first-party.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default config;
