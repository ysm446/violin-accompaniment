import { defineConfig } from "vite";

export default defineConfig({
  // 書き出し済みの楽譜(.mxl / .mid)を静的ファイルとして配信する
  publicDir: "../muse-score",
  server: { port: 5173, strictPort: true },
});
