import { defineConfig } from 'vite';

// GitHub Pages では https://<ユーザー名>.github.io/starrail-translator/ という住所で公開されるので、
// 公開用に書き出す（npm run build）ときだけ、住所の頭に /starrail-translator/ を付ける
export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/starrail-translator/' : '/',
}));
