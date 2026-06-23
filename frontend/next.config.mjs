/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Produces a minimal standalone server output for Docker.
  output: "standalone",
  // Proxy API calls to the backend so the frontend can use relative paths
  // (e.g. /auth/login) and httpOnly refresh cookies work same-origin.
  async rewrites() {
    const apiBase = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";
    return [
      {
        source: "/auth/:path*",
        destination: `${apiBase}/auth/:path*`,
      },
      {
        source: "/api/:path*",
        destination: `${apiBase}/:path*`,
      },
    ];
  },
};

export default nextConfig;
