import { defineConfig } from "vite";

export default defineConfig({
  // Electron で file:// から読むため相対パスで出力する
  base: "./",
  // 書き出し済みの楽譜(.mxl / .mid)を静的ファイルとして配信する
  publicDir: "../muse-score",
  server: { port: 5173, strictPort: true },
});
