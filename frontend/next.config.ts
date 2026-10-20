import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  distDir: process.env.ENIGMA_NEXT_DIST_DIR ?? ".next",
};

export default nextConfig;
